"""Settings JSON adapter regressions; all writes use isolated files."""
import base64
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from literature_monitor.application import settings as application_settings
from literature_monitor.web.app import create_app
from test_access_mapping_a4 import prepared
from test_full_markdown_import_a5 import candidate
from settings_api_helpers import form

JSON = {"Accept": "application/json"}


@pytest.fixture
def isolated(tmp_path):
    config, path, service, spare = prepared(tmp_path)
    with TestClient(create_app(config), base_url="http://localhost") as client:
        yield client, config, path


def settings(client):
    response = client.get("/api/ui-next/settings")
    assert response.status_code == 200
    return response.json()


def post(client, route, data, **kwargs):
    return client.post(route, headers=JSON, data=data, **kwargs)


@pytest.mark.parametrize("damage", ["monitor_missing", "monitor_corrupt", "list_missing", "list_corrupt", "workspace"])
def test_settings_load_is_independent_of_workspace(isolated, damage):
    client, config, path = isolated
    if damage == "monitor_missing":
        config.unlink()
    elif damage == "monitor_corrupt":
        config.write_text("monitor: [")
    elif damage == "list_missing":
        path.unlink()
    elif damage == "list_corrupt":
        path.write_text("# invalid list")
    else:
        workspace = config.parent / application_settings.load_settings(config).draft.output_dir
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "Papers").write_text("not a directory")
    before = (config.read_bytes() if config.exists() else None, path.read_bytes() if path.exists() else None)
    assert client.get("/ui-next").status_code == 200
    state = settings(client)
    assert state["csrf_token"] and state["config_path"] == str(config)
    assert "monitor_revision_digest" in state["values"]
    if damage != "workspace":
        assert state["issues"]
    assert before == (config.read_bytes() if config.exists() else None, path.read_bytes() if path.exists() else None)


def test_json_save_reuses_safe_writes_and_preserves_human_sections(isolated):
    client, config, path = isolated
    path.write_text(path.read_text() + "\n## Human Notes\n\nPreserve me.\n")
    before_monitor = config.read_bytes()
    data = form(settings(client)); data["journal_group"][0] = "Renamed Group"
    response = post(client, "/settings/save", data)
    assert response.json()["save"]["outcome"] == "SAVED"
    assert response.json()["saved"] is True
    assert "Preserve me." in path.read_text() and config.read_bytes() == before_monitor
    assert client.get("/settings").status_code == 404
    assert client.get("/").status_code == 200


@pytest.mark.parametrize("failure", ["invalid", "revision", "csrf"])
def test_json_save_preserves_submitted_values_and_does_not_write(isolated, failure):
    client, config, path = isolated
    data = form(settings(client))
    if failure == "invalid":
        data["keyword_expression"] = "A NOT B"
    elif failure == "revision":
        path.write_text(path.read_text() + "\n# External change")
    else:
        data["csrf_token"] = "wrong"
    before = config.read_bytes(), path.read_bytes()
    response = post(client, "/settings/save", data)
    if failure == "csrf":
        assert response.status_code == 403 and "CSRF" in response.json()["message"]
    else:
        result = response.json()
        assert result["values"]["keyword_expression"] == data["keyword_expression"]
        assert result["save"]["outcome"] == ("INVALID_DRAFT" if failure == "invalid" else "REVISION_CONFLICT")
    assert before == (config.read_bytes(), path.read_bytes())


def test_json_deletion_requires_the_signed_current_draft(isolated):
    client, config, path = isolated
    data = form(settings(client))
    for key in ("service_id", "service_name", "service_access_url", "service_access_method"):
        data[key] = data[key][:-1]
    original = path.read_bytes()
    preview = post(client, "/settings/save", data).json()
    assert preview["deletion_plan"]["services"] and path.read_bytes() == original
    approved = dict(data, confirm_settings_deletions="yes", settings_deletion_proof=preview["deletion_proof"])
    changed = dict(approved, name="Different Draft")
    rejected = post(client, "/settings/save", changed)
    assert rejected.json()["saved"] is False and path.read_bytes() == original
    result = post(client, "/settings/save", approved)
    assert result.json()["save"]["outcome"] == "SAVED"


def test_json_partial_save_reports_actual_writes_without_rebasing(isolated, monkeypatch):
    client, config, path = isolated
    data = form(settings(client)); data["service_name"][0] = "Changed Service"; data["name"] = "Changed Monitor"
    old = config.read_bytes(), path.read_bytes()
    original = application_settings.replace_text_if_unchanged
    def fail_monitor(target, *args, **kwargs):
        if Path(target) == config:
            raise OSError("fixture monitor write failure")
        return original(target, *args, **kwargs)
    monkeypatch.setattr(application_settings, "replace_text_if_unchanged", fail_monitor)
    result = post(client, "/settings/save", data).json()
    assert result["save"]["outcome"] == "PARTIAL_SAVE"
    assert result["save"]["journal_written"] and not result["save"]["monitor_written"]
    assert result["values"]["journal_revision_digest"] == data["journal_revision_digest"]
    assert config.read_bytes() == old[0] and path.read_bytes() != old[1]


