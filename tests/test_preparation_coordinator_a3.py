"""A3 isolated CARSI Preparation, native-save exclusion and bridge identity checks."""

from __future__ import annotations

import json
import select
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, asdict
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import literature_monitor.web.preparation_coordinator as preparation_module
from literature_monitor.application.batch_import import BatchImportService, BatchPhase
from literature_monitor.application.settings import load_settings
from literature_monitor.config import AccessService, ConfigurationError, render_settings_list_text
from literature_monitor.web.app import create_app
from literature_monitor.web.capture_coordinator import (
    CaptureCoordinator, CaptureOutcome, CaptureStartOutcome, PdfOutcome, SaveInvocation,
)
from literature_monitor.web.preparation_coordinator import (
    EXECUTOR_CAPABILITY, PreparationCoordinator, PreparationOutcome, PreparationPhase,
    VisitEvidence, read_saved_preparation_services,
)
from test_access_mapping_a4 import stamp
from preparation_helpers import prepared
from test_batch_import import paper, command as capture_command, invocation as capture_invocation, finished
from capture_helpers import reservation


INSTANCE = "preparation-browser-instance-one"
NEXT_INSTANCE = "preparation-browser-instance-two"
CAPABILITY = {"capability": EXECUTOR_CAPABILITY, "version": "0.6.4"}


def enabled(config):
    capture = CaptureCoordinator()
    prep = PreparationCoordinator(capture, execution_enabled=True)
    assert prep.heartbeat(INSTANCE, **CAPABILITY)
    return capture, prep


def identity(command, instance=INSTANCE):
    return dict(
        instance_id=instance, task_id=command.task_id, generation=command.generation,
        request_id=command.request_id, service_id=command.service_id,
    )


@pytest.mark.parametrize("outcome", [PreparationOutcome.OPENED, PreparationOutcome.NEEDS_ATTENTION])
def test_service_result_advances_immediately_and_retries_are_idempotent(tmp_path, outcome):
    config, _, first, second = prepared(tmp_path)
    capture, prep = enabled(config)
    started = prep.start(config)
    command = prep.claim(INSTANCE)
    proof = None
    reason = "resource_not_found"
    if outcome is PreparationOutcome.OPENED:
        nonce = prep.authorize_visit(**identity(command), source_tab_id=42)
        proof = VisitEvidence(nonce, True, 42, 43, 43, True)
        reason = "target_opened"
    assert prep.result(**identity(command), outcome=outcome, reason=reason, evidence=proof)
    assert prep.result(**identity(command), outcome=outcome, reason=reason, evidence=proof)
    assert not prep.result(**identity(command), outcome=PreparationOutcome.FAILED,
                           reason="connector_error")
    state = prep.snapshot()
    assert state.phase is PreparationPhase.RUNNING
    assert state.results == (preparation_module.PreparationResult(first.id, outcome, reason),)
    assert state.current_service_id == second.id
    assert not prep.authorize(**identity(command))
    next_command = prep.claim(INSTANCE)
    assert next_command.service_id == second.id and next_command.task_id == started.task_id
    assert not prep.result(**identity(command), outcome=outcome, reason=reason + "x",
                           evidence=proof)
    assert capture.reserve_browser_task("import") is None
    assert prep.result(**identity(next_command), outcome=PreparationOutcome.FAILED,
                       reason="navigation_failed")
    assert prep.snapshot().phase is PreparationPhase.COMPLETED
    assert len(prep.snapshot().results) == 2
    import_owner = capture.reserve_browser_task("import")
    assert import_owner is not None
    assert prep.result(**identity(next_command), outcome=PreparationOutcome.FAILED,
                       reason="navigation_failed"), "duplicate must not release another owner"
    assert capture.reserve_browser_task("preparation") is None
    assert prep.result(**identity(command), outcome=outcome, reason=reason, evidence=proof)
    assert len(prep.snapshot().results) == 2, "delayed prior-Service retry must not advance twice"
    assert capture.release_browser_task("import", import_owner)


