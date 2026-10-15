"""Chrome cover batches, four-column configuration and isolated local assets."""

from __future__ import annotations

import io
import base64
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from literature_monitor.application import journal_covers as covers
from literature_monitor.application.full_markdown_import import confirm_full_markdown, preview_full_markdown
from literature_monitor.application.settings import load_settings
from literature_monitor.config import ConfigurationError, parse_settings_list_text, render_settings_list_text
from literature_monitor.web.app import create_app
from test_access_mapping_a4 import prepared
from settings_api_helpers import form as settings_form_data


def current_form(client):
    return settings_form_data(client.get("/api/ui-next/settings").json())


def png(color="red"):
    output = io.BytesIO()
    Image.new("RGB", (24, 36), color).save(output, "PNG")
    return output.getvalue()


def test_four_column_settings_and_complete_import_preserve_content(tmp_path):
    config, path, _, _ = prepared(tmp_path)
    notes = "\n## Personal Notes\n\nDo not change this text.\n"
    path.write_text(path.read_text() + notes)
    with TestClient(create_app(config), base_url="http://localhost") as client:
        form = current_form(client)
        assert "journal_cover_url" not in form
        form["journal_group"][0] = "Updated Group"
        # Reordering journal rows must preserve each row's field alignment.
        expected_order = list(reversed(form["journal_name"]))
        for key, values in form.items():
            if key.startswith("journal_") and isinstance(values, list):
                values.reverse()
        saved = client.post("/settings/save", data=form)
        assert saved.json()["saved"] is True, saved.text[-1000:]
    draft = load_settings(config).draft
    assert [journal.name for journal in draft.journals] == expected_order
    assert next(journal for journal in draft.journals if journal.name == "Biometrics").group == "Updated Group"
    assert path.read_text().endswith(notes)
    assert "Cover URL" not in path.read_text()
    journals, publishers, services = parse_settings_list_text(path.read_text(), path=path)
    proposed = render_settings_list_text(path.read_text(),
        (journals[0].model_copy(update={"group": "Imported"}), *journals[1:]),
        publishers, access_services=services, path=path)
    confirm_full_markdown(preview_full_markdown(path, proposed, filename="list.md"))
    assert load_settings(config).draft.journals[0].group == "Imported"
    assert path.read_text().endswith(notes)
    with pytest.raises(ConfigurationError):
        parse_settings_list_text(proposed.replace("| Group |", "| Group | Cover URL |", 1), path=path)


def enable_covers(client):
    reply = client.post("/api/connector/heartbeat", json={
        "version": "0.6.8", "zotero_reachable": False, "capabilities": ["journal-covers/v2"],
    })
    assert reply.status_code == 200