@pytest.mark.parametrize("failure", ["stale", "source", "draft", "dirty", "none"])
def test_json_import_preview_confirm_preserves_authority(isolated, failure):
    client, config, path = isolated
    data = form(settings(client)); source = candidate(path)
    if failure == "dirty":
        data["name"] = "Unsaved Monitor"
    before = config.read_bytes(), path.read_bytes()
    response = post(client, "/settings/import/preview", data,
                    files={"markdown_file": ("list.md", source.encode(), "text/markdown")})
    preview = response.json()
    assert preview["import_plan"]["proposed"] == source
    assert before == (config.read_bytes(), path.read_bytes())
    confirm = dict(data, markdown_import_payload=preview["import_payload"],
                   markdown_import_filename=preview["import_filename"],
                   markdown_import_proof=preview["import_proof"], confirm_full_import="yes")
    if failure == "stale":
        path.write_text(path.read_text() + "\n# external")
    elif failure == "source":
        confirm["markdown_import_payload"] = base64.b64encode((source + "\n").encode()).decode()
    elif failure == "draft":
        confirm["name"] = "Edited after Preview"
    current = config.read_bytes(), path.read_bytes()
    result = post(client, "/settings/import/confirm", confirm)
    if failure == "none":
        assert result.json()["saved"] is True and path.read_text() == source
    else:
        assert result.json()["saved"] is False
        assert current == (config.read_bytes(), path.read_bytes())
    if failure == "dirty":
        confirm["import_discard"] = "yes"
        result = post(client, "/settings/import/confirm", confirm)
        assert result.json()["saved"] is True
        assert result.json()["values"]["name"] != "Unsaved Monitor"
    assert config.read_bytes() == before[0]


def test_json_export_and_reload_guards(isolated):
    client, config, path = isolated
    data = form(settings(client)); data["name"] = "Unsaved"
    assert post(client, "/settings/export", data).json()["export_guard"]
    exported = post(client, "/settings/export", dict(data, export_discard="yes"))
    assert exported.content == path.read_bytes() and "attachment" in exported.headers["Content-Disposition"]
    assert post(client, "/settings/reload", data).json()["reload_guard"]
    response = post(client, "/settings/reload", dict(data, reload_step="confirm", reload_word="RELOAD"))
    assert response.json()["saved"] is True and response.json()["values"]["name"] != "Unsaved"


def test_json_reset_requires_token_and_saved_target(isolated):
    client, config, path = isolated
    state = settings(client)
    plan = client.get("/api/ui-next/settings/reset-preview").json()
    workspace = Path(plan["reset_plan"]["workspace"])
    workspace.mkdir(parents=True, exist_ok=True); marker = workspace / "fixture.txt"; marker.write_text("fixture")
    data = dict(csrf_token=state["csrf_token"], reset_word="RESET", reset_token="wrong")
    rejected = post(client, "/settings/workspace-reset", data)
    assert rejected.status_code == 409 and not rejected.json()["reset_success"] and marker.exists()
    preview = client.get("/api/ui-next/settings/reset-preview").json()
    config.write_text(config.read_text() + "\n# changed revision")
    rejected = post(client, "/settings/workspace-reset", dict(data, reset_token=preview["reset_token"]))
    assert rejected.status_code == 409 and marker.exists()


def test_five_page_shell_and_production_assets(isolated):
    client, _, _ = isolated
    html = client.get("/ui-next").text
    for page in ("inbox", "kept", "journals", "access", "settings"):
        assert f'id="{page}-open"' in html
    assert 'src="settings.js"' in html
    assert html.index('src="settings.js"') > html.index('src="app.js"') > html.index('src="ui-transitions.js"')
    for file in ("settings.js", "settings.css", "licenses/RhineLabUI-MIT.txt"):
        assert client.get("/static/ui_next/" + file).status_code == 200
    script = client.get("/static/ui_next/settings.js").text
    assert "settings-data.js" not in script and "demo-service-" not in script