def test_opened_requires_exact_nonce_detail_source_and_attributed_target(tmp_path):
    config, _, _, _ = prepared(tmp_path)
    _, prep = enabled(config)
    prep.start(config)
    command = prep.claim(INSTANCE)
    assert not prep.result(**identity(command), outcome=PreparationOutcome.OPENED,
                           reason="target_opened")
    nonce = prep.authorize_visit(**identity(command), source_tab_id=42)
    for proof in (
        VisitEvidence("invalid-nonce", True, 42, 43, 43, True),
        VisitEvidence(nonce, False, 42, 43, 43, True),
        VisitEvidence(nonce, True, 42, 43, 43, False),
        VisitEvidence(nonce, True, 99, 43, 43, True),
        VisitEvidence(nonce, True, 42, 43, 44, True),
    ):
        assert not prep.result(**identity(command), outcome=PreparationOutcome.OPENED,
                               reason="target_opened", evidence=proof)
    assert prep.authorize(**identity(command))
    assert prep.result(**identity(command), outcome=PreparationOutcome.OPENED,
                       reason="target_opened", evidence=VisitEvidence(nonce, True, 42, 43, 43, True))


def test_missing_connector_result_times_out_only_current_service_and_rejects_old_authority(tmp_path, monkeypatch):
    config, _, _, second = prepared(tmp_path)
    now = [preparation_module.time.monotonic()]
    monkeypatch.setattr(preparation_module.time, "monotonic", lambda: now[0])
    capture, prep = enabled(config)
    started = prep.start(config)
    claimed = prep.claim(INSTANCE)
    monkeypatch.setattr(preparation_module, "CONNECTOR_STALE_SECONDS", 1200.0)
    now[0] += preparation_module.SERVICE_WAIT_SECONDS + 1
    state = prep.snapshot()
    assert state.phase is PreparationPhase.RUNNING
    assert state.results[0].reason == "timeout"
    assert state.current_service_id == second.id
    assert not prep.authorize(**identity(claimed))
    assert not prep.result(**identity(claimed), outcome=PreparationOutcome.FAILED, reason="timeout")
    assert prep.authorize_visit(**identity(claimed), source_tab_id=42) is None
    assert capture.reserve_browser_task("import") is None
    now[0] += preparation_module.SERVICE_WAIT_SECONDS + 1
    assert prep.heartbeat(INSTANCE, **CAPABILITY)
    assert len(prep.snapshot().results) == 1, "waiting for the next claim consumes no deadline"
    fresh = prep.claim(INSTANCE)
    assert fresh is not None, prep.snapshot()
    assert fresh.service_id == second.id and fresh.task_id == started.task_id
    assert prep.authorize(**identity(fresh))
    assert not prep.authorize(**identity(claimed))
    now[0] += preparation_module.SERVICE_WAIT_SECONDS + 1
    completed = prep.snapshot()
    assert completed.phase is PreparationPhase.COMPLETED
    assert len(completed.results) == 2
    assert all(result.reason == "timeout" for result in completed.results)
    assert capture.reserve_browser_task("import") is not None


