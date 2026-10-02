"""A2 uses only MockTransport, tmp_path and a fake OS credential backend."""

from datetime import datetime, timedelta, timezone
from collections.abc import Iterator
from email.utils import format_datetime
import hashlib
import json
import logging
import os
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from literature_monitor import zotero_credentials as credentials
from literature_monitor.zotero_credentials import ZoteroCredentialStore, ZoteroCredentialStoreError
from literature_monitor.zotero_local import LOCAL_API_BASE, VerifiedZoteroItem
from literature_monitor.zotero_write import (
    ZoteroAuthorizationOutcome as Auth,
    ZoteroUploadOutcome as Upload,
    ZoteroUploadStage as Stage,
    ZoteroWriteClient,
)

SERVER = "test-instance-A"
OTHER_SERVER = "test-instance-B"
PARENT = VerifiedZoteroItem("PARENT23", "10.1234/example", SERVER)
ATTACHMENT = "CHILD234"
# Explicit sentinel secrets; these are fabricated, never real credentials.
SECRET = "SENTINELlocalAPIkey".ljust(32, "0")
FRESH_SECRET = "SENTINELfreshAPIkey".ljust(32, "0")
UPLOAD_SECRET = "SENTINEL_UPLOAD_SECRET"
UPLOAD_URL = LOCAL_API_BASE + "/local/uploads/" + UPLOAD_SECRET
FILE_ENDPOINT = "/api/users/0/items/" + ATTACHMENT + "/file"


class FakeOSBackend:
    def __init__(self):
        self.values = {}
        self.calls = []
        self.fail = None

    def _record(self, action, service, account):
        self.calls.append((action, service, account))
        if self.fail == action:
            raise RuntimeError(SECRET + " " + UPLOAD_URL)

    def get_password(self, service, account):
        self._record("get", service, account)
        return self.values.get((service, account))

    def set_password(self, service, account, key):
        self._record("set", service, account)
        self.values[service, account] = key

    def delete_password(self, service, account):
        self._record("delete", service, account)
        del self.values[service, account]

    def remember(self, key=SECRET, server=SERVER):
        self.values[credentials.SERVICE_NAME, server] = key


@pytest.fixture
def backend(monkeypatch):
    fake = FakeOSBackend()
    monkeypatch.setattr(credentials, "_os_backend", lambda: fake)
    return fake


@pytest.fixture
def local_file(tmp_path):
    path = tmp_path / "caller-owned.pdf"
    # A2 intentionally performs no acquisition/PDF-signature validation.
    path.write_bytes(b"caller supplied bytes\x00\xff\n")
    os.utime(path, ns=(1_700_000_000_123_456_789, 1_700_000_000_123_456_789))
    return path


def response(status=200, payload=None, *, server=SERVER, headers=None, content=None):
    combined = {"Zotero-Server-ID": server} if server is not None else {}
    combined.update(headers or {})
    if content is not None:
        return httpx.Response(status, content=content, headers=combined)
    return httpx.Response(status, json=payload, headers=combined)


def approved(remember=True, key=SECRET):
    return response(payload={"key": key, "remember": remember})


def created():
    return response(payload={
        "successful": {"0": {"key": ATTACHMENT, "data": {
            "key": ATTACHMENT, "itemType": "attachment", "parentItem": PARENT.key,
            "linkMode": "imported_file", "contentType": "application/pdf",
        }}}, "unchanged": {}, "failed": {},
    }, headers={"Last-Modified-Version": "5"})


def prepared(**changes):
    payload = {"uploadKey": UPLOAD_SECRET, "url": UPLOAD_URL,
               "contentType": "application/octet-stream", "prefix": "", "suffix": ""}
    payload.update(changes)
    return response(payload=payload)


def client_for(post_responses, *, get_responses=None, observer=None, parent=PARENT, authorization_runtime=None):
    posts = iter(post_responses)
    gets = iter(get_responses) if get_responses is not None else None
    requests = []

    def handler(request):
        requests.append(request)
        assert request.headers["Zotero-API-Version"] == "3"
        assert request.headers["Zotero-Server-ID"] == parent.server_id
        assert request.url.host == "localhost" and request.url.port == 23119
        if observer:
            observer(request)
        value = next(gets) if request.method == "GET" and gets is not None else (
            response() if request.method == "GET" else next(posts)
        )
        if isinstance(value, Exception):
            raise value
        return value

    return ZoteroWriteClient(parent, authorization_runtime=authorization_runtime, transport=httpx.MockTransport(handler)), requests


def posts(requests):
    return [r for r in requests if r.method == "POST"]


def assert_no_secrets(result, caplog=None):
    public = repr(result) + result.message + (caplog.text if caplog else "")
    for secret in (SECRET, FRESH_SECRET, UPLOAD_SECRET, UPLOAD_URL):
        assert secret not in public


@pytest.mark.parametrize("remember", [False, True])
def test_authorization_success(backend, remember):
    client, requests = client_for([approved(remember)])
    with client:
        result = client.authorize()
    assert result.outcome is Auth.AUTHORIZED
    assert result.remembered is remember
    assert result.server_id == SERVER
    auth_request = posts(requests)[0]
    assert auth_request.url.path == "/api/local/authorize"
    assert json.loads(auth_request.content) == {"appName": "Literature Monitor"}
    assert "Zotero-API-Key" not in auth_request.headers
    assert "Authorization" not in auth_request.headers
    assert backend.values == ({(credentials.SERVICE_NAME, SERVER): SECRET} if remember else {})
    assert_no_secrets(result)