@pytest.mark.parametrize("action", ["noop", "new_service", "rename_group", "reorder_group"])
def test_frontend_draft_roundtrip_uses_production_form_contract(isolated, action):
    client, config, path = isolated
    before = config.read_bytes(), path.read_bytes()
    state = settings(client)
    node = shutil.which("node")
    assert node, "Node is required for frontend interface verification"
    script = Path("src/literature_monitor/web/static/ui_next/settings.js")
    harness = r"""
const fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(process.argv[1],'utf8');
const controls={},context={FormData,structuredClone,Date,console,crypto:require('node:crypto').webcrypto};
context.$=id=>controls[id] ||= {value:'',focus(){},addEventListener(event,fn){this.handler=fn;}};
context.showDialog=(title,body,choices)=>{context.confirm=choices[1].run;};
context.cancel={};context.changed=context.renderAccess=context.renderCollection=context.renderGroups=()=>{};
context.ui={group:'all'};
vm.createContext(context);
vm.runInContext(`let draft,saved;const copy=structuredClone,same=(a,b)=>JSON.stringify(a)===JSON.stringify(b);const list=()=>draft.list,group=id=>list().groups.find(g=>g.id===id);`+source.slice(source.indexOf('function groupKey('),source.indexOf('async function formRequest(')),context);
context.input=JSON.parse(process.argv[2]);
vm.runInContext('saved=project(input);draft=copy(saved);',context);
vm.runInContext(source.slice(source.indexOf('const esc='),source.indexOf('const copy=')),context);
const action=process.argv[3];
if(action==='new_service'){
  vm.runInContext(source.slice(source.indexOf("$('s-new-service').addEventListener"),source.indexOf('// One native drag payload')),context);
  controls['s-new-service'].handler();
  context.$('s-new-service-name').value='New Real Service';context.$('s-new-service-url').value='https://library.example/new';context.$('s-new-service-method').value='manual';context.confirm();
}
if(action==='rename_group' || action==='reorder_group'){
  vm.runInContext(source.slice(source.indexOf('function editGroup('),source.indexOf('function removeJournal(')),context);
  if(action==='rename_group'){vm.runInContext('editGroup(draft.list.groups[0])',context);controls['s-group-name'].value='Ungrouped';context.confirm();}
  else vm.runInContext('moveGroup(draft.list.groups[1].id,-1)',context);
}
const pairs=vm.runInContext('[...submission().entries()]',context);
console.log(JSON.stringify(pairs));
"""
    result = subprocess.run([node, "-e", harness, str(script), json.dumps(state), action], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    data = {}
    for key, value in json.loads(result.stdout):
        data.setdefault(key, []).append(value)
    data["csrf_token"] = state["csrf_token"]
    response = post(client, "/settings/save", data)
    assert response.json()["save"]["outcome"] == "SAVED"
    if action == "noop":
        assert before == (config.read_bytes(), path.read_bytes())
    elif action == "new_service":
        assert response.json()["values"]["access_services"][-1]["id"].startswith("svc_")
        assert response.json()["values"]["access_services"][-1]["access_method"] == "manual"
    elif action == "rename_group":
        assert response.json()["values"]["journals"][0]["group"] == "Ungrouped"
    else:
        assert response.json()["values"]["groups"] == list(reversed(state["values"]["groups"]))


def test_json_upgrade_is_explicit_and_revision_bound(isolated):
    from test_access_service_schema import LEGACY
    client, config, path = isolated
    path.write_text(LEGACY)
    state = settings(client)
    assert state["needs_access_upgrade"]
    data = form(state)
    original = path.read_bytes()
    preview = post(client, "/settings/upgrade/preview", data).json()
    assert preview["upgrade_plan"] and path.read_bytes() == original
    confirmed = dict(data, confirm_access_upgrade="yes", access_upgrade_proof=preview["upgrade_proof"])
    forged = post(client, "/settings/upgrade/confirm", dict(confirmed, access_upgrade_proof="wrong"))
    assert forged.json()["saved"] is False and path.read_bytes() == original
    response = post(client, "/settings/upgrade/confirm", confirmed)
    assert response.json()["saved"] is True
    assert not settings(client)["needs_access_upgrade"]


def test_diagnostics_are_real_read_only_projections(isolated, monkeypatch):
    from literature_monitor.zotero_local import ZoteroLocalClient, ZoteroInstanceResult, ZoteroReadOutcome
    client, config, path = isolated
    before = config.read_bytes(), path.read_bytes()
    monkeypatch.setattr(ZoteroLocalClient, "current_instance", lambda self: ZoteroInstanceResult(ZoteroReadOutcome.API_FAILURE, None, "fixture unavailable"))
    zotero = client.get("/api/ui-next/settings/diagnostics/zotero").json()
    assert zotero["zotero_instance"]["message"] == "fixture unavailable"
    assert "health_workspace" in client.get("/api/ui-next/settings/diagnostics/health").json()
    assert "snapshot" in client.get("/api/ui-next/run").json()
    assert client.get("/api/ui-next/settings/diagnostics/unknown").status_code == 404
    assert before == (config.read_bytes(), path.read_bytes())
