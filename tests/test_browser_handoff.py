"""Offline protocol authority tests: no browser, filesystem store or worker."""

from concurrent.futures import ThreadPoolExecutor
import re
from threading import Event
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import pytest

from literature_monitor.web import browser_handoff as protocol
from literature_monitor.web.browser_handoff import BrowserHandoffRegistry, MAX_MESSAGE_BYTES, parse_handoff_message

TASK = UUID('11111111-1111-4111-8111-111111111111')
TAB = 'opaque-tab-session-A_123'


def issue(registry=None, task_id=TASK):
    registry = registry if registry is not None else BrowserHandoffRegistry()
    launch = registry.issue(task_id, port=8765)
    return registry, launch, urlsplit(launch.url).fragment


def claimed():
    registry, launch, initial = issue()
    claim = registry.claim(str(TASK), initial, TAB)
    assert claim is not None
    return registry, launch, initial, claim


def event(registry, expected_capability, **changes):
    message = {'task_id': str(TASK), 'capability': expected_capability, 'tab_binding': TAB,
               'event_type': 'tab_ready', 'payload': {'observed': True}}
    message.update(changes)
    return registry.receive_event(**message)


def test_issue_uses_256_bits_and_fragment_only_canonical_url(monkeypatch):
    lengths = []
    real_random = protocol.secrets.token_urlsafe
    def random_token(length):
        lengths.append(length)
        return real_random(length)
    monkeypatch.setattr(protocol.secrets, 'token_urlsafe', random_token)
    secrets_seen = set()
    for _ in range(20):
        registry, launch, initial = issue()
        url = urlsplit(launch.url)
        assert url.scheme == 'http' and url.hostname == 'localhost' and url.port == 8765
        assert url.username is None and url.password is None and url.query == ''
        assert url.path == f'/browser-handoff/{TASK}'
        assert initial not in url.path and re.fullmatch(r'[A-Za-z0-9_-]{43}', initial)
        assert initial not in secrets_seen
        secrets_seen.add(initial)
        assert initial not in repr(launch) + repr(registry) + repr(registry._active)
        assert registry.status(str(TASK)).claimed is False
    assert lengths == [32] * 20


def test_claim_consumes_once_rotates_and_stores_only_digests():
    registry, launch, initial, claim = claimed()
    assert claim.task_id == TASK and claim.event_capability != initial
    assert registry.status(str(TASK)).claimed
    assert registry._active.initial_digest is None
    assert isinstance(registry._active.event_digest, bytes)
    assert registry.claim(str(TASK), initial, TAB) is None
    assert registry.claim(str(TASK), initial, 'different-tab') is None
    assert registry.claim(str(TASK), claim.event_capability, TAB) is None
    public = repr(launch) + repr(claim) + repr(registry._active) + repr(registry.status(str(TASK)))
    assert initial not in public and claim.event_capability not in public


def test_wrong_claim_leaves_pending_authority_usable():
    registry, _, initial = issue()
    assert registry.claim(str(TASK), 'x' * 43, TAB) is None
    assert registry.claim(str(uuid4()), initial, TAB) is None
    assert registry.status(str(TASK)).claimed is False
    assert registry.claim(str(TASK), initial, TAB) is not None


def test_simultaneous_claims_bind_exactly_one_tab():
    registry, _, initial = issue()
    def claim(index):
        return registry.claim(str(TASK), initial, f'tab-{index}')
    with ThreadPoolExecutor(max_workers=8) as executor:
        replies = list(executor.map(claim, range(16)))
    winners = [(i, value) for i, value in enumerate(replies) if value is not None]
    assert len(winners) == 1
    index, value = winners[0]
    assert event(registry, value.event_capability, tab_binding=f'tab-{index}') is not None
    assert event(registry, value.event_capability, tab_binding=f'tab-{(index+1)%16}') is None


@pytest.mark.parametrize('field,value', [
    ('task_id', str(uuid4())), ('capability', 'x' * 43), ('tab_binding', 'unrelated-tab'),
])
def test_events_require_matching_task_capability_and_bound_tab(field, value):
    registry, _, initial, claim = claimed()
    assert event(registry, claim.event_capability, **{field: value}) is None
    assert event(registry, initial) is None
    assert event(registry, claim.event_capability) is not None


