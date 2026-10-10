"""A7 public Settings context, using temporary files and synthetic Connector events."""
from dataclasses import asdict, replace
import json
from pathlib import Path
import shutil
import subprocess

from fastapi.testclient import TestClient
import pytest
import yaml

from literature_monitor.application import settings
from literature_monitor.application.access import AccessObservation
from literature_monitor.config import ConfigurationError, InstitutionConfig, load_config, parse_monitor_definition
from literature_monitor.web.app import create_app
from test_a4_publisher_settings import setup
from test_batch_import import paper, command, finished, state
from test_access_preparation import access_payload, result
from test_unified_import_ui import setup as import_setup, start_data, poll
from test_web_run_settings import browser_settings_submission, SettingsDOM, SETTINGS_NODE_DOM


PUBLIC = InstitutionConfig(name="厦门大学", idp_entity_id="https://idp.example/idp/shibboleth")


@pytest.fixture(autouse=True)
def no_provider_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Institution settings must not request a Provider")
    monkeypatch.setattr(settings, "OpenAlexClient", forbidden)
    from literature_monitor.openalex import OpenAlexClient
    from literature_monitor.crossref import CrossrefClient
    monkeypatch.setattr(OpenAlexClient, "_request_json", forbidden)
    monkeypatch.setattr(CrossrefClient, "_request_json", forbidden)


@pytest.mark.parametrize("institution", [None, {}, {"name": None, "idp_entity_id": None},
                                          {"name": "   ", "idp_entity_id": " "}])
def test_blank_and_legacy_config_are_unconfigured(tmp_path, institution):
    config, _, draft = setup(tmp_path)
    assert draft.institution is load_config(config).institution is None
    raw = yaml.safe_load(config.read_text())
    raw["institution"] = institution
    config.write_text(yaml.safe_dump(raw))
    assert load_config(config).institution is settings.load_settings(config).draft.institution is None
    assert settings.validate_settings(config, settings.load_settings(config).draft).outcome is settings.SettingsValidationOutcome.VALID


@pytest.mark.parametrize("institution", [PUBLIC, InstitutionConfig(name="University A"),
                                          InstitutionConfig(idp_entity_id="urn:mace:example.org:idp")])
def test_load_edit_validate_save_reload_preserves_all_unrelated_content(tmp_path, monkeypatch, institution):
    config, venues, _ = setup(tmp_path)
    # Deliberately non-canonical layout and CRLF must survive an institution-only Save.
    venues.write_bytes(venues.read_bytes().replace(b"| Canonical A |", b"|  Canonical A  |").replace(b"\n", b"\r\n"))
    _, document = paper(tmp_path / "workspace", "a")
    before = venues.read_bytes(), document.read_bytes(), venues.stat().st_ino
    opened = settings.load_settings(config)
    def forbidden(*args, **kwargs):
        pytest.fail("Institution-only Save must not resolve Journal/Publisher metadata or render list.md")
    monkeypatch.setattr(settings, "_resolve_settings_targets", forbidden)
    monkeypatch.setattr(settings, "render_settings_list_text", forbidden)
    draft = replace(opened.draft, institution=institution)
    validation = settings.validate_settings(config, draft)
    assert validation.config.institution == institution
    saved = settings.save_settings(config, draft)
    assert saved.outcome is settings.SettingsSaveOutcome.SAVED
    assert saved.monitor_written and not saved.journal_written
    assert saved.state.draft.institution == load_config(config).institution == institution
    assert saved.state.draft.journals == opened.draft.journals
    assert saved.state.draft.publishers == opened.draft.publishers
    assert saved.journal_revision == opened.draft.journal_revision
    assert (venues.read_bytes(), document.read_bytes(), venues.stat().st_ino) == before
    assert yaml.safe_load(config.read_text())["institution"] == institution.model_dump(exclude_none=True)
    cleared = settings.save_settings(config, replace(saved.state.draft, institution=None))
    assert cleared.outcome is settings.SettingsSaveOutcome.SAVED
    assert load_config(config).institution is None and "institution" not in yaml.safe_load(config.read_text())
    assert (venues.read_bytes(), document.read_bytes(), venues.stat().st_ino) == before