@pytest.mark.parametrize("status,expected", [(403, Auth.DENIED), (500, Auth.API_FAILURE), (302, Auth.API_FAILURE)])
def test_authorization_failures_stop(backend, status, expected):
    client, requests = client_for([response(status, {"key": SECRET}, headers={"Location": UPLOAD_URL})])
    with client:
        result = client.authorize()
    assert result.outcome is expected
    assert len(posts(requests)) == 1
    assert backend.values == {}
    assert_no_secrets(result)


@pytest.mark.parametrize("retry_after,expected", [
    ("25", 25.0), ("0", 0.0), (None, None), ("", None), ("-2", None),
    ("NaN", None), ("Infinity", None), ("2.5", None), ("bad", None), ("9" * 400, None),
])
def test_authorization_rate_limit_no_prompt_loop(backend, retry_after, expected):
    headers = {} if retry_after is None else {"Retry-After": retry_after}
    client, requests = client_for([response(429, {"key": SECRET}, headers=headers)])
    with client:
        result = client.authorize()
    assert result.outcome is Auth.RATE_LIMITED and result.retryable
    assert result.retry_after_seconds == expected
    assert len(posts(requests)) == 1
    assert_no_secrets(result)


def test_authorization_rate_limit_http_date(backend):
    date = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=60), usegmt=True)
    client, requests = client_for([response(429, headers={"Retry-After": date})])
    with client:
        result = client.authorize()
    assert 58 <= result.retry_after_seconds <= 60
    assert len(posts(requests)) == 1


@pytest.mark.parametrize("payload", [
    {}, [], None, {"key": "short", "remember": False},
    {"key": SECRET + "\n", "remember": False}, {"key": "!" * 32, "remember": False},
    {"key": SECRET}, {"key": SECRET, "remember": 1}, {"key": SECRET, "remember": "true"},
])
def test_authorization_invalid_payload(backend, payload):
    client, requests = client_for([response(payload=payload)])
    with client:
        result = client.authorize()
    assert result.outcome is Auth.INVALID_RESPONSE
    assert len(posts(requests)) == 1 and backend.values == {}
    assert_no_secrets(result)


@pytest.mark.parametrize("bad", [
    response(content=b"{invalid SENTINEL_UPLOAD_SECRET"),
    httpx.ReadError(SECRET + UPLOAD_URL),
])
def test_authorization_malformed_json_or_transport(backend, bad):
    client, _ = client_for([bad])
    with client:
        result = client.authorize()
    assert result.outcome is (Auth.API_FAILURE if isinstance(bad, Exception) else Auth.INVALID_RESPONSE)
    assert_no_secrets(result)


@pytest.mark.parametrize("where", ["probe", "authorization"])
@pytest.mark.parametrize("server,status,expected", [
    (None, 200, Auth.INVALID_RESPONSE), ("", 200, Auth.INVALID_RESPONSE),
    (" \t", 412, Auth.INVALID_RESPONSE),
    (OTHER_SERVER, 200, Auth.SERVER_ID_MISMATCH),
    (OTHER_SERVER, 412, Auth.SERVER_ID_MISMATCH),
    (OTHER_SERVER, 401, Auth.SERVER_ID_MISMATCH),
    (SERVER, 412, Auth.API_FAILURE),
])
def test_authorization_instance_checks(backend, where, server, status, expected):
    bad = response(status, {"key": SECRET, "remember": True}, server=server)
    client, requests = client_for([bad] if where == "authorization" else [],
                                  get_responses=[bad] if where == "probe" else None)
    with client:
        result = client.authorize()
    assert result.outcome is expected
    assert len(posts(requests)) == (where == "authorization")
    assert not backend.calls


def test_credential_os_store_exact_instance_account(backend):
    store = ZoteroCredentialStore()
    store.save(SERVER, SECRET)
    assert store.load(SERVER) == SECRET
    assert store.load(OTHER_SERVER) is None
    store.delete(SERVER)
    assert store.load(SERVER) is None
    assert all(service == credentials.SERVICE_NAME for _, service, _ in backend.calls)
    assert {account for _, _, account in backend.calls} == {SERVER, OTHER_SERVER}


@pytest.mark.parametrize("action", ["get", "set", "delete"])
def test_credential_backend_errors_sanitized_without_files(backend, tmp_path, monkeypatch, action):
    monkeypatch.chdir(tmp_path)
    backend.remember()
    backend.fail = action
    store = ZoteroCredentialStore()
    with pytest.raises(ZoteroCredentialStoreError) as caught:
        {"get": lambda: store.load(SERVER), "set": lambda: store.save(SERVER, SECRET),
         "delete": lambda: store.delete(SERVER)}[action]()
    assert SECRET not in str(caught.value) and UPLOAD_URL not in str(caught.value)
    assert list(tmp_path.iterdir()) == []