def test_event_result_is_attributed_detached_and_not_retained():
    registry, _, initial, claim = claimed()
    payload = {'nested': [1, {'observed': True}]}
    received = []
    result = registry.receive_event(str(TASK), claim.event_capability, TAB, 'tab_ready', payload,
                                    on_event=received.append)
    assert received == [result]
    assert result.task_id == TASK and result.tab_binding == TAB and result.event_type == 'tab_ready'
    payload['nested'].append('later caller edit')
    assert result.payload == {'nested': [1, {'observed': True}]}
    assert set(vars(registry)) == {'_active', '_lock'}
    assert set(vars(registry._active)) == {'task_id', 'initial_digest', 'event_digest', 'tab_binding'}
    assert initial not in repr(result) and claim.event_capability not in repr(result)


@pytest.mark.parametrize('value', [None, 12, [], {}, '', 'x' * 200, 'not-a-uuid', 'ABCDEFAB-1234-4234-8234-ABCDEFABCDEF'])
def test_invalid_task_id_does_not_consume_pending_claim(value):
    registry, _, initial = issue()
    assert registry.status(value) is None
    assert registry.claim(value, initial, TAB) is None
    assert registry.claim(str(TASK), initial, TAB) is not None


@pytest.mark.parametrize('field,value', [
    ('capability', None), ('capability', 12), ('capability', 'short'),
    ('capability', 'x' * 44), ('capability', 'é' * 43), ('capability', ['x' * 43]),
    ('tab_binding', None), ('tab_binding', 1), ('tab_binding', ''),
    ('tab_binding', 'x' * 129), ('tab_binding', 'bad/tab'), ('tab_binding', ' tab'),
    ('tab_binding', 'tab\n'), ('tab_binding', '浏览器'),
])
def test_invalid_claim_fields_do_not_destroy_pending_authority(field, value):
    registry, _, initial = issue()
    fields = {'task_id': str(TASK), 'capability': initial, 'tab_binding': TAB, field: value}
    assert registry.claim(**fields) is None
    assert registry.claim(str(TASK), initial, TAB) is not None


@pytest.mark.parametrize('field,value', [
    ('task_id', 'x' * 10000), ('capability', 'x' * 10000), ('tab_binding', 'x' * 10000),
    ('event_type', None), ('event_type', ''), ('event_type', 1), ('event_type', 'UpperCase'),
    ('event_type', 'x' * 65), ('event_type', '../download'), ('event_type', 'ready\n'),
    ('payload', None), ('payload', []), ('payload', 'secret'),
    ('payload', {'large': 'x' * 4096}), ('payload', {'bad': float('nan')}),
    ('payload', {'bad': float('inf')}), ('payload', {1: 'non-string-key'}),
    ('payload', {'bad': object()}), ('payload', {'bad': tuple()}),
    ('payload', {'nested': [[[[[[[[[[1]]]]]]]]]]}),
])
def test_malformed_events_are_rejected_without_affecting_valid_authority(field, value):
    registry, _, _, claim = claimed()
    assert event(registry, claim.event_capability, **{field: value}) is None
    assert event(registry, claim.event_capability) is not None


@pytest.mark.parametrize('phase', ['pending', 'claimed'])
def test_invalidation_kills_authority_and_late_events(phase):
    registry, _, initial = issue()
    claim = registry.claim(str(TASK), initial, TAB) if phase == 'claimed' else None
    assert not registry.invalidate(uuid4())
    assert registry.invalidate(TASK)
    assert not registry.invalidate(TASK)
    assert registry.status(str(TASK)) is None
    assert registry.claim(str(TASK), initial, TAB) is None
    calls = []
    result = registry.receive_event(str(TASK), claim.event_capability if claim else initial,
                                    TAB, 'tab_ready', {}, on_event=calls.append)
    assert result is None and calls == [] and registry._active is None


def test_invalidation_serializes_with_event_dispatch():
    registry, _, _, claim = claimed()
    entered, release, invalidating = Event(), Event(), Event()
    calls = []
    def handler(message):
        entered.set()
        assert release.wait(3)
        calls.append(message)
    def invalidate():
        invalidating.set()
        return registry.invalidate(TASK)
    with ThreadPoolExecutor(max_workers=2) as executor:
        accepted = executor.submit(registry.receive_event, str(TASK), claim.event_capability,
                                   TAB, 'tab_ready', {}, on_event=handler)
        assert entered.wait(3)
        invalidated = executor.submit(invalidate)
        assert invalidating.wait(3) and not invalidated.done()
        release.set()
        assert accepted.result(timeout=3) is not None and invalidated.result(timeout=3)
    assert len(calls) == 1
    assert registry.receive_event(str(TASK), claim.event_capability, TAB, 'tab_ready', {}, on_event=calls.append) is None
    assert len(calls) == 1


