"""A4 official IdP entry construction; no credentials or browser session."""

from urllib.parse import parse_qs, urlsplit

import literature_monitor.web.preparation_coordinator as preparation_module

import pytest
from fastapi.testclient import TestClient

from literature_monitor.config import ConfigurationError
from literature_monitor.web.preparation_coordinator import (
    CARSI_LOGIN_URL, CARSI_PORTAL_URL, institution_login_url,
    saved_institution_login_url, EXECUTOR_CAPABILITY,
    PreparationCoordinator, PreparationPhase,
)
from literature_monitor.web.capture_coordinator import CaptureCoordinator
from literature_monitor.web.app import create_app
from preparation_helpers import prepared
from test_web_run_settings import csrf_from_html


def test_exact_official_entry_and_url_encoding():
    for entity in ("urn:mace:example.org:university", "https://idp.example.edu/idp/shibboleth"):
        login = institution_login_url(entity)
        assert login.startswith(CARSI_LOGIN_URL + "?")
        parsed = urlsplit(login)
        assert (parsed.scheme, parsed.netloc, parsed.path) == (
            "https", "ds.carsi.edu.cn", "/Shibboleth.sso/Login",
        )
        assert parse_qs(parsed.query) == {
            "entityID": [entity], "target": [CARSI_PORTAL_URL],
        }
        assert "%3A" in parsed.query and "%2F" in parsed.query
        assert "https://idp.example.edu" not in login


def test_no_idp_is_missing_and_invalid_entities_never_become_urls():
    assert institution_login_url(None) is None
    assert institution_login_url("") is None
    for invalid in (
        "javascript:alert(1)", "http://idp.example.edu/path",
        "https://idp.example.edu/?token=x", "https://idp.example.edu/%2Fescape",
        "https://idp.example.edu/path#fragment", "urn:bad\nheader:value", 123,
    ):
        with pytest.raises(ConfigurationError):
            institution_login_url(invalid)


def test_saved_institution_only_no_manual_guess_and_claim_contract(tmp_path):
    config, _, _, _ = prepared(tmp_path)
    assert saved_institution_login_url(config) is None
    entity = "urn:mace:example.edu:campus"
    config.write_text(config.read_text(encoding="utf-8") +
                      "\ninstitution:\n  idp_entity_id: " + entity + "\n", encoding="utf-8")
    url = saved_institution_login_url(config)
    assert parse_qs(urlsplit(url).query) == {
        "entityID": [entity], "target": [CARSI_PORTAL_URL],
    }


def test_capability_and_version_are_required_and_loss_revokes_claimed_task(tmp_path, monkeypatch):
    config, _, _, _ = prepared(tmp_path)
    capture = CaptureCoordinator()
    prep = PreparationCoordinator(capture, execution_enabled=True)
    instance = "carsi-test-instance-001"
    assert not prep.heartbeat(instance)
    assert not prep.heartbeat(instance, capability="carsi-preparation/a3", version="0.6.4")
    assert not prep.heartbeat(instance, capability=EXECUTOR_CAPABILITY, version="0.7.0")
    assert prep.heartbeat(instance, capability=EXECUTOR_CAPABILITY, version="0.6.4")
    assert prep.start(config).phase is PreparationPhase.RUNNING
    command = prep.claim(instance)
    assert command and command.login_url is None
    identity = dict(instance_id=instance, task_id=command.task_id,
                    generation=command.generation, request_id=command.request_id,
                    service_id=command.service_id)
    assert prep.authorize(**identity)
    assert not prep.heartbeat(instance, capability=EXECUTOR_CAPABILITY, version="0.7.0")
    assert not prep.authorize(**identity)
    assert capture.reserve_browser_task("import") is None
    assert prep.snapshot().results[0].reason == "connector_disconnected"
    monkeypatch.setattr(preparation_module, "RELEASE_GRACE_SECONDS", 0.0)
    # Simulate expiration of the already established recovery deadline.
    prep._task.release_after = preparation_module.time.monotonic() - 1
    prep.snapshot()
    assert capture.reserve_browser_task("import") is not None