def test_credential_unsupported_os_never_plaintext_fallback(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(credentials.sys, "platform", "unsupported")
    with pytest.raises(ZoteroCredentialStoreError):
        ZoteroCredentialStore().save(SERVER, SECRET)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("platform,module,class_name", [
    ("darwin", "keyring.backends.macOS", "Keyring"),
    ("win32", "keyring.backends.Windows", "WinVaultKeyring"),
    ("linux", "keyring.backends.SecretService", "Keyring"),
])
def test_credential_selects_os_backend_without_configured_fallback(monkeypatch, platform, module, class_name):
    import sys
    import types
    backend = FakeOSBackend()
    fake_module = types.ModuleType(module)
    setattr(fake_module, class_name, lambda: backend)
    monkeypatch.setitem(sys.modules, module, fake_module)
    monkeypatch.setattr(credentials.sys, "platform", platform)
    assert credentials._os_backend() is backend


@pytest.mark.parametrize("action", ["get", "set"])
def test_authorization_credential_store_failure_stops(backend, local_file, action):
    backend.fail = action
    client, requests = client_for([approved()])
    with client:
        if action == "set":
            result = client.authorize()
            assert result.outcome is Auth.SECURE_STORE_FAILURE and not result.remembered
        else:
            result = client.upload_pdf(local_file)
            assert result.outcome is Upload.AUTH_FAILURE
            assert result.authorization.outcome is Auth.SECURE_STORE_FAILURE
    assert len(posts(requests)) == (action == "set")
    assert_no_secrets(result)


def test_credential_server_change_never_loads_old_or_new_entry(backend, local_file):
    backend.remember()
    backend.remember(FRESH_SECRET, OTHER_SERVER)
    client, requests = client_for([], get_responses=[response(server=OTHER_SERVER)])
    with client:
        result = client.upload_pdf(local_file)
        again = client.upload_pdf(local_file)
    assert result.outcome is again.outcome is Upload.SERVER_ID_MISMATCH
    assert not backend.calls and not posts(requests)
    assert len(requests) == 1


def test_credential_current_server_only_loaded(backend, local_file):
    backend.remember(server=OTHER_SERVER)
    client, requests = client_for([])
    with client:
        result = client.upload_pdf(local_file)
    assert result.authorization.outcome is Auth.REQUIRED
    assert not posts(requests)
    assert all(account == SERVER for _, _, account in backend.calls)
    assert backend.values == {(credentials.SERVICE_NAME, OTHER_SERVER): SECRET}


def test_upload_complete_with_remembered_authorization(backend, local_file, monkeypatch):
    backend.remember()
    flow = [created(), prepared(), response(201), response(204)]
    observed = []
    def inspect(request):
        if request.url.path == "/api/users/0/items":
            observed.extend(json.loads(request.content))
    client, requests = client_for(flow, observer=inspect)
    original = local_file.read_bytes()
    request = client._request
    streamed = []
    def stream_request(method, url, **kwargs):
        if url == UPLOAD_URL:
            assert isinstance(kwargs['content'], Iterator)
            streamed.append(True)
        return request(method, url, **kwargs)
    monkeypatch.setattr(client, '_request', stream_request)
    def no_preload(path):
        raise AssertionError('Upload must stream the opened file.')
    with monkeypatch.context() as patch, client:
        patch.setattr(Path, 'read_bytes', no_preload)
        result = client.upload_pdf(local_file)
    assert streamed == [True]
    assert result.outcome is Upload.SUCCEEDED and result.stage is Stage.REGISTERED
    assert (result.parent_key, result.attachment_key, result.server_id) == (PARENT.key, ATTACHMENT, SERVER)
    assert not result.mutation_uncertain
    assert observed == [{
        "itemType": "attachment", "linkMode": "imported_file", "parentItem": PARENT.key,
        "contentType": "application/pdf", "url": "https://doi.org/10.1234/example",
        "filename": "article.pdf", "title": "Full text PDF", "tags": [], "collections": [], "relations": {},
    }]
    standard = [r for r in posts(requests) if r.url.path.startswith("/api/users/")]
    assert len(standard) == 3 and all(r.method == "POST" for r in standard)
    assert re_is_write_token(standard[0].headers["Zotero-Write-Token"])
    expected_form = {"md5": [hashlib.md5(original).hexdigest()], "filename": ["article.pdf"],
                     "filesize": [str(len(original))], "mtime": [str(local_file.stat().st_mtime_ns // 1_000_000)]}
    assert parse_qs(standard[1].content.decode()) == expected_form
    assert parse_qs(standard[2].content.decode()) == {"upload": [UPLOAD_SECRET]}
    assert standard[1].headers["If-None-Match"] == standard[2].headers["If-None-Match"] == "*"
    transfer = next(r for r in requests if r.url.path.startswith("/api/local/uploads/"))
    assert transfer.content == original
    assert transfer.headers['Content-Length'] == str(len(original))
    assert 'Transfer-Encoding' not in transfer.headers
    assert transfer.headers["Content-Type"] == "application/octet-stream"
    assert "Zotero-API-Key" not in transfer.headers and "Authorization" not in transfer.headers
    assert "Cookie" not in transfer.headers
    auth_posts = [r for r in posts(requests) if r.url.path == "/api/local/authorize"]
    assert len(auth_posts) == 0
    assert [r.headers["Zotero-API-Key"] for r in standard] == [SECRET] * 3
    assert not any(action == "set" for action, _, _ in backend.calls)
    assert local_file.read_bytes() == original
    assert_no_secrets(result)


def re_is_write_token(value):
    import re
    return bool(re.fullmatch(r"[0-9a-f]{32}", value))


def test_upload_exists_short_circuits_transfer_and_register(backend, local_file):
    backend.remember()
    client, requests = client_for([created(), response(payload={"exists": 1})])
    with client:
        result = client.upload_pdf(local_file)
    assert result.outcome is Upload.SUCCEEDED and result.stage is Stage.REGISTERED
    assert len(posts(requests)) == 2


def test_authorization_revoked_key_one_fresh_authorization_one_create_retry(backend, local_file):
    backend.remember()
    flow = [response(401), approved(True, FRESH_SECRET), created(), response(payload={"exists": 1})]
    client, requests = client_for(flow)
    with client:
        result = client.upload_pdf(local_file)
    assert result.outcome is Upload.SUCCEEDED
    creates = [r for r in posts(requests) if r.url.path == "/api/users/0/items"]
    assert len(creates) == 2
    assert creates[0].headers["Zotero-Write-Token"] == creates[1].headers["Zotero-Write-Token"]
    assert creates[0].content == creates[1].content
    assert [r.headers["Zotero-API-Key"] for r in creates] == [SECRET, FRESH_SECRET]
    assert backend.values[credentials.SERVICE_NAME, SERVER] == FRESH_SECRET
    assert sum(action == "delete" for action, _, _ in backend.calls) == 1
    assert len([r for r in posts(requests) if r.url.path == "/api/local/authorize"]) == 1


@pytest.mark.parametrize("next_response,expected_auth", [
    (response(401), None), (response(403), Auth.DENIED),
    (response(429, headers={"Retry-After": "30"}), Auth.RATE_LIMITED),
])
def test_authorization_401_retry_stops_at_second_failure(backend, local_file, next_response, expected_auth):
    backend.remember()
    flow = [response(401)] + ([approved(True, FRESH_SECRET), next_response] if expected_auth is None else [next_response])
    client, requests = client_for(flow)
    with client:
        result = client.upload_pdf(local_file)
    assert result.outcome is Upload.AUTH_FAILURE
    assert result.stage is Stage.NO_CONFIRMED_MUTATION and not result.mutation_uncertain
    assert len([r for r in posts(requests) if r.url.path == "/api/local/authorize"]) == 1
    assert len([r for r in posts(requests) if r.url.path == "/api/users/0/items"]) == (2 if expected_auth is None else 1)
    assert backend.values == {}
    if expected_auth:
        assert result.authorization.outcome is expected_auth
    assert_no_secrets(result)


def test_authorization_401_store_delete_failure_stops_without_prompt(backend, local_file):
    backend.remember()
    backend.fail = "delete"
    client, requests = client_for([response(401)])
    with client:
        result = client.upload_pdf(local_file)
    assert result.authorization.outcome is Auth.SECURE_STORE_FAILURE
    assert len(posts(requests)) == 1


def test_authorization_401_rechecks_instance_before_new_dialog(backend, local_file):
    backend.remember()
    client, requests = client_for([response(401)], get_responses=[response(), response(server=OTHER_SERVER)])
    with client:
        result = client.upload_pdf(local_file)
    assert result.outcome is Upload.SERVER_ID_MISMATCH
    assert len(posts(requests)) == 1 and backend.values == {}


@pytest.mark.parametrize("status,uncertain", [(400, False), (403, False), (409, False), (500, True), (302, False)])
def test_upload_create_http_failure_no_replay(backend, local_file, status, uncertain):
    backend.remember()
    client, requests = client_for([response(status, {"key": SECRET}, headers={"Location": UPLOAD_URL})])
    with client:
        result = client.upload_pdf(local_file)
    assert result.outcome is Upload.API_FAILURE
    assert result.stage is Stage.NO_CONFIRMED_MUTATION and result.attachment_key is None
    assert result.mutation_uncertain is uncertain and len(posts(requests)) == 1
    assert_no_secrets(result)


def test_upload_create_transport_failure_is_uncertain_no_replay(backend, local_file):
    backend.remember()
    client, requests = client_for([httpx.ReadError(SECRET + UPLOAD_URL)])
    with client:
        result = client.upload_pdf(local_file)
    assert result.outcome is Upload.API_FAILURE and result.mutation_uncertain
    assert result.stage is Stage.NO_CONFIRMED_MUTATION and len(posts(requests)) == 1
    assert_no_secrets(result)


@pytest.mark.parametrize("change", [
    "bad_json", "missing_successful", "legacy_success", "mixed_failed", "unchanged", "extra_success",
    "invalid_key", "parent_key", "wrong_type", "wrong_parent", "wrong_link_mode", "wrong_content_type", "inconsistent_key",
])
def test_upload_malformed_create_never_invents_attachment(backend, local_file, change):
    backend.remember()
    payload = created().json()
    item = payload["successful"]["0"]
    if change == "bad_json":
        value = response(content=("bad " + SECRET).encode())
    else:
        if change == "missing_successful": del payload["successful"]
        elif change == "legacy_success": payload = {"success": {"0": ATTACHMENT}, "unchanged": {}, "failed": {}}
        elif change == "mixed_failed": payload["failed"] = {"0": {"message": SECRET}}
        elif change == "unchanged": payload["unchanged"] = {"0": ATTACHMENT}
        elif change == "extra_success": payload["successful"]["1"] = item
        elif change == "invalid_key": item["key"] = "bad"
        elif change == "parent_key": item["key"] = item["data"]["key"] = PARENT.key
        elif change == "wrong_type": item["data"]["itemType"] = "journalArticle"
        elif change == "wrong_parent": item["data"]["parentItem"] = "OTHER234"
        elif change == "wrong_link_mode": item["data"]["linkMode"] = "linked_file"
        elif change == "wrong_content_type": item["data"]["contentType"] = "text/html"
        elif change == "inconsistent_key": item["data"]["key"] = "OTHER234"
        value = response(payload=payload)
    client, requests = client_for([value])
    with client:
        result = client.upload_pdf(local_file)
    assert result.outcome is Upload.INVALID_RESPONSE and result.mutation_uncertain
    assert result.attachment_key is None and len(posts(requests)) == 1
    assert_no_secrets(result)


@pytest.mark.parametrize("url", [
    "https://publisher.example/file.pdf", "http://localhost:23120/api/local/uploads/" + UPLOAD_SECRET,
    "file:///tmp/file.pdf", "https://localhost:23119/api/local/uploads/" + UPLOAD_SECRET,
    "http://127.0.0.1:23119/api/local/uploads/" + UPLOAD_SECRET,
    "http://localhost:23119/api/local/uploads/other-key",
    "http://localhost:23119/api/users/0/items/" + UPLOAD_SECRET,
    UPLOAD_URL + "?key=" + SECRET, UPLOAD_URL + "#fragment",
    "http://user:password@localhost:23119/api/local/uploads/" + UPLOAD_SECRET,
    UPLOAD_URL.replace("/local/", "/local/%2e%2e/local/"), UPLOAD_URL + "/",
])
def test_upload_rejects_arbitrary_endpoints_before_sending_bytes(backend, local_file, url):
    backend.remember()
    client, requests = client_for([created(), prepared(url=url)])
    with client:
        result = client.upload_pdf(local_file)
    assert result.outcome is Upload.PARTIAL_FAILURE and result.failure is Upload.INVALID_RESPONSE
    assert result.stage is Stage.CHILD_CREATED and result.attachment_key == ATTACHMENT
    assert len(posts(requests)) == 2
    assert_no_secrets(result)


@pytest.mark.parametrize("changes", [
    {"uploadKey": ""}, {"uploadKey": "bad/key"}, {"uploadKey": None}, {"url": None},
    {"contentType": ""}, {"contentType": "text/html\r\nX-Secret: secret"}, {"contentType": None},
    {"prefix": "prefix"}, {"prefix": None}, {"suffix": "suffix"}, {"suffix": None},
    {"exists": 1},
])
def test_upload_invalid_authorization_fields(backend, local_file, changes):
    backend.remember()
    client, requests = client_for([created(), prepared(**changes)])
    with client:
        result = client.upload_pdf(local_file)
    assert result.failure is Upload.INVALID_RESPONSE and result.stage is Stage.CHILD_CREATED
    assert len(posts(requests)) == 2


@pytest.mark.parametrize("payload", [{"exists": True}, {"exists": "1"}, {"exists": 0}, {}, [], None])
def test_upload_invalid_exists_payload(backend, local_file, payload):
    backend.remember()
    client, requests = client_for([created(), response(payload=payload)])
    with client:
        result = client.upload_pdf(local_file)
    assert result.outcome is Upload.PARTIAL_FAILURE and result.failure is Upload.INVALID_RESPONSE
    assert len(posts(requests)) == 2


@pytest.mark.parametrize("phase", ["create", "prepare", "bytes", "register"])
@pytest.mark.parametrize("server,status,expected", [
    (None, 200, Upload.INVALID_RESPONSE), ("", 200, Upload.INVALID_RESPONSE),
    (" \t", 412, Upload.INVALID_RESPONSE),
    (OTHER_SERVER, 200, Upload.SERVER_ID_MISMATCH),
    (OTHER_SERVER, 412, Upload.SERVER_ID_MISMATCH),
    (OTHER_SERVER, 401, Upload.SERVER_ID_MISMATCH),
    (SERVER, 412, Upload.API_FAILURE),
])
def test_upload_server_guard_at_each_phase(backend, local_file, phase, server, status, expected):
    backend.remember()
    normal = [created(), prepared(), response(201), response(204)]
    index = ["create", "prepare", "bytes", "register"].index(phase)
    value = normal[index]
    value.headers.pop("Zotero-Server-ID")
    if server is not None: value.headers["Zotero-Server-ID"] = server
    value.status_code = status
    client, requests = client_for(normal[:index + 1])
    with client:
        result = client.upload_pdf(local_file)
    assert result.failure is expected
    assert len(posts(requests)) == index + 1
    assert result.stage is [Stage.NO_CONFIRMED_MUTATION, Stage.CHILD_CREATED,
                            Stage.CHILD_CREATED, Stage.BYTES_UPLOADED][index]
    assert result.outcome is (expected if index == 0 else Upload.PARTIAL_FAILURE)
    assert_no_secrets(result)


@pytest.mark.parametrize("phase", ["authorization", "create", "prepare", "bytes", "register"])
def test_same_instance_412_allows_subsequent_guarded_operation(backend, local_file, phase):
    backend.remember()
    normal = [created(), prepared(), response(201), response(204)]
    index = ["create", "prepare", "bytes", "register"].index(phase) if phase != "authorization" else 0
    flow = ([] if phase == "authorization" else normal[:index]) + [response(412), approved()]
    client, requests = client_for(flow)
    with client:
        if phase == "authorization":
            failed = client.authorize()
            assert failed.outcome is Auth.API_FAILURE
        else:
            failed = client.upload_pdf(local_file)
            assert failed.failure is Upload.API_FAILURE
            assert failed.stage is [Stage.NO_CONFIRMED_MUTATION, Stage.CHILD_CREATED,
                                    Stage.CHILD_CREATED, Stage.BYTES_UPLOADED][index]
            assert not failed.mutation_uncertain
            assert failed.attachment_key == (None if phase == "create" else ATTACHMENT)
        # A new guarded authorization still reaches the same verified instance.
        subsequent = client.authorize()
    assert subsequent.outcome is Auth.AUTHORIZED
    assert len(posts(requests)) == len(flow)
    assert_no_secrets(failed)
    assert_no_secrets(subsequent)


def test_same_instance_create_412_allows_new_attempt_with_remembered_key(backend, local_file):
    backend.remember()
    client, requests = client_for([response(412), created(), response(payload={"exists": 1})])
    with client:
        first = client.upload_pdf(local_file)
        subsequent = client.upload_pdf(local_file)
    assert first.outcome is Upload.API_FAILURE
    assert first.stage is Stage.NO_CONFIRMED_MUTATION and not first.mutation_uncertain
    assert subsequent.outcome is Upload.SUCCEEDED
    assert subsequent.stage is Stage.REGISTERED
    assert [r.url.path for r in posts(requests)] == ["/api/users/0/items", "/api/users/0/items", FILE_ENDPOINT]
    assert all(r.headers["Zotero-API-Key"] == SECRET for r in posts(requests))
    assert not any(action in {"set", "delete"} for action, _, _ in backend.calls)


def test_different_instance_412_keeps_client_fail_closed(backend, local_file):
    backend.remember()
    client, requests = client_for([response(412, server=OTHER_SERVER)])
    with client:
        first = client.upload_pdf(local_file)
        subsequent = client.authorize()
    assert first.outcome is Upload.SERVER_ID_MISMATCH
    assert subsequent.outcome is Auth.SERVER_ID_MISMATCH
    assert len(requests) == 2  # Initial instance probe and rejected create only.
    assert len(posts(requests)) == 1


@pytest.mark.parametrize("phase", ["prepare", "bytes", "register"])
@pytest.mark.parametrize("bad", [response(500, {"secret": SECRET}), httpx.ReadError(SECRET + UPLOAD_URL)])
def test_upload_truthful_partial_failures(backend, local_file, phase, bad):
    backend.remember()
    index = ["create", "prepare", "bytes", "register"].index(phase)
    normal = [created(), prepared(), response(201), response(204)]
    client, requests = client_for(normal[:index] + [bad])
    with client:
        result = client.upload_pdf(local_file)
    assert result.outcome is Upload.PARTIAL_FAILURE and result.failure is Upload.API_FAILURE
    assert result.stage is (Stage.BYTES_UPLOADED if phase == "register" else Stage.CHILD_CREATED)
    assert result.attachment_key == ATTACHMENT and len(posts(requests)) == index + 1
    assert_no_secrets(result)


@pytest.mark.parametrize("status", [200, 204, 302, 307, 400])
def test_upload_transfer_requires_201_and_never_redirects(backend, local_file, status):
    backend.remember()
    client, requests = client_for([created(), prepared(), response(status, headers={"Location": "https://remote.example/"})])
    with client:
        result = client.upload_pdf(local_file)
    assert result.outcome is Upload.PARTIAL_FAILURE and result.stage is Stage.CHILD_CREATED
    assert len(posts(requests)) == 3


@pytest.mark.parametrize("status", [200, 201, 302, 400, 403])
def test_upload_register_requires_204(backend, local_file, status):
    backend.remember()
    client, requests = client_for([created(), prepared(), response(201), response(status)])
    with client:
        result = client.upload_pdf(local_file)
    assert result.outcome is Upload.PARTIAL_FAILURE and result.stage is Stage.BYTES_UPLOADED
    assert len(posts(requests)) == 4


@pytest.mark.parametrize("phase", ["prepare", "register"])
def test_upload_later_401_stops_without_authorization_or_replay(backend, local_file, phase):
    backend.remember()
    start = [created()] if phase == "prepare" else [created(), prepared(), response(201)]
    client, requests = client_for(start + [response(401)])
    with client:
        result = client.upload_pdf(local_file)
    assert result.outcome is Upload.PARTIAL_FAILURE and result.failure is Upload.AUTH_FAILURE
    assert result.stage is (Stage.CHILD_CREATED if phase == "prepare" else Stage.BYTES_UPLOADED)
    assert len(posts(requests)) == len(start) + 1
    assert all(r.url.path != "/api/local/authorize" for r in posts(requests))
    assert backend.values == {}


@pytest.mark.parametrize("failure", [response(400), response(401), httpx.ReadError(SECRET)])
def test_upload_one_time_consumed_after_failed_authenticated_write(backend, local_file, failure):
    client, requests = client_for([approved(False), failure])
    with client:
        assert client.authorize().outcome is Auth.AUTHORIZED
        first = client.upload_pdf(local_file)
        second = client.upload_pdf(local_file)
    assert first.failure in (Upload.API_FAILURE, Upload.AUTH_FAILURE)
    assert first.mutation_uncertain is isinstance(failure, httpx.HTTPError)
    assert not second.mutation_uncertain
    assert second.authorization.outcome is Auth.REQUIRED
    assert second.stage is Stage.NO_CONFIRMED_MUTATION
    assert len(posts(requests)) == 2 and backend.values == {}
    assert_no_secrets(first)
    assert_no_secrets(second)


def test_upload_secret_logs_and_cookies_do_not_escape(backend, local_file, caplog):
    backend.remember()
    caplog.set_level(logging.DEBUG)
    def observe(request):
        assert "Cookie" not in request.headers
        # Model HTTP debug loggers; an ordinary unrelated logger is unaffected.
        logging.getLogger("httpx").info("request %s key %s", request.url, SECRET)
        logging.getLogger("httpcore.http11").debug("headers %s", request.headers)
    prep = prepared()
    prep.headers["Set-Cookie"] = "session=" + SECRET
    client, _ = client_for([created(), prep, response(201), response(204)], observer=observe)
    with client:
        result = client.upload_pdf(local_file)
    logging.getLogger("httpx").info("unrelated request remains visible")
    assert "unrelated request remains visible" in caplog.text
    assert_no_secrets(result, caplog)


@pytest.mark.parametrize("phase", ["authorization", "create", "prepare", "bytes", "register"])
def test_upload_sanitizes_transport_secrets_at_every_phase(backend, local_file, caplog, phase):
    caplog.set_level(logging.DEBUG)
    backend.remember()
    normal = [created(), prepared(), response(201), response(204)]
    if phase == "authorization":
        backend.values.clear()
        flow = [httpx.ReadError(SECRET + UPLOAD_URL)]
    else:
        index = ["create", "prepare", "bytes", "register"].index(phase)
        flow = normal[:index] + [httpx.ReadError(SECRET + UPLOAD_URL)]
    client, _ = client_for(flow)
    with client:
        result = client.authorize() if phase == "authorization" else client.upload_pdf(local_file)
    assert result.outcome not in (Upload.SUCCEEDED, Auth.AUTHORIZED)
    assert_no_secrets(result, caplog)


@pytest.mark.parametrize("kind", ["missing", "empty", "directory"])
def test_upload_invalid_local_file_no_remote_mutation(backend, tmp_path, kind):
    path = tmp_path / "input"
    if kind == "empty": path.touch()
    if kind == "directory": path.mkdir()
    client, requests = client_for([])
    with client:
        result = client.upload_pdf(path)
    assert result.outcome is not Upload.SUCCEEDED and not result.mutation_uncertain
    assert result.stage is Stage.NO_CONFIRMED_MUTATION and not requests and not backend.calls


def test_authorization_remembered_save_failure_prevents_create(backend, local_file):
    backend.fail = "set"
    client, requests = client_for([approved(True)])
    with client:
        assert client.authorize().outcome is Auth.SECURE_STORE_FAILURE
        result = client.upload_pdf(local_file)
    assert result.outcome is Upload.AUTH_FAILURE
    assert result.authorization.outcome is Auth.SECURE_STORE_FAILURE
    assert not result.authorization.remembered
    assert result.stage is Stage.NO_CONFIRMED_MUTATION
    assert [r.url.path for r in posts(requests)] == ["/api/local/authorize"]
    assert_no_secrets(result)


@pytest.mark.parametrize("stored", ["short", "!" * 32, 42])
def test_credential_invalid_stored_key_never_sent_or_prompted(backend, local_file, stored):
    backend.remember(stored)
    client, requests = client_for([])
    with client:
        result = client.upload_pdf(local_file)
    assert result.authorization.outcome is Auth.SECURE_STORE_FAILURE
    assert not posts(requests)


@pytest.mark.parametrize("phase,successful_posts,probes", [
    ("prepare", [created()], 1),
    ("bytes", [created(), prepared()], 2),
    ("register", [created(), prepared(), response(201)], 3),
])
def test_upload_instance_probe_stops_before_next_phase(backend, local_file, phase, successful_posts, probes):
    backend.remember()
    client, requests = client_for(successful_posts,
                                  get_responses=[response()] * probes + [response(server=OTHER_SERVER)])
    with client:
        result = client.upload_pdf(local_file)
    assert result.outcome is Upload.PARTIAL_FAILURE and result.failure is Upload.SERVER_ID_MISMATCH
    assert result.stage is (Stage.BYTES_UPLOADED if phase == "register" else Stage.CHILD_CREATED)
    assert len(posts(requests)) == len(successful_posts)


def test_upload_one_time_shared_runtime_consumes_allow_and_preserves_partial_truth(backend, local_file):
    runtime = credentials.ZoteroAuthorizationRuntime()
    settings, auth_requests = client_for([approved(False)], authorization_runtime=runtime)
    with settings:
        assert settings.authorize().outcome is Auth.AUTHORIZED
    writer, requests = client_for([created()], authorization_runtime=runtime)
    with writer:
        first = writer.upload_pdf(local_file)
        second = writer.upload_pdf(local_file)
    assert first.outcome is Upload.PARTIAL_FAILURE and first.stage is Stage.CHILD_CREATED
    assert first.authorization.outcome is Auth.REQUIRED
    assert second.authorization.outcome is Auth.REQUIRED
    assert [r.url.path for r in posts(requests)] == ["/api/users/0/items"]
    assert len(posts(auth_requests)) == 1
    assert not any(action == "set" for action, _, _ in backend.calls)
    assert backend.values == {}


@pytest.mark.parametrize("size", [4 * 1024**3, 4 * 1024**3 + 1])
def test_upload_file_size_contract_rejected_before_hash_or_write(backend, tmp_path, size):
    path = tmp_path / "oversized.pdf"
    with path.open("wb") as file:
        file.truncate(size)
    client, requests = client_for([])
    with client:
        result = client.upload_pdf(path)
    assert result.outcome is Upload.INVALID_RESPONSE
    assert not requests and not backend.calls


@pytest.mark.parametrize('phase', ['create','prepare','register'])
def test_a5_guard_rejects_401_before_new_authorization(backend,local_file,phase):
    from literature_monitor.zotero_write import ZoteroWriteGuardPhase
    backend.remember()
    prefix=[] if phase=='create' else [created()] if phase=='prepare' else [created(),prepared(),response(201)]
    client,requests=client_for(prefix+[response(401)])
    contexts=[]
    def guard(context):
        contexts.append(context)
        return context.phase is not ZoteroWriteGuardPhase.BEFORE_AUTHORIZATION_RETRY
    with client:result=client.upload_pdf(local_file,guard=guard)
    assert result.failure is (Upload.GUARD_REJECTED if phase == "create" else Upload.AUTH_FAILURE)
    assert result.stage is {'create':Stage.NO_CONFIRMED_MUTATION,'prepare':Stage.CHILD_CREATED,'register':Stage.BYTES_UPLOADED}[phase]
    assert result.outcome is (Upload.GUARD_REJECTED if phase=='create' else Upload.PARTIAL_FAILURE)
    assert all(r.url.path!='/api/local/authorize' for r in posts(requests))
    context=contexts[-1]
    assert context.stage is result.stage and context.attachment_key==(None if phase=='create' else ATTACHMENT)
    assert_no_secrets(result)


def test_a5_guard_rechecks_after_fresh_authorization_before_replay(backend,local_file):
    from literature_monitor.zotero_write import ZoteroWriteGuardPhase
    backend.remember()
    authorized=False
    def observe(request):
        nonlocal authorized
        if request.url.path=='/api/local/authorize':authorized=True
    client,requests=client_for([response(401),approved(True,FRESH_SECRET)],observer=observe)
    def guard(context):
        return context.phase is ZoteroWriteGuardPhase.BEFORE_AUTHORIZATION_RETRY or not authorized
    with client:result=client.upload_pdf(local_file,guard=guard)
    assert result.outcome is Upload.GUARD_REJECTED
    assert [r.url.path for r in posts(requests)]==['/api/users/0/items','/api/local/authorize']


def test_a5_guard_rechecks_after_initial_authorization_before_create(backend,local_file):
    client,requests=client_for([approved(True)])
    def guard(context):return not posts(requests)
    with client:
        assert client.authorize().outcome is Auth.AUTHORIZED
        result=client.upload_pdf(local_file,guard=guard)
    assert result.outcome is Upload.GUARD_REJECTED and len(posts(requests))==1
    assert posts(requests)[0].url.path=='/api/local/authorize'


def test_a5_guard_false_prevents_any_authorization_or_write(backend,local_file):
    client,requests=client_for([])
    with client:result=client.upload_pdf(local_file,guard=lambda context:False)
    assert result.outcome is Upload.GUARD_REJECTED and requests==[]


def test_a5_guard_exception_is_sanitized_and_fail_closed(backend,local_file,caplog):
    client,requests=client_for([])
    def guard(context):raise RuntimeError(SECRET+' '+UPLOAD_URL)
    with client:result=client.upload_pdf(local_file,guard=guard)
    assert result.outcome is Upload.GUARD_REJECTED and requests==[]
    assert_no_secrets(result,caplog)


def test_a5_guard_applies_before_upload_key_bytes_write(backend,local_file):
    backend.remember()
    client,requests=client_for([created(),prepared()])
    def guard(context):return len(posts(requests))<2
    with client:result=client.upload_pdf(local_file,guard=guard)
    assert result.outcome is Upload.PARTIAL_FAILURE and result.failure is Upload.GUARD_REJECTED
    assert result.stage is Stage.CHILD_CREATED and len(posts(requests))==2
    assert all(r.url.path!= '/api/local/uploads/'+UPLOAD_SECRET for r in posts(requests))


def test_one_time_allow_does_not_access_unavailable_os_store(backend, local_file):
    backend.fail = 'get'
    runtime = credentials.ZoteroAuthorizationRuntime()
    settings, _ = client_for([approved(False)], authorization_runtime=runtime)
    with settings:
        assert settings.authorize().outcome is Auth.AUTHORIZED
    writer, requests = client_for([created()], authorization_runtime=runtime)
    with writer:
        result = writer.upload_pdf(local_file)
    assert result.stage is Stage.CHILD_CREATED
    assert len(posts(requests)) == 1
    assert result.authorization.outcome is Auth.SECURE_STORE_FAILURE
    assert not any(action == 'set' for action, _, _ in backend.calls)
    assert backend.values == {}


def test_one_time_instance_observation_invalidates_cached_client_key(backend, local_file):
    runtime = credentials.ZoteroAuthorizationRuntime()
    client, requests = client_for([approved(False)], authorization_runtime=runtime)
    with client:
        assert client.authorize().outcome is Auth.AUTHORIZED
        runtime.observe_instance(OTHER_SERVER)
        # Returning to A does not resurrect its old process Allow.
        result = client.upload_pdf(local_file)
    assert result.authorization.outcome is Auth.REQUIRED
    assert [r.url.path for r in posts(requests)] == ['/api/local/authorize']


def test_remembered_401_refresh_to_one_time_allow_replays_once_then_stops(backend, local_file):
    backend.remember()
    client, requests = client_for([response(401), approved(False, FRESH_SECRET), created()])
    with client:
        result = client.upload_pdf(local_file)
    assert result.outcome is Upload.PARTIAL_FAILURE and result.stage is Stage.CHILD_CREATED
    assert result.authorization.outcome is Auth.REQUIRED
    assert [r.url.path for r in posts(requests)] == [
        '/api/users/0/items', '/api/local/authorize', '/api/users/0/items']
    assert backend.values == {}
    assert_no_secrets(result)