def test_web_search_upload_replacement_skip_and_failure(tmp_path):
    config, path, _, _ = prepared(tmp_path)
    original = path.read_bytes()
    with TestClient(create_app(config), base_url="http://localhost") as client:
        form = current_form(client)
        csrf = {"csrf_token": form["csrf_token"]}
        root = "/settings/covers/0006-341X"
        assert client.post("/settings/covers/search", data={"csrf_token": "wrong"}).status_code == 403
        assert client.post("/settings/covers/search", data=csrf).status_code == 409
        enable_covers(client)
        upload = client.post(root + "/upload", data=csrf, files={"file": ("cover.png", png(), "image/png")})
        assert upload.json()["state"] == "Uploaded"
        before = client.get(root + "/image").content
        batch = client.post("/settings/covers/search", data=csrf).json()
        command = client.post("/api/connector/covers/claim", json={}).json()["command"]
        assert batch["total"] == 1 and command["journals"][0]["issn_l"] != "0006-341X"
        assert client.post(root + "/upload", data=csrf).status_code == 409
        capture = client.app.state.capture_coordinator
        assert capture.reserve_browser_task('import') is None
        assert capture.reserve_browser_task('preparation') is None
        assert capture.reserve_browser_task('enrichment') is None
        assert client.post("/api/connector/covers/result", json={
            "request_id": command["request_id"], "issn_l": command["journals"][0]["issn_l"], "source_url": None,
        }).json()["accepted"]
        client.post("/api/connector/covers/finish", json={"request_id": command["request_id"]})
        state = client.get("/settings/covers/search").json()
        assert (state["completed"], state["failed"], state["phase"]) == (1, 1, "Completed")
        assert state["results"][0]["status"] == "未获取到图片"
        assert client.get(root + "/image").content == before

        # Real route, decoding, conversion and atomic asset writes.
        for valid in (True, False):
            client.post("/settings/covers/search", data={**csrf, "issn_l": "0006-341X"})
            command = client.post("/api/connector/covers/claim", json={}).json()["command"]
            result = client.post("/api/connector/covers/result", json={
                "request_id": command["request_id"], "issn_l": "0006-341X", "source_url": "https://example.org/original.png",
                "image_base64": base64.b64encode(png("green") if valid else b"<html>blocked</html>").decode(),
            })
            assert result.status_code == 200
            client.post("/api/connector/covers/finish", json={"request_id": command["request_id"]})
            image = client.get(root + "/image")
            assert image.headers["content-type"] == "image/jpeg"
            assert image.headers["x-content-type-options"] == "nosniff"
            if valid:
                before = image.content
                assert client.get("/settings/covers/search").json()["saved"] == 1
            else:
                assert image.content == before
                assert client.get("/settings/covers/search").json()["failed"] == 1
        assert path.read_bytes() == original
        assert client.get('/api/ui-next/settings').status_code == 200


def test_cancel_during_download_rejects_late_commit(tmp_path, monkeypatch):
    config, path, _, _ = prepared(tmp_path)
    fetching, finish = threading.Event(), threading.Event()
    decode = base64.b64decode
    def slow_decode(*args, **kwargs):
        fetching.set()
        assert finish.wait(2)
        return decode(*args, **kwargs)
    monkeypatch.setattr("literature_monitor.web.journal_covers.base64.b64decode", slow_decode)
    with TestClient(create_app(config), base_url="http://localhost") as client, ThreadPoolExecutor() as pool:
        csrf = {"csrf_token": current_form(client)['csrf_token']}
        enable_covers(client)
        client.post('/settings/covers/search', data={**csrf, 'issn_l': '0006-341X'})
        command = client.post('/api/connector/covers/claim', json={}).json()['command']
        pending = pool.submit(client.post, '/api/connector/covers/result', json={
            'request_id': command['request_id'], 'issn_l': '0006-341X', 'source_url': 'https://example.org/cover.png',
            'image_base64': base64.b64encode(png()).decode()})
        assert fetching.wait(2)
        assert client.post("/settings/covers/reset", data=csrf).status_code == 409
        try:
            assert client.post('/settings/covers/search/cancel', data={**csrf, 'request_id': command['request_id']}).json()['accepted']
        finally:
            finish.set()
        assert pending.result().status_code == 409
        assert covers.cover_image(path, '0006-341X') is None
        client.post('/api/connector/covers/finish', json={'request_id': command['request_id']})
        state = client.get('/settings/covers/search').json()
        assert state['phase'] == 'Cancelled' and state['completed'] == 0 and not state['busy']


def test_manifest_and_damaged_asset_fail_without_repair(tmp_path):
    _, path, _, _ = prepared(tmp_path)
    covers.upload_cover(path, '0006-341X', png())
    manifest = tmp_path / 'journal-assets' / 'manifest.json'
    record = json.loads(manifest.read_text())['journals']['0006-341X']
    asset = tmp_path / 'journal-assets' / record['filename']
    asset.write_bytes(b'damaged')
    assert covers.cover_details(path, '0006-341X')['state'] == 'Generated'
    with pytest.raises(covers.CoverError, match='damaged'):
        covers.upload_cover(path, '0006-341X', png())
    assert asset.read_bytes() == b'damaged'
    manifest.write_text('{broken')
    with pytest.raises(covers.CoverError, match='Manifest'):
        covers.upload_cover(path, '0006-341X', png('blue'))
    assert manifest.read_text() == '{broken'


