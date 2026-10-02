"""Real FastAPI handoff routes, with no Chrome/extension or live services."""

import json
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import pytest

from literature_monitor.web.app import create_app
from literature_monitor.web.browser_handoff import MAX_MESSAGE_BYTES

TASK = UUID('22222222-2222-4222-8222-222222222222')
TAB = 'synthetic-browser-tab-42'


@pytest.fixture
def handoff(tmp_path, monkeypatch):
    import literature_monitor.web.app as web
    def forbidden(*args, **kwargs):
        raise AssertionError('Handoff must not launch acquisition, browser or Zotero')
    monkeypatch.setattr(web, '_build_acquisition_service', forbidden)
    monkeypatch.setattr(web.ZoteroAuthorizationClient, 'authorize', forbidden)
    monkeypatch.setattr(web.ZoteroLocalClient, 'current_instance', forbidden)
    app = create_app(tmp_path/'monitor.yaml')
    registry = app.state.browser_handoff
    launch = registry.issue(TASK, port=8765)
    s = SimpleNamespace(app=app, registry=registry, launch=launch, tmp=tmp_path,
                        initial=urlsplit(launch.url).fragment, events=[])
    app.state.browser_handoff_event_handler = s.events.append
    with TestClient(app, base_url='http://localhost:8765') as client:
        s.client = client
        yield s


def claim_body(s, **changes):
    message = {'task_id': str(TASK), 'capability': s.initial, 'tab_binding': TAB}
    message.update(changes)
    return message


def claim(s):
    response = s.client.post('/browser-handoff/claim', json=claim_body(s))
    assert response.status_code == 200
    assert_no_reflection(s, response)
    assert set(response.json()) == {'event_capability'}
    return response.json()['event_capability']


def event_body(expected_capability, **changes):
    message = {'task_id': str(TASK), 'capability': expected_capability, 'tab_binding': TAB,
               'event_type': 'tab_ready', 'payload': {'browser_observation': True}}
    message.update(changes)
    return message


def assert_no_reflection(s, response, *additional_secrets):
    public = response.text + repr(response.headers)
    assert s.initial not in public
    assert all(secret not in public for secret in additional_secrets)
    assert 'Location' not in response.headers and 'Set-Cookie' not in response.headers
    assert not any(name.lower().startswith('hx-') for name in response.headers)
    assert response.headers['Cache-Control'] == 'no-store'


def test_launch_get_transmits_no_fragment_renders_no_capability_and_remains_pending(handoff, caplog):
    s = handoff
    caplog.set_level('DEBUG')
    # httpx's test-client log records its original URL including the fragment.
    # It is outside the application; capture application/server logs instead.
    caplog.set_level('WARNING', logger='httpx')
    page = s.client.get(s.launch.url)
    # Inspect the ASGI-visible target, not httpx's client-side URL object.
    seen = []
    async def observe(scope, receive, send):
        if scope['type'] == 'http':
            seen.append((scope['path'], scope['query_string'], scope['raw_path']))
        await s.app(scope, receive, send)
    with TestClient(observe, base_url='http://localhost:8765') as client:
        direct = client.get(s.launch.url)
    assert page.status_code == direct.status_code == 200
    assert seen == [(f'/browser-handoff/{TASK}', b'', f'/browser-handoff/{TASK}'.encode())]
    assert 'Waiting for the Literature Monitor browser companion' in page.text
    assert 'Install or enable the companion in Chrome' in page.text
    assert str(TASK) in page.text and s.registry.status(str(TASK)).claimed is False
    assert '<script' not in page.text and '<form' not in page.text and '<input' not in page.text
    for unsafe in ('localStorage', 'sessionStorage', 'document.cookie', 'location.hash'):
        assert unsafe not in page.text
    assert 'default-src' in page.headers['Content-Security-Policy']
    assert s.initial not in caplog.text
    assert_no_reflection(s, page)
    assert list(s.tmp.iterdir()) == []


def test_claim_once_rotates_authority_and_event_is_delivered_without_secret(handoff, caplog):
    s = handoff; caplog.set_level('DEBUG')
    token = claim(s)
    assert len(token) == 43 and token != s.initial
    again = s.client.post('/browser-handoff/claim', json=claim_body(s))
    takeover = s.client.post('/browser-handoff/claim', json=claim_body(s, tab_binding='different-tab'))
    assert again.status_code == takeover.status_code == 403
    observed = s.client.get(urlsplit(s.launch.url)._replace(fragment="").geturl())
    assert 'companion is connected' in observed.text
    assert_no_reflection(s, observed, token)
    accepted = s.client.post('/browser-handoff/events', json=event_body(token))
    assert accepted.status_code == 204 and accepted.text == ''
    assert len(s.events) == 1
    event = s.events[0]
    assert event.task_id == TASK and event.tab_binding == TAB and event.event_type == 'tab_ready'
    assert event.payload == {'browser_observation': True}
    assert_no_reflection(s, accepted, token)
    assert_no_reflection(s, again, token)
    assert s.initial not in caplog.text + repr(event) and token not in caplog.text + repr(event)
    assert s.app.state.acquisition_coordinator.snapshot().paper_id is None
    assert list(s.tmp.iterdir()) == []


