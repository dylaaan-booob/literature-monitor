"""Temporary Workspace helpers shared by Kept Workflow and native Import API checks."""
from literature_monitor.web.app import create_app
from test_web_app import write_valid_config


def setup(tmp_path, *, connected=True):
    config, root = write_valid_config(tmp_path)
    # Native Import tests need a complete, valid Settings document before Workspace tokens exist.
    with (config.parent / "list.md").open("a", encoding="utf-8") as mapping:
        mapping.write("\n## Publishers\n\n| Publisher | OpenAlex ID | Publisher URL | Access Service ID |\n|---|---|---|---|\n\n## Access Services\n\n| Service ID | Service | Access URL |\n|---|---|---|\n")
    app = create_app(config)
    if connected:
        app.state.capture_coordinator.heartbeat(connector_version="A5-test", zotero_reachable=True)
    return app, config, root


def start_data(client):
    response = client.get("/api/ui-next/workspace")
    assert response.status_code == 200, response.text
    state = response.json()
    return {field: state[field] for field in ("csrf_token", "import_token")}