@pytest.mark.parametrize("version", ["0.6.5", "0.6.6", "0.6.7", "0.6.8"])
def test_claim_freezes_saved_valid_institution_login_without_access_url_navigation(tmp_path, version):
    config, _, _, _ = prepared(tmp_path)
    entity = "https://idp.example.edu/idp/shibboleth"
    config.write_text(config.read_text(encoding="utf-8") +
                      "\ninstitution:\n  idp_entity_id: " + entity + "\n", encoding="utf-8")
    prep = PreparationCoordinator(CaptureCoordinator(), execution_enabled=True)
    instance = "carsi-test-instance-002"
    assert prep.heartbeat(instance, capability=EXECUTOR_CAPABILITY, version=version)
    assert prep.start(config).phase is PreparationPhase.RUNNING
    first = prep.claim(instance)
    assert first is not None
    assert first.login_url == institution_login_url(entity)
    assert parse_qs(urlsplit(first.login_url).query)["target"] == [CARSI_PORTAL_URL]
    assert first.login_url != first.access_url


def test_bridge_visit_nonce_and_attributable_opening_evidence_are_one_shot(tmp_path):
    config, _, _, _ = prepared(tmp_path)
    app = create_app(config)
    instance = "carsi-instance-verified-001"
    with TestClient(app, base_url="http://localhost") as client:
        token = csrf_from_html(client.get("/settings").text)
        assert client.post("/api/connector/preparation/heartbeat", json={
            "instance_id": instance, "capability": EXECUTOR_CAPABILITY, "version": "0.6.4",
        }).json() == {"ready": True}
        assert client.post("/api/preparation/start", json={"csrf_token": token}).status_code == 200
        claimed = client.post("/api/connector/preparation/claim", json={
            "instance_id": instance,
        }).json()["command"]
        assert claimed["last_service"] is False
        identity = {"instance_id": instance, **{
            key: claimed[key] for key in ("task_id", "generation", "request_id", "service_id")}}
        endpoint = "/api/connector/preparation/result"
        assert client.post(endpoint, json={**identity, "outcome": "Opened",
            "reason": "target_opened"}).status_code == 409
        nonce = client.post("/api/connector/preparation/visit-authorize", json={
            **identity, "source_tab_id": 42,
        }).json()["nonce"]
        assert nonce and len(nonce) >= 32
        assert client.post("/api/connector/preparation/visit-authorize", json={
            **identity, "source_tab_id": 42,
        }).status_code == 409
        proof = {"nonce": nonce, "visit_action_observed": True,
                 "source_tab_id": 42, "target_tab_id": 43,
                 "created_target_tab_id": 43, "navigation_observed": True}
        for change in ({"navigation_observed": False}, {"source_tab_id": 99},
                       {"nonce": "x" * 43}, {"visit_action_observed": False},
                       {"created_target_tab_id": 44}):
            assert client.post(endpoint, json={**identity, "outcome": "Opened",
                "reason": "target_opened", "evidence": {**proof, **change}}).status_code == 409
        result = client.post(endpoint, json={**identity, "outcome": "Opened",
            "reason": "target_opened", "evidence": proof})
        assert result.status_code == 200
        assert client.post(endpoint, json={**identity, "outcome": "Opened",
            "reason": "target_opened", "evidence": proof}).json() == {"accepted": True}
        # Existing claim's grant is gone, duplicate receipt cannot advance again.
        for change in ({"navigation_observed": False}, {"nonce": "x" * 43},
                       {"target_tab_id": 99}):
            assert client.post(endpoint, json={**identity, "outcome": "Opened",
                "reason": "target_opened", "evidence": {**proof, **change}}).status_code == 409
        assert client.post("/api/connector/preparation/authorize", json=identity).status_code == 409
        assert client.post("/api/connector/preparation/visit-authorize", json={
            **identity, "source_tab_id": 42,
        }).status_code == 409
        assert client.post("/api/connector/preparation/settled", json=identity).status_code == 404
        state = client.get("/api/preparation/state").json()
        assert state["results"][0] == {"service_id": claimed["service_id"],
                                       "outcome": "Opened", "reason": "target_opened"}
        assert len(state["results"]) == 1