def test_bad_images_and_symlink_asset_directory(tmp_path):
    for payload in (b"GIF89a", b"<html></html>", b""):
        with pytest.raises(covers.CoverError):
            covers._normalize_image(payload)
    _, path, _, _ = prepared(tmp_path)
    journal = parse_settings_list_text(path.read_text(), path=path)[0][0]
    (tmp_path / "journal-assets").symlink_to(tmp_path / "outside")
    with pytest.raises(OSError):
        covers.upload_cover(path, journal.issn_l, png())
    with pytest.raises(OSError):
        covers.reset_covers(path)
    assert (tmp_path / "journal-assets").is_symlink()


@pytest.mark.parametrize("image_format, mode", [("JPEG", "RGB"), ("PNG", "RGBA"),
                                                  ("WebP", "RGB"), ("GIF", "RGB")])
def test_upload_formats_use_shared_conversion_and_preserve_selection_on_error(tmp_path, image_format, mode):
    _, path, _, _ = prepared(tmp_path)
    payload = io.BytesIO()
    Image.new(mode, (24, 36), "red").save(payload, image_format)
    assert covers.upload_cover(path, "0006-341X", payload.getvalue()) == "Uploaded"
    selected, mime = covers.cover_image(path, "0006-341X")
    with Image.open(io.BytesIO(selected)) as image:
        assert image.size == (24, 36)
        assert image.format == ("PNG" if mode == "RGBA" else "JPEG")
    assert mime == ("image/png" if mode == "RGBA" else "image/jpeg")
    if image_format == "JPEG":
        manifest = tmp_path / "journal-assets" / "manifest.json"
        before = manifest.read_bytes()
        with pytest.raises(covers.CoverError):
            covers.upload_cover(path, "0006-341X", b"not an image")
        assert manifest.read_bytes() == before
        assert covers.cover_image(path, "0006-341X")[0] == selected