def test_single_active_no_queue_history_or_restart_recovery():
    registry, _, initial, claim = claimed()
    with pytest.raises(RuntimeError, match='already active'):
        registry.issue(uuid4(), port=8765)
    fresh = BrowserHandoffRegistry()
    assert fresh.claim(str(TASK), initial, TAB) is None
    assert event(fresh, claim.event_capability) is None
    assert registry.invalidate(TASK)
    second = registry.issue(TASK, port=8765)
    assert registry.claim(str(TASK), initial, TAB) is None
    assert event(registry, claim.event_capability) is None
    assert registry.claim(str(TASK), urlsplit(second.url).fragment, TAB) is not None


@pytest.mark.parametrize('task_id,port', [(None, 8765), (str(TASK), 8765), (TASK, True),
                                        (TASK, 0), (TASK, 65536), (TASK, '8765')])
def test_invalid_launch_parameters_create_no_authority(task_id, port):
    registry = BrowserHandoffRegistry()
    with pytest.raises(ValueError, match='Invalid handoff launch parameters'):
        registry.issue(task_id, port=port)
    assert registry._active is None


@pytest.mark.parametrize('raw', [b'{bad SECRET', b'[]', b'null', b'"SECRET"', b'\xff',
                               b'{"capability":"A","capability":"B"}', b' ' * (MAX_MESSAGE_BYTES + 1)])
def test_json_parser_fails_closed_without_reflection(raw):
    assert parse_handoff_message(raw) is None


def test_no_files_or_application_logs_for_protocol_operations(tmp_path, monkeypatch, caplog):
    monkeypatch.chdir(tmp_path)
    caplog.set_level('DEBUG')
    registry, launch, initial, claim = claimed()
    assert event(registry, claim.event_capability) is not None
    assert registry.invalidate(TASK)
    assert list(tmp_path.iterdir()) == []
    assert initial not in caplog.text and claim.event_capability not in caplog.text
    assert not [record for record in caplog.records if record.name.startswith('literature_monitor')]


def test_shared_lock_and_command_reply_are_bound_and_not_retained():
    import threading
    from literature_monitor.web.browser_handoff import validate_command
    lock=threading.RLock();registry=BrowserHandoffRegistry(lock=lock)
    assert registry.lock is lock
    launch=registry.issue(TASK,port=8765)
    from urllib.parse import urlsplit
    claim=registry.claim(str(TASK),urlsplit(launch.url).fragment,TAB)
    reply={'task_id':str(TASK),'type':'CHOOSE','choice_id':1}
    event=registry.receive_event(str(TASK),claim.event_capability,TAB,'resolver_choice_request',{'choice_id':1},on_event=lambda event:reply)
    assert event.command==reply and validate_command(TASK,reply)==reply
    reply['choice_id']=5
    assert event.command['choice_id']==1 and 'CHOOSE' not in repr(event)
    assert set(vars(registry))=={'_lock','_active'}


@pytest.mark.parametrize('reply',[
    {'task_id':str(TASK),'type':'CHOOSE','choice_id':True},
    {'task_id':str(TASK),'type':'CHOOSE','choice_id':6},
    {'task_id':str(TASK),'type':'DOWNLOAD_CURRENT','url':'https://arbitrary.example/'},
    {'task_id':str(TASK),'type':'PUBLISHER_EXHAUSTED','tab_id':3},
    {'task_id':str(TASK),'type':'DOWNLOAD_CURRENT','capability':'SENTINEL_SECRET'},
    {'task_id':str(UUID(int=99)),'type':'DOWNLOAD_CURRENT'},
    {'task_id':str(TASK),'type':['DOWNLOAD_CURRENT']},
    {'task_id':str(TASK),'type':'UNKNOWN'},
])
def test_command_schema_refuses_authority_tabs_and_arbitrary_targets(reply):
    from literature_monitor.web.browser_handoff import validate_command
    assert validate_command(TASK,reply) is None


def test_start_reply_must_equal_a7_validated_plan():
    from literature_monitor.web.browser_handoff import validate_command
    from literature_monitor.application.acquisition import AcquisitionClass, AcquisitionTask
    from literature_monitor.browser_acquisition import navigation_plan
    from literature_monitor.models import PaperVersion,VersionKind
    task=AcquisitionTask(TASK,TASK,'10.5555/test','PARENT01',
        PaperVersion(source='doi',identifier='10.5555/test',kind=VersionKind.JOURNAL_FINAL),AcquisitionClass.PUBLISHED,'instance')
    command={'task_id':str(TASK),'type':'START','plan':navigation_plan(task).message()}
    assert validate_command(TASK,command)==command
    command['plan']['direct_url']='https://arbitrary.example/'
    assert validate_command(TASK,command) is None