def test_manual_login_wait_is_independent_of_short_service_deadline(tmp_path, monkeypatch):
    config, _, _, _ = prepared(tmp_path)
    now = [preparation_module.time.monotonic()]
    monkeypatch.setattr(preparation_module.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(preparation_module, "CONNECTOR_STALE_SECONDS", 1200.0)
    _, prep = enabled(config)
    prep.start(config)
    command = prep.claim(INSTANCE)
    now[0] += 30
    assert prep.report_waiting(**identity(command), waiting=True)
    now[0] += preparation_module.SERVICE_WAIT_SECONDS + 1
    assert prep.snapshot().waiting_for_user
    assert prep.snapshot().phase is PreparationPhase.RUNNING
    assert prep.authorize(**identity(command))
    assert prep.report_waiting(**identity(command), waiting=False)
    now[0] += preparation_module.SERVICE_WAIT_SECONDS + 1
    assert prep.snapshot().phase is PreparationPhase.RUNNING
    assert prep.snapshot().results[0].reason == "timeout"
    next_command = prep.claim(INSTANCE)
    assert prep.report_waiting(**identity(next_command), waiting=True)
    now[0] += preparation_module.LOGIN_WAIT_SECONDS + 1
    assert prep.snapshot().phase is PreparationPhase.BLOCKED
    assert prep.snapshot().results[1].reason == "timeout"


def test_frozen_kept_related_services_with_blank_urls_are_immutable(tmp_path):
    config, path, first, second_service = prepared(tmp_path)
    captured_bytes = stamp(path), stamp(config)
    capture, prep = enabled(config)
    ordered = read_saved_preparation_services(config)
    assert tuple(s.id for s in ordered) == (first.id, second_service.id)
    assert ordered[1].access_url is None
    snap = prep.start(config)
    assert snap.phase is PreparationPhase.RUNNING
    assert snap.services == ordered
    assert snap.current_service_id == first.id
    with pytest.raises(FrozenInstanceError):
        snap.current_service_id = second_service.id
    with pytest.raises(ValueError):
        snap.services[0].name = "tamper"
    before = prep.claim(INSTANCE)
    assert before.service_id == first.id and before.name == first.name
    assert before.last_service is False
    # An external Settings edit does not change the active queue.
    source = path.read_text()
    fields = load_settings(config).draft
    path.write_text(render_settings_list_text(
        source, fields.journals, fields.publishers,
        access_services=tuple(s.model_copy(update={"name": "Changed " + s.name}) for s in fields.access_services),
        path=path), encoding="utf-8")
    assert prep.snapshot().services == ordered
    assert prep.authorize(**identity(before))
    assert prep.result(**identity(before), outcome=PreparationOutcome.NEEDS_ATTENTION,
                       reason="resource_not_found")
    second = prep.claim(INSTANCE)
    assert second.service_id == second_service.id and second.name == second_service.name
    assert second.last_service is True
    assert second.access_url is None
    assert prep.result(**identity(second), outcome=PreparationOutcome.FAILED,
                       reason="navigation_failed")
    assert prep.snapshot().phase is PreparationPhase.COMPLETED
    assert [result.outcome for result in prep.snapshot().results] == [
        PreparationOutcome.NEEDS_ATTENTION, PreparationOutcome.FAILED,
    ]
    assert stamp(config) == captured_bytes[1]
    assert all(path.suffix == ".md" for path in (path,))


def test_missing_legacy_unsafe_corrupt_or_duplicate_services_fail_without_browser_owner(tmp_path):
    config, path, first, second_service = prepared(tmp_path)
    capture, prep = enabled(config)
    path.unlink()
    with pytest.raises(ConfigurationError):
        prep.start(config)
    path.write_text("## Journals\n", encoding="utf-8")
    with pytest.raises(ConfigurationError):
        prep.start(config)
    path.write_bytes(b"\xff\xfe")
    with pytest.raises(ConfigurationError):
        prep.start(config)
    path.unlink()
    path.symlink_to(tmp_path / "other.md")
    with pytest.raises(ConfigurationError):
        prep.start(config)
    assert capture.reserve_browser_task("import") is not None


def test_concurrent_preparation_start_only_one_owns_browser(tmp_path):
    config, _, _, _ = prepared(tmp_path)
    capture, prep = enabled(config)
    with ThreadPoolExecutor(max_workers=6) as executor:
        states = list(executor.map(lambda _: prep.start(config), range(6)))
    assert sum(state.phase is PreparationPhase.RUNNING for state in states) == 1
    assert sum(state.phase is PreparationPhase.BLOCKED for state in states) == 5
    assert len({state.task_id for state in states}) == 1
    assert capture.reserve_browser_task("import") is None
    assert prep.cancel(states[0].task_id, states[0].generation)
    assert prep.snapshot().phase is PreparationPhase.STOPPED
    assert not prep.authorize(instance_id=INSTANCE, task_id=states[0].task_id,
                              generation=states[0].generation,
                              request_id="fabricated", service_id=states[0].services[0].id)
    restarted = prep.start(config)
    assert restarted.generation > states[0].generation
    assert restarted.task_id != states[0].task_id
    assert not prep.cancel(states[0].task_id, states[0].generation)


def test_cancel_revokes_old_task_and_recovers_without_settled(tmp_path, monkeypatch):
    config, _, first, _ = prepared(tmp_path)
    now = [preparation_module.time.monotonic()]
    monkeypatch.setattr(preparation_module.time, "monotonic", lambda: now[0])
    capture, prep = enabled(config)
    started = prep.start(config)
    command = prep.claim(INSTANCE)
    assert prep.authorize(**identity(command))
    assert not prep.cancel(started.task_id, started.generation + 1)
    assert prep.cancel(started.task_id, started.generation)
    assert prep.snapshot().phase is PreparationPhase.STOPPED
    assert not prep.authorize(**identity(command))
    assert not prep.result(**identity(command), outcome=PreparationOutcome.FAILED, reason="unknown")
    assert prep.claim(INSTANCE) is None
    assert capture.reserve_browser_task("import") is None
    now[0] += preparation_module.RELEASE_GRACE_SECONDS + 1
    restarted = prep.start(config)
    assert restarted.phase is PreparationPhase.RUNNING and restarted.task_id != started.task_id
    fresh = prep.claim(INSTANCE)
    assert fresh.service_id == first.id
    assert not prep.authorize(**identity(command))
    assert not prep.result(**identity(command), outcome=PreparationOutcome.FAILED, reason="unknown")
    assert prep.authorize(**identity(fresh))


def test_bridge_replay_and_forgery_never_authorize_or_advance_a_new_service(tmp_path):
    config, _, _, second = prepared(tmp_path)
    _, prep = enabled(config)
    prep.start(config)
    command = prep.claim(INSTANCE)
    assert not prep.claim(INSTANCE)
    assert not prep.authorize(**{**identity(command), "request_id": "forged"})
    assert not prep.authorize(**{**identity(command), "service_id": "svc_" + "0" * 32})
    assert not prep.result(**identity(command), outcome=PreparationOutcome.OPENED, reason="unknown")
    assert not prep.result(**identity(command), outcome=PreparationOutcome.FAILED,
                           reason="https://sensitive")
    assert prep.result(**identity(command), outcome=PreparationOutcome.FAILED,
                       reason="navigation_failed")
    assert prep.result(**identity(command), outcome=PreparationOutcome.FAILED,
                       reason="navigation_failed")
    assert not prep.result(**identity(command), outcome=PreparationOutcome.FAILED, reason="timeout")
    next_command = prep.claim(INSTANCE)
    assert next_command.service_id == second.id
    assert not prep.authorize(**identity(command))
    assert prep.authorize(**identity(next_command))


def test_cancel_and_result_race_cannot_revive_old_authority(tmp_path):
    from threading import Barrier
    config, _, _, _ = prepared(tmp_path)
    capture, prep = enabled(config)
    started = prep.start(config)
    command = prep.claim(INSTANCE)
    barrier = Barrier(2)

    def send_result():
        barrier.wait()
        return prep.result(**identity(command), outcome=PreparationOutcome.FAILED,
                           reason="navigation_failed")

    def cancel():
        barrier.wait()
        return prep.cancel(started.task_id, started.generation)

    with ThreadPoolExecutor(max_workers=2) as pool:
        accepted_result = pool.submit(send_result)
        accepted_cancel = pool.submit(cancel)
        delivered, canceled = accepted_result.result(), accepted_cancel.result()
    if delivered:
        assert len(prep.snapshot().results) == 1
        assert not canceled or prep.snapshot().phase is PreparationPhase.STOPPED
    if canceled:
        assert not prep.authorize(**identity(command))
    assert prep.snapshot().phase in (PreparationPhase.RUNNING, PreparationPhase.STOPPED)
    assert len(prep.snapshot().results) == 1
    assert prep.snapshot().results[0].reason == ("navigation_failed" if delivered else "canceled")
    assert capture.reserve_browser_task("import") is None


def test_duplicate_saved_service_id_is_rejected_without_partial_queue(tmp_path):
    config, path, first, spare = prepared(tmp_path)
    source = path.read_text()
    path.write_text(source.replace(spare.id, first.id), encoding="utf-8")
    capture, prep = enabled(config)
    with pytest.raises(ConfigurationError):
        prep.start(config)
    owner = capture.reserve_browser_task("import")
    assert owner is not None
    assert capture.release_browser_task("import", owner)


def test_connector_reconnect_revokes_old_generation_and_recovers(tmp_path, monkeypatch):
    config, _, first, _ = prepared(tmp_path)
    now = [preparation_module.time.monotonic()]
    monkeypatch.setattr(preparation_module.time, "monotonic", lambda: now[0])
    capture, prep = enabled(config)
    previous = prep.start(config)
    command = prep.claim(INSTANCE)
    assert prep.heartbeat(NEXT_INSTANCE, **CAPABILITY)
    state = prep.snapshot()
    assert state.phase is PreparationPhase.BLOCKED
    assert state.results[0].reason == "connector_disconnected"
    assert not prep.authorize(**identity(command))
    assert capture.reserve_browser_task("import") is None
    now[0] += preparation_module.RELEASE_GRACE_SECONDS + 1
    restarted = prep.start(config)
    assert restarted.phase is PreparationPhase.RUNNING and restarted.task_id != previous.task_id
    fresh = prep.claim(NEXT_INSTANCE)
    assert fresh.service_id == first.id
    assert not prep.result(**identity(command), outcome=PreparationOutcome.FAILED,
                           reason="connector_error")
    assert prep.authorize(**identity(fresh, NEXT_INSTANCE))


def test_unclaimed_service_waits_without_deadline_and_stale_connector_still_releases(tmp_path, monkeypatch):
    config, _, first, _ = prepared(tmp_path)
    now = [preparation_module.time.monotonic()]
    monkeypatch.setattr(preparation_module.time, "monotonic", lambda: now[0])
    capture, prep = enabled(config)
    started = prep.start(config)
    monkeypatch.setattr(preparation_module, "SERVICE_WAIT_SECONDS", -1)
    waiting = prep.snapshot()
    assert waiting.phase is PreparationPhase.RUNNING and not waiting.results
    assert capture.reserve_browser_task("import") is None
    now[0] += preparation_module.CONNECTOR_STALE_SECONDS + 1
    disconnected = prep.snapshot()
    assert disconnected.phase is PreparationPhase.BLOCKED
    assert disconnected.results[0].service_id == first.id
    assert disconnected.results[0].reason == "connector_disconnected"
    assert capture.reserve_browser_task("import") is not None
    assert not prep.authorize(instance_id=INSTANCE, task_id=started.task_id, generation=started.generation,
                              request_id="old-request", service_id=first.id)


def test_shared_import_reservation_bidirectional_and_zotero_offline(tmp_path):
    config, path, first, spare = prepared(tmp_path)
    capture, prep = enabled(config)
    # Prep does not need a Zotero heartbeat.
    state = prep.start(config)
    assert state.phase is PreparationPhase.RUNNING
    paper(tmp_path, "one")
    batch = BatchImportService(capture)
    assert batch.start(tmp_path).phase is BatchPhase.BLOCKED
    assert prep.cancel(state.task_id, state.generation)
    # Zotero Import retains its original Connector+Zotero requirement.
    capture.heartbeat(connector_version="1.0", zotero_reachable=True)
    running = batch.start(tmp_path)
    assert running.phase is BatchPhase.RUNNING
    assert prep.start(config).phase is PreparationPhase.BLOCKED
    cmd = capture_command(batch)
    invocation = capture_invocation(cmd)
    assert capture.submit_connector_result(
        request_id=invocation.request_id, invocation_id=invocation.invocation_id,
        doi_url=invocation.doi_url, tab_id=invocation.tab_id, session_id=invocation.session_id,
        outcome=CaptureOutcome.FAILED, pdf_outcome=PdfOutcome.UNVERIFIED,
    )
    finished(batch)
    # A delivered Import result is not yet browser idle.
    assert prep.start(config).phase is PreparationPhase.BLOCKED
    assert not capture.report_import_browser_idle(cmd.request_id, "forged", cmd.doi_url)
    assert capture.report_import_browser_idle(cmd.request_id, cmd.invocation_id, cmd.doi_url)
    assert prep.start(config).phase is PreparationPhase.RUNNING


def test_competing_import_and_preparation_starts_are_atomic(tmp_path):
    from threading import Barrier
    config, _, _, _ = prepared(tmp_path)
    paper(tmp_path, "concurrent")
    capture, prep = enabled(config)
    capture.heartbeat(connector_version="1.0", zotero_reachable=True)
    batch = BatchImportService(capture)
    barrier = Barrier(2)

    def import_action():
        barrier.wait()
        return batch.start(tmp_path)

    def prepare_action():
        barrier.wait()
        return prep.start(config)

    with ThreadPoolExecutor(max_workers=2) as executor:
        importing = executor.submit(import_action)
        preparing = executor.submit(prepare_action)
        import_state, prepare_state = importing.result(), preparing.result()
    assert (import_state.phase is BatchPhase.RUNNING) != (prepare_state.phase is PreparationPhase.RUNNING)
    if prepare_state.phase is PreparationPhase.RUNNING:
        assert prep.cancel(prepare_state.task_id, prepare_state.generation)
        assert batch.start(tmp_path).phase is BatchPhase.RUNNING
    command = capture_command(batch)
    invoked = capture_invocation(command)
    assert capture.submit_connector_result(
        request_id=invoked.request_id, invocation_id=invoked.invocation_id,
        doi_url=invoked.doi_url, tab_id=invoked.tab_id, session_id=invoked.session_id,
        outcome=CaptureOutcome.FAILED, pdf_outcome=PdfOutcome.UNVERIFIED,
    )
    finished(batch)
    assert prep.start(config).phase is PreparationPhase.BLOCKED
    assert capture.report_import_browser_idle(command.request_id, command.invocation_id, command.doi_url)
    assert prep.start(config).phase is PreparationPhase.RUNNING


def test_import_browser_idle_requires_terminal_and_matching_identity(tmp_path):
    config, _, _, _ = prepared(tmp_path)
    capture, prep = enabled(config)
    capture.heartbeat(connector_version="1.0", zotero_reachable=True)
    assert capture.start_capture(reservation(tmp_path)).outcome is CaptureStartOutcome.STARTED
    command = capture.claim_command()
    assert not capture.report_import_browser_idle(command.request_id, command.invocation_id, command.doi_url)
    assert prep.start(config).phase is PreparationPhase.BLOCKED
    assert capture.submit_connector_result(
        request_id=command.request_id, invocation_id=command.invocation_id,
        doi_url=command.doi_url, tab_id=5, session_id="session",
        outcome=CaptureOutcome.FAILED, pdf_outcome=PdfOutcome.UNVERIFIED,
    )
    completed = capture.consume_completion()
    assert capture.claim_completion(completed)
    capture.finish_resolution(completed)
    assert prep.start(config).phase is PreparationPhase.BLOCKED
    assert not capture.report_import_browser_idle(command.request_id, command.invocation_id,
                                                  "https://doi.org/unrelated")
    assert capture.report_import_browser_idle(command.request_id, command.invocation_id, command.doi_url)
    assert capture.report_import_browser_idle(command.request_id, command.invocation_id, command.doi_url)
    assert prep.start(config).phase is PreparationPhase.RUNNING


def test_process_restart_rejects_old_preparation_callbacks(tmp_path):
    config, _, _, _ = prepared(tmp_path)
    _, previous = enabled(config)
    previous.start(config)
    command = previous.claim(INSTANCE)
    restarted = PreparationCoordinator(CaptureCoordinator(), execution_enabled=True)
    assert restarted.snapshot().phase is PreparationPhase.IDLE
    assert restarted.heartbeat(INSTANCE, **CAPABILITY)
    assert not restarted.authorize(**identity(command))
    assert not restarted.result(**identity(command), outcome=PreparationOutcome.FAILED, reason="unknown")
    current = restarted.start(config)
    assert current.generation == 1 and current.task_id != command.task_id
    assert not restarted.authorize(**identity(command))


def test_outstanding_native_save_grant_blocks_preparation_until_original_settled(tmp_path):
    config, _, _, _ = prepared(tmp_path)
    capture, prep = enabled(config)
    capture.heartbeat(connector_version="1.0", zotero_reachable=True)
    result = capture.start_capture(reservation(tmp_path))
    assert result.outcome is CaptureStartOutcome.STARTED
    command = capture.claim_command()
    invocation = SaveInvocation(command.request_id, command.invocation_id, command.doi_url, 100, "session")
    assert capture.authorize_parent_dispatch(invocation)
    assert prep.start(config).phase is PreparationPhase.BLOCKED
    assert capture.submit_connector_result(
        request_id=invocation.request_id, invocation_id=invocation.invocation_id,
        doi_url=invocation.doi_url, tab_id=invocation.tab_id, session_id=invocation.session_id,
        outcome=CaptureOutcome.UNCONFIRMED, pdf_outcome=PdfOutcome.UNVERIFIED,
    )
    completion = capture.consume_completion()
    assert capture.claim_completion(completion)
    capture.finish_resolution(completion)
    assert prep.start(config).phase is PreparationPhase.BLOCKED
    assert capture.report_native_save_settled(invocation)
    assert prep.start(config).phase is PreparationPhase.BLOCKED
    assert capture.report_import_browser_idle(command.request_id, command.invocation_id, command.doi_url)
    assert prep.start(config).phase is PreparationPhase.RUNNING


@pytest.mark.parametrize('supported', [False, True])
def test_attribute_consent_is_frozen_and_old_connector_remains_manual(tmp_path, supported):
    import yaml
    config, _, _, _ = prepared(tmp_path)
    raw = yaml.safe_load(config.read_text())
    raw['institution'] = {'idp_entity_id': 'https://idp.example/idp/shibboleth',
                          'automatic_attribute_release_consent': True}
    config.write_text(yaml.safe_dump(raw))
    _, prep = enabled(config)
    if supported:
        prep.heartbeat(INSTANCE, **CAPABILITY, consent_capability=preparation_module.CONSENT_CAPABILITY)
    started = prep.start(config)
    raw['institution']['automatic_attribute_release_consent'] = False
    config.write_text(yaml.safe_dump(raw))
    claimed = prep.claim(INSTANCE)
    assert claimed.automatic_attribute_release_consent is supported
    assert ('Reload the updated Connector' in started.message) is (not supported)
    assert prep.authorize(**identity(claimed))
    prep.cancel(started.task_id, started.generation)
    assert not prep.authorize(**identity(claimed))


def test_attribute_consent_capability_is_optional_on_bridge_heartbeat(tmp_path):
    config, _, _, _ = prepared(tmp_path)
    app = create_app(config)
    with TestClient(app, base_url='http://localhost') as client:
        for extra in ({}, {'consent_capability': preparation_module.CONSENT_CAPABILITY}):
            response = client.post('/api/connector/preparation/heartbeat', json={'instance_id': INSTANCE, **CAPABILITY, **extra})
            assert response.status_code == 200 and response.json()['ready']