INVALID = [
    {"name": 123}, {"name": ["University"]}, {"name": "x" * 121},
    {"name": "University\npassword"}, {"name": "University\u202e"},
    {"name": "<script>alert(1)</script>"}, {"name": "user@example.org"},
    {"idp_entity_id": 42}, {"idp_entity_id": "idp"}, {"idp_entity_id": "http://idp.example"},
    {"idp_entity_id": "https://user:secret@idp.example"},
    {"idp_entity_id": "https://idp.example/?SAMLResponse=secret"},
    {"idp_entity_id": "https://idp.example/#access_token=secret"},
    {"idp_entity_id": "https://idp.example/%3Ftoken"}, {"idp_entity_id": "https://localhost/idp"},
    {"idp_entity_id": "https://127.0.0.1"}, {"idp_entity_id": "javascript:alert(1)"},
    {"idp_entity_id": "https://idp.example/\n"}, {"idp_entity_id": "urn:example:" + "x" * 512},
]


@pytest.mark.parametrize("institution", INVALID)
def test_invalid_config_and_application_draft_rejected_without_writes(tmp_path, institution):
    config, venues, opened = setup(tmp_path)
    before = config.read_bytes(), venues.read_bytes()
    with pytest.raises(ConfigurationError):
        parse_monitor_definition(config, {"keyword_expression": "causal", "institution": institution})
    saved = settings.save_settings(config, replace(opened, institution=institution))
    assert saved.outcome is settings.SettingsSaveOutcome.INVALID_DRAFT
    assert (config.read_bytes(), venues.read_bytes()) == before


@pytest.mark.parametrize("key", ["account", "password", "cookies", "SAMLResponse", "session_snapshot",
                                 "access_token", "login_history", "expires_at", "login_url"])
def test_sensitive_config_and_form_fields_rejected_without_echo_or_write(tmp_path, key):
    config, venues, _ = setup(tmp_path)
    raw = yaml.safe_load(config.read_text())
    raw["institution"] = {"name": "University", key: "SECRET-MUST-NOT-APPEAR"}
    with pytest.raises(ConfigurationError) as failure:
        parse_monitor_definition(config, raw)
    assert "SECRET-MUST-NOT-APPEAR" not in str(failure.value)
    before = config.read_bytes(), venues.read_bytes()
    with TestClient(create_app(config), base_url="http://localhost") as client:
        fields = browser_settings_submission(client.get("/settings").text)
        for field in (key, "institution_" + key):
            response = client.post("/settings/save", data={**fields, field: ["SECRET-MUST-NOT-APPEAR"]})
            assert "Settings were not saved." in response.text
            assert "SECRET-MUST-NOT-APPEAR" not in response.text
            assert "settingsSaved" not in response.headers.get("HX-Trigger", "")
    assert (config.read_bytes(), venues.read_bytes()) == before


@pytest.mark.parametrize("field", [
    "idp_password", "idp_cookie", "idp_session", "idp_token", "saml_assertion",
    "authentication_password", "auth_cookie", "session_token", "credential_secret",
    "oauth_access_token", "shibboleth_session", "openathens_token", "IdP_Password", "idp.password",
])
@pytest.mark.parametrize("htmx", [False, True])
def test_unknown_sensitive_save_fields_rejected_without_echo_or_write(tmp_path, field, htmx):
    config, venues, _ = setup(tmp_path)
    _, document = paper(tmp_path / "workspace", "a")
    before = config.read_bytes(), venues.read_bytes(), document.read_bytes()
    secret = "SENSITIVE-HTTP-PROBE-MUST-NOT-APPEAR"
    with TestClient(create_app(config), base_url="http://localhost") as client:
        data = browser_settings_submission(client.get("/settings").text)
        data.update(name=["Unsaved monitor"], institution_name=[PUBLIC.name],
                    institution_idp_entity_id=[PUBLIC.idp_entity_id])
        response = client.post("/settings/save", data={**data, field: [secret]},
                               headers={"HX-Request": "true"} if htmx else {})
        assert "Settings were not saved." in response.text
        assert "Settings saved." not in response.text
        assert "settingsSaved" not in response.headers.get("HX-Trigger", "")
        assert secret not in response.text and secret not in str(response.headers)
        assert browser_settings_submission(response.text)["institution_name"] == [PUBLIC.name]
        assert settings.load_settings(config).draft.institution is None
    assert (config.read_bytes(), venues.read_bytes(), document.read_bytes()) == before
    assert all(secret.encode() not in content for content in before)