def test_continue_retry_and_reset_are_current_batch_operations(tmp_path):
    config, path, _, _ = prepared(tmp_path)
    before = path.read_bytes()
    marker = tmp_path / 'workspace-marker.txt'
    marker.write_text('keep')
    image = base64.b64encode(png()).decode()
    with TestClient(create_app(config), base_url='http://localhost') as client:
        csrf = {'csrf_token': current_form(client)['csrf_token']}
        # v1 must not enable a v2 payload contract.
        client.post('/api/connector/heartbeat', json={
            'version': '0.6.8', 'zotero_reachable': False, 'capabilities': ['journal-covers/v1']})
        assert not client.get('/settings/covers/search').json()['supported']
        enable_covers(client)
        start = client.post('/settings/covers/search', data=csrf).json()
        command = client.post('/api/connector/covers/claim', json={}).json()['command']
        assert len(command['journals']) == 2
        first, second = command['journals']
        def submit(command, journal, data):
            return client.post('/api/connector/covers/result', json={
                'request_id': command['request_id'], 'issn_l': journal['issn_l'],
                'source_url': 'https://example.org/image', 'image_base64': data})
        assert submit(command, first, 'invalid base64').json()['accepted']
        assert client.post('/settings/covers/reset', data=csrf).status_code == 409
        client.post('/settings/covers/search/cancel', data={**csrf, 'request_id': start['request_id']})
        assert client.post('/settings/covers/search/continue', data={**csrf, 'request_id': start['request_id']}).status_code == 409
        client.post('/api/connector/covers/finish', json={'request_id': start['request_id']})
        stopped = client.get('/settings/covers/search').json()
        assert (stopped['completed'], stopped['remaining'], stopped['failed']) == (1, 1, 1)
        assert stopped['can_continue'] and stopped['can_retry']
        with TestClient(create_app(config), base_url='http://localhost') as restarted:
            fresh = restarted.get('/settings/covers/search').json()
            assert fresh['phase'] == 'Idle' and not fresh['can_continue'] and not fresh['can_retry']
        assert client.get('/settings/covers/search').json()['can_continue']
        continued = client.post('/settings/covers/search/continue', data={**csrf, 'request_id': start['request_id']}).json()
        assert continued['request_id'] != start['request_id'] and continued['completed'] == 1
        assert submit(command, second, image).status_code == 409
        command2 = client.post('/api/connector/covers/claim', json={}).json()['command']
        assert command2['journals'] == [second]
        covers.upload_cover(path, second['issn_l'], png('blue'))
        assert submit(command2, second, image).json()['accepted']
        assert client.get('/settings/covers/search').json()['results'][-1]['result'] == 'Skipped'
        client.post('/api/connector/covers/finish', json={'request_id': command2['request_id']})
        retry = client.post('/settings/covers/search/retry', data={**csrf, 'request_id': command2['request_id']}).json()
        assert (retry['total'], retry['completed'], retry['saved'], retry['failed']) == (1, 0, 0, 0)
        retry_command = client.post('/api/connector/covers/claim', json={}).json()['command']
        assert retry_command['journals'] == [first]
        # Cancelling a retry retains its own pending queue for Continue.
        client.post('/settings/covers/search/cancel', data={**csrf, 'request_id': retry['request_id']})
        client.post('/api/connector/covers/finish', json={'request_id': retry['request_id']})
        resumed = client.post('/settings/covers/search/continue', data={**csrf, 'request_id': retry['request_id']}).json()
        retry_command = client.post('/api/connector/covers/claim', json={}).json()['command']
        assert retry_command['journals'] == [first]
        assert submit(retry_command, first, image).json()['accepted']
        client.post('/api/connector/covers/finish', json={'request_id': resumed['request_id']})
        assert client.get('/settings/covers/search').json()['saved'] == 1
        # Explicit failed replacement retries even while an old selection exists.
        single = client.post('/settings/covers/search', data={**csrf, 'issn_l': first['issn_l']}).json()
        single_command = client.post('/api/connector/covers/claim', json={}).json()['command']
        submit(single_command, first, None)
        client.post('/api/connector/covers/finish', json={'request_id': single['request_id']})
        client.post('/settings/covers/search/retry', data={**csrf, 'request_id': single['request_id']})
        single_command = client.post('/api/connector/covers/claim', json={}).json()['command']
        assert submit(single_command, first, image).json()['accepted']
        client.post('/api/connector/covers/finish', json={'request_id': single_command['request_id']})
        client.post(f"/settings/covers/{first['issn_l']}/upload", data=csrf,
                    files={'file': ('cover.png', png('blue'), 'image/png')})
        assets = tmp_path / 'journal-assets'
        (assets / 'old-orphan.jpg').write_bytes(b'old')
        capture = client.app.state.capture_coordinator
        for kind in ('import', 'preparation', 'enrichment'):
            owner = capture.reserve_browser_task(kind)
            assert owner
            assert client.post('/settings/covers/reset', data=csrf).status_code == 409
            capture.release_browser_task(kind, owner)
        assert client.post('/settings/covers/reset', data={'csrf_token': 'wrong'}).status_code == 403
        # Reset is local, even if the Connector is unavailable.
        capture._last_heartbeat_monotonic = None
        reset = client.post('/settings/covers/reset', data=csrf)
        assert reset.status_code == 200
        assert reset.json()['phase'] == 'Idle' and reset.json()['total'] == 0
        assert not assets.exists()
        assert client.post('/api/connector/covers/claim', json={}).json()['command'] is None
        assert path.read_bytes() == before and marker.read_text() == 'keep'