@pytest.mark.parametrize('change', [
    {'task_id': str(uuid4())}, {'capability': 'x'*43}, {'capability': 'short'},
    {'capability': None}, {'capability': []}, {'task_id': None}, {'task_id': 1},
    {'task_id': 'invalid'}, {'task_id': 'x'*100}, {'tab_binding': 42},
    {'tab_binding': ''}, {'tab_binding': 'x'*129}, {'tab_binding': 'bad\n'},
    {'unknown': 'private-input'}, {'csrf_token': 'not-companion-authority'},
])
def test_wrong_or_malformed_claim_is_generic_and_does_not_consume(handoff, change):
    s = handoff
    denied = s.client.post('/browser-handoff/claim', json=claim_body(s, **change))
    assert denied.status_code == 403 and denied.text == 'Browser handoff request rejected.'
    assert_no_reflection(s, denied)
    assert not s.registry.status(str(TASK)).claimed
    assert claim(s)


@pytest.mark.parametrize('change', [
    {'task_id': str(uuid4())}, {'tab_binding': 'different-tab'}, {'capability': 'x'*43},
    {'event_type': 'x'*65}, {'event_type': 'tab\n'}, {'event_type': 1},
    {'payload': []}, {'payload': None}, {'payload': {'large': 'x'*4096}},
    {'unknown': 'ignored-extra-field'},
])
def test_wrong_or_malformed_event_never_reaches_handler(handoff, change):
    s = handoff; token = claim(s)
    denied = s.client.post('/browser-handoff/events', json=event_body(token, **change))
    assert denied.status_code == 403
    assert_no_reflection(s, denied, token)
    assert s.events == []
    assert s.client.post('/browser-handoff/events', json=event_body(token)).status_code == 204
    assert len(s.events) == 1


def test_initial_authority_cannot_authenticate_events_or_rotated_authority_claim_other_task(handoff):
    s = handoff; token = claim(s)
    response = s.client.post('/browser-handoff/events', json=event_body(s.initial))
    assert response.status_code == 403 and not s.events
    assert_no_reflection(s, response, token)
    s.registry.invalidate(TASK)
    next_task = uuid4(); next_launch = s.registry.issue(next_task, port=8765)
    response = s.client.post('/browser-handoff/claim', json=claim_body(s, task_id=str(next_task), capability=token))
    assert response.status_code == 403
    assert_no_reflection(s, response, token, urlsplit(next_launch.url).fragment)
    assert not s.registry.status(str(next_task)).claimed


@pytest.mark.parametrize('phase', ['pending', 'claimed'])
def test_invalidation_and_restart_reject_old_claims_and_late_events(handoff, phase):
    s = handoff
    token = claim(s) if phase == 'claimed' else 'x'*43
    fresh = create_app(s.tmp/'monitor.yaml')
    with TestClient(fresh, base_url='http://localhost') as client:
        assert client.post('/browser-handoff/claim', json=claim_body(s)).status_code == 403
        assert client.post('/browser-handoff/events', json=event_body(token)).status_code == 403
        assert client.get(s.launch.url).status_code == 404
    assert s.registry.invalidate(TASK)
    assert s.client.post('/browser-handoff/claim', json=claim_body(s)).status_code == 403
    denied = s.client.post('/browser-handoff/events', json=event_body(token))
    assert denied.status_code == 403 and s.events == []
    assert s.client.get(s.launch.url).status_code == 404
    assert_no_reflection(s, denied, token)
    assert list(s.tmp.iterdir()) == []


@pytest.mark.parametrize('route', ['/browser-handoff/claim', '/browser-handoff/events'])
@pytest.mark.parametrize('mode', ['invalid_json', 'non_object', 'duplicate_fields', 'oversized', 'chunked_oversized', 'non_json', 'query'])
def test_malformed_request_body_and_url_channel_fail_closed(handoff, route, mode):
    s = handoff
    raw = json.dumps(claim_body(s)).encode()
    kwargs = {'headers': {'Content-Type': 'application/json'}}
    if mode == 'invalid_json':
        raw = b'{"capability":"' + s.initial.encode() + b'"'
    elif mode == 'non_object':
        raw = json.dumps([s.initial]).encode()
    elif mode == 'duplicate_fields':
        raw = b'{"capability":"' + s.initial.encode() + b'","capability":"duplicate"}'
    elif mode == 'oversized':
        raw += b' ' * MAX_MESSAGE_BYTES
    elif mode == 'chunked_oversized':
        raw = iter([b' ' * 4096] * 3)
    elif mode == 'non_json':
        kwargs['headers'] = {'Content-Type': 'text/plain'}
    elif mode == 'query':
        route += '?unsupported=1'
    response = s.client.post(route, content=raw, **kwargs)
    assert response.status_code == 403
    assert_no_reflection(s, response)
    assert not s.registry.status(str(TASK)).claimed and not s.events
    assert claim(s)