@pytest.mark.parametrize("endpoint", ["preview", "confirm"])
@pytest.mark.parametrize("field", ["idp_password", "idp_cookie", "idp_session", "idp_token", "saml_assertion"])
def test_import_draft_rejects_sensitive_fields_without_apply_or_echo(tmp_path, endpoint, field):
    config, venues, _ = setup(tmp_path)
    _, document = paper(tmp_path / "workspace", "a")
    before = config.read_bytes(), venues.read_bytes(), document.read_bytes()
    secret = "SENSITIVE-IMPORT-PROBE-MUST-NOT-APPEAR"
    with TestClient(create_app(config), base_url="http://localhost") as client:
        data = browser_settings_submission(client.get("/settings").text)
        data.update(institution_name=[PUBLIC.name], institution_idp_entity_id=[PUBLIC.idp_entity_id])
        response = client.post("/settings/import/" + endpoint, data={**data, field: [secret]},
                               headers={"HX-Request": "true"})
        assert ("Import not written" if endpoint == "confirm" else "Import Preview blocked") in response.text
        assert "settingsDraftChanged" not in response.headers.get("HX-Trigger", "")
        assert "settingsSaved" not in response.headers.get("HX-Trigger", "")
        assert secret not in response.text and secret not in str(response.headers)
        submitted = browser_settings_submission(response.text)
        assert submitted["institution_name"] == [PUBLIC.name]
        assert submitted["institution_idp_entity_id"] == [PUBLIC.idp_entity_id]
    assert (config.read_bytes(), venues.read_bytes(), document.read_bytes()) == before


def test_invalid_stored_institution_recovers_without_exposing_secrets(tmp_path):
    config, _, _ = setup(tmp_path)
    raw = yaml.safe_load(config.read_text())
    raw["institution"] = {"name": "University", "password": "SECRET-MUST-NOT-APPEAR"}
    config.write_text(yaml.safe_dump(raw))
    before = config.read_bytes()
    loaded = settings.load_settings(config)
    assert loaded.issues and loaded.draft.institution is None
    with TestClient(create_app(config), base_url="http://localhost") as client:
        response = client.get("/settings")
        assert "SECRET-MUST-NOT-APPEAR" not in response.text
        assert "Not configured." in response.text
    assert config.read_bytes() == before


def test_shared_settings_save_preserves_context_and_revalidates_model_copies(tmp_path):
    config, venues, draft = setup(tmp_path)
    saved = settings.save_settings(config, replace(draft, institution=PUBLIC))
    assert saved.outcome is settings.SettingsSaveOutcome.SAVED
    before = venues.read_bytes()
    edited = settings.save_settings(config, replace(saved.state.draft, name="Edited monitor"))
    assert edited.outcome is settings.SettingsSaveOutcome.SAVED
    assert edited.state.draft.name == "Edited monitor"
    assert load_config(config).institution == PUBLIC
    assert venues.read_bytes() == before
    bypassed = PUBLIC.model_copy(update={"name": "<script>malicious</script>"})
    rejected = settings.save_settings(config, replace(edited.state.draft, institution=bypassed))
    assert rejected.outcome is settings.SettingsSaveOutcome.INVALID_DRAFT
    assert load_config(config).institution == PUBLIC


def test_institution_save_retains_existing_publisher_membership_gate(tmp_path):
    config, venues, _ = setup(tmp_path)
    text = venues.read_text()
    venues.write_text(text[:text.index("## Publishers")] + text[text.index("## Conferences"):])
    opened = settings.load_settings(config)
    before = config.read_bytes(), venues.read_bytes()
    saved = settings.save_settings(config, replace(opened.draft, institution=PUBLIC))
    assert saved.outcome is settings.SettingsSaveOutcome.INVALID_DRAFT
    assert (config.read_bytes(), venues.read_bytes()) == before