def test_trusted_host_csrf_and_capability_authority_remain_distinct(handoff):
    s = handoff
    result = s.client.post('/browser-handoff/claim', json=claim_body(s), headers={'Host': 'untrusted.example'})
    assert result.status_code == 400 and not s.registry.status(str(TASK)).claimed
    user_csrf = s.app.state.csrf_token
    result = s.client.post('/browser-handoff/claim', json=claim_body(s, capability=user_csrf))
    assert result.status_code == 403
    assert_no_reflection(s, result, user_csrf)
    # No user-form CSRF is required to exercise a valid browser capability.
    token = claim(s)
    result = s.client.post('/settings/zotero/authorize', data={'csrf_token': token})
    assert result.status_code == 403
    assert s.client.get('/settings').status_code == 200
    assert s.client.get('/browser-handoff/claim').status_code == 404
    assert s.client.get('/browser-handoff/events').status_code == 404


def test_callback_failure_is_generic_and_never_logs_or_reflects_capabilities(handoff, caplog):
    s = handoff; token = claim(s); caplog.set_level('DEBUG')
    def fail(event):
        raise RuntimeError(token + s.initial)
    s.app.state.browser_handoff_event_handler = fail
    failed = s.client.post('/browser-handoff/events', json=event_body(token))
    assert failed.status_code == 500
    assert_no_reflection(s, failed, token)
    assert s.initial not in caplog.text and token not in caplog.text


def test_unknown_and_malformed_get_does_not_reflect_request_input(handoff):
    s = handoff
    for path in ('/browser-handoff/not-a-task', f'/browser-handoff/{uuid4()}', '/browser-handoff/'+'x'*128):
        response = s.client.get(path)
        assert response.status_code == 404 and response.text == 'Browser handoff is unavailable.'
        assert_no_reflection(s, response)
    assert s.client.get(f'/browser-handoff/{TASK}?unsupported=1').status_code == 404
    assert not s.registry.status(str(TASK)).claimed


@pytest.mark.parametrize('terminal',['cancel','command_failure'])
def test_nonsecret_liveness_tracks_actual_coordinator_terminalization(handoff,terminal):
    import threading
    from test_acquisition_coordinator import ControlledService
    from literature_monitor.web.acquisition_coordinator import AcquisitionCoordinator, AcquisitionActionOutcome

    s=handoff;s.registry.invalidate(TASK)  # Replace this fixture's pending standalone authority.
    service=ControlledService();service.prepare_release.set()
    prepared=[];opened=threading.Event();prepare=service.prepare
    def tracked(*args,**kwargs):
        prepared.append(threading.current_thread());return prepare(*args,**kwargs)
    service.prepare=tracked
    coordinator=AcquisitionCoordinator(service,registry=s.registry,launcher=lambda launch:opened.set())
    s.app.state.acquisition_coordinator=coordinator
    s.app.state.browser_handoff_event_handler=coordinator.handle_event
    coordinator.start(TASK)
    assert opened.wait(5);prepared[0].join(5);assert not prepared[0].is_alive()
    task=service.prepared.task;launch=coordinator._active.launch
    capability=s.client.post('/browser-handoff/claim',json={
        'task_id':str(task.task_id),'capability':urlsplit(launch.url).fragment,'tab_binding':'tab-42'}).json()['event_capability']
    def event(kind,payload,**changes):
        message=dict(task_id=str(task.task_id),capability=capability,tab_binding='tab-42',event_type=kind,payload=payload)
        message.update(changes);return s.client.post('/browser-handoff/events',json=message)
    command=event('tab_ready',{});assert command.status_code==200 and command.json()['command']['type']=='START'
    path=f'/browser-handoff/{task.task_id}'
    active=s.client.get(path);assert active.status_code==200
    assert 'capability' not in path and capability not in active.text+repr(active.headers)
    assert active.headers['Cache-Control']=='no-store' and 'Set-Cookie' not in active.headers
    if terminal=='cancel':
        assert coordinator.cancel(TASK,coordinator.snapshot().attempt_id) is AcquisitionActionOutcome.ACCEPTED
    else:
        before=coordinator.snapshot()
        rejected=event('browser_path_failure',{'reason':'navigation_failed'},tab_binding='tab-99')
        assert rejected.status_code==403 and coordinator.snapshot()==before
        private='PRIVATE_EXCEPTION https://signed.example/?secret=PRIVATE'
        ignored=event('browser_path_failure',{'reason':private})
        assert ignored.status_code==204 and private not in ignored.text and coordinator.snapshot()==before
        failed=event('browser_path_failure',{'reason':'navigation_failed'})
        assert failed.status_code==204 and failed.text==''
        assert private not in repr(coordinator.snapshot()) and capability not in repr(failed.headers)
    revoked=s.client.get(path)
    assert revoked.status_code==404 and revoked.headers['Cache-Control']=='no-store'
    assert revoked.text=='Browser handoff is unavailable.' and capability not in revoked.text+repr(revoked.headers)
    assert event('tab_ready',{}).status_code==403 and service.content_posts==0
    assert coordinator._active is None and s.registry.status(str(task.task_id)) is None