@pytest.mark.parametrize("failure", ["revision", "write", "list_race", "monitor_race"])
def test_revision_and_storage_failure_preserve_institution_attempt(tmp_path, monkeypatch, failure):
    config, venues, opened = setup(tmp_path)
    before = config.read_bytes(), venues.read_bytes()
    draft = replace(opened, institution=PUBLIC)
    if failure == "revision":
        config.write_bytes(config.read_bytes() + b"# concurrent edit\n")
    elif failure == "write":
        def fail(path, *args):
            assert path == config
            raise OSError("test storage unavailable")
        monkeypatch.setattr(settings, "_write_snapshot_target", fail)
    elif failure == "list_race":
        original = settings._render_monitor_yaml
        def render(*args, **kwargs):
            venues.write_bytes(venues.read_bytes() + b"# concurrent list edit\r\n")
            return original(*args, **kwargs)
        monkeypatch.setattr(settings, "_render_monitor_yaml", render)
    else:
        original = settings._write_snapshot_target
        def write(path, *args):
            config.write_bytes(config.read_bytes() + b"# CAS race\n")
            return original(path, *args)
        monkeypatch.setattr(settings, "_write_snapshot_target", write)
    saved = settings.save_settings(config, draft)
    assert saved.outcome is (settings.SettingsSaveOutcome.WRITE_FAILED if failure == "write" else settings.SettingsSaveOutcome.REVISION_CONFLICT)
    assert not saved.journal_written and not saved.monitor_written
    assert saved.state.draft.institution is None
    assert config.read_bytes() == before[0] + ({"revision": b"# concurrent edit\n", "monitor_race": b"# CAS race\n"}.get(failure, b""))
    assert venues.read_bytes() == before[1] + (b"# concurrent list edit\r\n" if failure == "list_race" else b"")


@pytest.mark.parametrize("failure", ["conflict", "write"])
def test_web_failure_retains_draft_and_current_disk_state(tmp_path, monkeypatch, failure):
    config, venues, _ = setup(tmp_path)
    before = config.read_bytes(), venues.read_bytes()
    with TestClient(create_app(config), base_url="http://localhost") as client:
        data = browser_settings_submission(client.get("/settings").text)
        data.update(institution_name=[PUBLIC.name], institution_idp_entity_id=[PUBLIC.idp_entity_id])
        if failure == "conflict":
            config.write_bytes(config.read_bytes() + b"# another editor\n")
        else:
            def denied(*args):
                raise OSError("test storage denied")
            monkeypatch.setattr(settings, "_write_snapshot_target", denied)
        response = client.post("/settings/save", data=data, headers={"HX-Request": "true"})
        assert "Settings saved." not in response.text
        assert browser_settings_submission(response.text)["institution_name"] == [PUBLIC.name]
        assert "settingsSaved" not in response.headers.get("HX-Trigger", "")
        assert settings.load_settings(config).draft.institution is None
    assert venues.read_bytes() == before[1]
    assert config.read_bytes() == before[0] + (b"# another editor\n" if failure == "conflict" else b"")


def test_web_csrf_form_roundtrip_preview_and_single_save(tmp_path):
    config, venues, _ = setup(tmp_path)
    before = config.read_bytes(), venues.read_bytes()
    with TestClient(create_app(config), base_url="http://localhost") as client:
        page = client.get("/settings").text
        assert "Not configured." in page
        assert "Configured institution ≠ authenticated session ≠ full-text entitlement" in page
        fields = browser_settings_submission(page)
        fields.update(institution_name=[PUBLIC.name], institution_idp_entity_id=[PUBLIC.idp_entity_id])
        for endpoint in ("/settings/save", "/settings/import/preview", "/settings/import/confirm"):
            assert client.post(endpoint, data={**fields, "csrf_token": ["wrong"]}).status_code == 403
        duplicate = client.post("/settings/save", data={**fields, "institution_name": ["First", "Second"]})
        assert "Settings were not saved." in duplicate.text
        invalid = client.post("/settings/save", data={**fields, "institution_name": ["<script>alert(1)</script>"]})
        assert "Settings were not saved." in invalid.text and "<script>alert(1)</script>" not in invalid.text
        preview = client.post("/settings/import/preview", data=fields,
                              files={"markdown_file": ("saved.md", venues.read_bytes(), "text/markdown")},
                              headers={"HX-Request": "true"})
        assert browser_settings_submission(preview.text)["institution_name"] == [PUBLIC.name]
        assert "settingsDraftChanged" not in preview.headers.get("HX-Trigger", "")
        assert browser_settings_submission(preview.text)["institution_idp_entity_id"] == [PUBLIC.idp_entity_id]
        assert (config.read_bytes(), venues.read_bytes()) == before
        saved = client.post("/settings/save", data=fields, headers={"HX-Request": "true"})
        assert saved.headers["HX-Trigger"] == "settingsSaved" and "Settings saved." in saved.text
        reloaded = browser_settings_submission(client.get("/settings").text)
        assert reloaded["institution_name"] == [PUBLIC.name]
        assert reloaded["institution_idp_entity_id"] == [PUBLIC.idp_entity_id]
        assert reloaded["monitor_revision_digest"] != fields["monitor_revision_digest"]
        assert reloaded["journal_revision_digest"] == fields["journal_revision_digest"]
    assert venues.read_bytes() == before[1]


def test_old_import_token_expires_and_fresh_context_remains_manual(tmp_path):
    app, config, root = import_setup(tmp_path)
    _, path = paper(root, "a")
    before = path.read_bytes()
    with TestClient(app, base_url="http://localhost") as client:
        stale = start_data(client)
        fields = browser_settings_submission(client.get("/settings").text)
        fields.update(institution_name=[PUBLIC.name], institution_idp_entity_id=[PUBLIC.idp_entity_id])
        assert "Settings saved." in client.post("/settings/save", data=fields).text
        assert path.read_bytes() == before
        assert client.post("/imports/start", data=stale).status_code == 409
        assert client.post("/api/connector/claim", json={}).json()["command"] is None
        assert path.read_bytes() == before
        assert client.post("/imports/start", data=start_data(client)).status_code == 200
        cmd = command(app.state.batch_import)
        payload = access_payload(cmd)
        assert client.post("/api/connector/access", json=payload).status_code == 200
        assert app.state.batch_import.snapshot().current_access == AccessObservation(**payload["observation"])
        html = poll(client, app).text
        assert "Access Service unknown" in html and "resource entitlement: unknown" in html
        serialized = json.dumps(asdict(cmd))
        assert PUBLIC.name not in serialized and PUBLIC.idp_entity_id not in serialized
        assert result(client, cmd, "FAILED").status_code == 200
        finished(app.state.batch_import)
    assert path.read_bytes() == before and state(path).status.value == "kept"
    overlay = Path("connector/overlay/literature-monitor-access.js").read_text()
    assert "const VERIFIED_RULES = Object.freeze([])" in overlay


def test_config_context_changes_before_locked_start_reject_reservation(tmp_path, monkeypatch):
    app, config, root = import_setup(tmp_path)
    _, path = paper(root, "a")
    before = path.read_bytes()
    original = app.state.batch_import.start
    def change_before_start(workspace, **kwargs):
        draft = settings.load_settings(config).draft
        assert settings.save_settings(config, replace(draft, institution=PUBLIC)).outcome is settings.SettingsSaveOutcome.SAVED
        return original(workspace, **kwargs)
    monkeypatch.setattr(app.state.batch_import, "start", change_before_start)
    with TestClient(app, base_url="http://localhost") as client:
        assert client.post("/imports/start", data=start_data(client)).status_code == 409
        assert client.post("/api/connector/claim", json={}).json()["command"] is None
    assert path.read_bytes() == before


def test_executable_settings_dom_institution_edit_and_htmx_preview(tmp_path):
    node = shutil.which("node")
    assert node is not None, "Node required for A7 DOM verification"
    config, _, _ = setup(tmp_path)
    with TestClient(create_app(config), base_url="http://localhost") as client:
        page = client.get("/settings").text
        fields = browser_settings_submission(page)
        fields.update(institution_name=[PUBLIC.name], institution_idp_entity_id=[PUBLIC.idp_entity_id])
        response = client.post("/settings/import/preview", data=fields).text
        source = client.get("/static/app.js").text
    script = f"const PAGE={json.dumps(SettingsDOM(page).root)}, SOURCE={json.dumps(source)}, RESPONSE={json.dumps(SettingsDOM(response).root)};\n" + SETTINGS_NODE_DOM + r'''
const editor = () => document.getElementById("settings-editor");
let input = editor().querySelector('[name="institution_name"]');
input.value = '厦门大学'; handlers.input({target: input});
assert.equal(document.documentElement.dataset.settingsDirty, 'true');
bodyHandlers['htmx:beforeSwap']({detail: {target: editor()}});
dom = build(RESPONSE);
bodyHandlers['htmx:afterSwap']({detail: {target: editor()}});
assert.equal(editor().querySelector('[name="institution_name"]').value, '厦门大学');
assert.equal(editor().querySelector('[name="institution_idp_entity_id"]').value, 'https://idp.example/idp/shibboleth');
bodyHandlers.settingsDraftChanged(); assert.equal(document.documentElement.dataset.settingsDirty, 'true');
bodyHandlers.settingsSaved(); assert.equal(document.documentElement.dataset.settingsDirty, 'false');
'''
    run = subprocess.run([node, "-e", script], capture_output=True, text=True)
    assert run.returncode == 0, run.stdout + run.stderr
