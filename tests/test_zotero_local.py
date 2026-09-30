from __future__ import annotations

import httpx
import pytest

from literature_monitor.zotero_local import (
    LOCAL_API_BASE,
    VerifiedZoteroItem,
    ZoteroLocalClient,
    ZoteroReadOutcome,
)


DOI = "10.5555/paper"
PARENT = "PARENT01"
SERVER = "local-instance"


def item(key=PARENT, *, doi=DOI, item_type="journalArticle", **metadata):
    return {
        "key": key,
        "data": {"key": key, "itemType": item_type, "DOI": doi, **metadata},
    }


def response(payload, *, server=SERVER, status=200, version="4", **headers):
    if server is not None:
        headers["Zotero-Server-ID"] = server
    if version is not None:
        headers.setdefault("Last-Modified-Version", version)
    if isinstance(payload, bytes):
        return httpx.Response(status, content=payload, headers=headers)
    return httpx.Response(status, json=payload, headers=headers)


def page(items, *, total=None, next_start=None, path="/users/0/items", server=SERVER, version="4", **headers):
    headers["Total-Results"] = str(len(items) if total is None else total)
    if next_start is not None:
        query = f"format=json&include=data&limit=100&start={next_start}"
        if path == "/users/0/items":
            query += "&itemType=-attachment"
        headers["Link"] = f'<{LOCAL_API_BASE}{path}?{query}>; rel="next"'
    return response(items, server=server, version=version, **headers)


class ReadTransport(httpx.MockTransport):
    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests = []
        self.closed = False
        super().__init__(self.respond)

    def respond(self, request):
        self.requests.append(request)
        assert request.method == "GET"
        assert str(request.url).startswith(LOCAL_API_BASE + "/users/0/items")
        assert request.headers["Zotero-API-Version"] == "3"
        assert "Authorization" not in request.headers
        assert "Zotero-API-Key" not in request.headers
        assert self.replies, f"Unexpected request: {request.url}"
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def close(self):
        self.closed = True
        super().close()


def resolve(*replies, key=None, doi=DOI):
    transport = ReadTransport(*replies)
    with ZoteroLocalClient(transport=transport) as client:
        result = client.resolve_identity(doi, key)
    assert transport.closed
    assert transport.replies == []
    return result, transport.requests


def first_page(*matches):
    unrelated = [item(f"ITEM{i:04d}", doi=f"10.5555/other-{i}") for i in range(100 - len(matches))]
    return [*matches, *unrelated]


def test_existing_key_is_read_and_verified_by_normalized_doi():
    result, requests = resolve(response(item(doi=" HTTPS://DOI.ORG/10.5555/PAPER ")), key=PARENT)
    assert result.outcome is ZoteroReadOutcome.VERIFIED
    assert result.item == VerifiedZoteroItem(PARENT, DOI, SERVER)
    assert len(requests) == 1
    assert requests[0].url.path == "/api/users/0/items/PARENT01"


@pytest.mark.parametrize("old", [
    response(None, status=404),
    response(item(item_type="attachment")),
    response(item(item_type="note")),
    response(item(item_type="annotation")),
    response(item(doi="10.5555/wrong")),
    response(item(doi=None)),
    response(item(doi="")),
    response(item(doi=["bad DOI"])),
    response({"key": PARENT, "data": {"itemType": "journalArticle"}}),
])
def test_unverified_existing_key_uses_complete_exact_doi_fallback(old):
    result, requests = resolve(old, page([item("MATCH001")]), key=PARENT)
    assert result.outcome is ZoteroReadOutcome.VERIFIED
    assert result.item.key == "MATCH001"
    assert len(requests) == 2
    assert requests[1].url.path == "/api/users/0/items"
    assert requests[1].url.params["itemType"] == "-attachment"
    assert requests[1].headers["Zotero-Server-ID"] == SERVER


@pytest.mark.parametrize("key", [None, "stale", "../../local/authorize"])
def test_missing_or_unusable_key_does_not_control_request_path(key):
    result, requests = resolve(page([item()]), key=key)
    assert result.outcome is ZoteroReadOutcome.VERIFIED
    assert len(requests) == 1
    assert requests[0].url.path == "/api/users/0/items"


def test_fallback_zero_matches_ignores_non_bibliographic_and_unusable_dois():
    entries = [
        item("NOMATCH1", doi="10.5555/other"),
        item("MISSING1", doi=None),
        item("BADDOI01", doi={"DOI": DOI}),
        item("NOTEPDF1", item_type="note"),
        item("ATTACH01", item_type="attachment"),
    ]
    result, _ = resolve(page(entries))
    assert result.outcome is ZoteroReadOutcome.NOT_FOUND
    assert result.item is None


def test_fallback_accepts_only_one_exact_match_after_all_items():
    result, _ = resolve(page([item("OTHER001", doi="10.5555/other"), item(doi=DOI.upper())]))
    assert result.item == VerifiedZoteroItem(PARENT, DOI, SERVER)


def test_multiple_exact_matches_never_select_a_parent():
    result, _ = resolve(page([item(), item("MATCH002")]))
    assert result.outcome is ZoteroReadOutcome.DUPLICATE
    assert result.item is None
    assert "duplicates" in result.message


def test_match_on_later_page_and_server_id_are_retained():
    result, requests = resolve(
        page(first_page(), total=101, next_start=100),
        page([item()], total=101), doi="https://doi.org/10.5555/PAPER",
    )
    assert result.item == VerifiedZoteroItem(PARENT, DOI, SERVER)
    assert requests[1].url.params["start"] == "100"
    assert requests[1].headers["Zotero-Server-ID"] == SERVER


def test_exact_matches_on_different_pages_are_duplicates():
    result, requests = resolve(
        page(first_page(item()), total=101, next_start=100),
        page([item("MATCH002")], total=101),
    )
    assert len(requests) == 2
    assert result.outcome is ZoteroReadOutcome.DUPLICATE
    assert result.item is None


@pytest.mark.parametrize("later", [
    response({"error": "SECRET"}, status=500),
    response(b"SECRET not JSON"),
    page([item("MATCH002")], total=101, server="different-instance"),
    page([item("MATCH002")], total=102),
])
def test_later_page_failure_never_accepts_a_partial_unique_match(later):
    result, _ = resolve(page(first_page(item()), total=101, next_start=100), later)
    assert result.outcome in {
        ZoteroReadOutcome.API_FAILURE,
        ZoteroReadOutcome.INVALID_RESPONSE,
        ZoteroReadOutcome.SERVER_ID_MISMATCH,
    }
    assert result.item is None
    assert "SECRET" not in result.message


@pytest.mark.parametrize("reply", [
    response({"items": [item()]}, **{"Total-Results": "1"}),
    response([item()]),
    response([item()], **{"Total-Results": "unknown"}),
    response([item()], **{"Total-Results": "2"}),
    response([item()], **{"Total-Results": "0"}),
    response([{"key": PARENT}], **{"Total-Results": "1"}),
    response([item(), item()], **{"Total-Results": "2"}),
    response(b"SECRET", **{"Total-Results": "1"}),
])
def test_unreadable_or_incomplete_enumeration_is_explicit_failure(reply):
    result, _ = resolve(reply)
    assert result.outcome is ZoteroReadOutcome.INVALID_RESPONSE
    assert result.item is None
    assert "SECRET" not in result.message


@pytest.mark.parametrize("link", [
    '<https://api.zotero.org/users/0/items?start=100>; rel="next"',
    f'<{LOCAL_API_BASE}/groups/1/items?start=100>; rel="next"',
    f'<{LOCAL_API_BASE}/users/0/items?format=json&include=data&limit=100&start=0&itemType=-attachment>; rel="next"',
    f'<{LOCAL_API_BASE}/users/0/items?format=json&include=data&limit=100&start=100&itemType=book>; rel="next"',
])
def test_pagination_cannot_change_origin_library_filter_or_repeat_a_page(link):
    result, requests = resolve(page(first_page(item()), total=101, Link=link))
    assert result.outcome is ZoteroReadOutcome.INVALID_RESPONSE
    assert result.item is None
    assert len(requests) == 1


def test_library_version_change_does_not_establish_uniqueness():
    result, _ = resolve(
        page(first_page(item()), total=101, next_start=100, **{"Last-Modified-Version": "4"}),
        page([item("OTHER001", doi="10.5555/other")], total=101, **{"Last-Modified-Version": "5"}),
    )
    assert result.outcome is ZoteroReadOutcome.INVALID_RESPONSE
    assert result.item is None


@pytest.mark.parametrize("version", [None, "", " ", "-1", "+1", "1.5", "1e2", "0x4", "4,4", "SECRET"])
def test_first_page_requires_a_usable_library_version(version):
    result, _ = resolve(page([item()], version=version))
    assert result.outcome is ZoteroReadOutcome.INVALID_RESPONSE
    assert result.item is None
    assert "SECRET" not in result.message


def test_empty_enumeration_without_version_is_not_no_match_evidence():
    result, _ = resolve(page([], version=None))
    assert result.outcome is ZoteroReadOutcome.INVALID_RESPONSE
    assert result.item is None


@pytest.mark.parametrize("matches", [(), (item(),), (item(), item("MATCH002"))])
@pytest.mark.parametrize("version", [None, "malformed", "5"])
def test_unstable_later_version_cannot_establish_zero_one_or_multiple_matches(matches, version):
    result, _ = resolve(
        page(first_page(*matches), total=101, next_start=100),
        page([item("OTHER001", doi="10.5555/other")], total=101, version=version),
    )
    assert result.outcome is ZoteroReadOutcome.INVALID_RESPONSE
    assert result.item is None


@pytest.mark.parametrize("version", ["0", "4", "12345678901234567890"])
def test_stable_decimal_library_version_allows_complete_exact_match(version):
    result, requests = resolve(
        page(first_page(), total=101, next_start=100, version=version),
        page([item()], total=101, version=version),
    )
    assert result.item == VerifiedZoteroItem(PARENT, DOI, SERVER)
    assert len(requests) == 2


@pytest.mark.parametrize("reply", [response(item(), server=None), response(item(), server=" ")])
def test_missing_server_id_prevents_existing_key_verification(reply):
    result, _ = resolve(reply, key=PARENT)
    assert result.outcome is ZoteroReadOutcome.INVALID_RESPONSE
    assert result.item is None
    assert "Server-ID" in result.message


def test_server_id_change_between_key_and_fallback_is_failure():
    result, _ = resolve(response(None, status=404), page([item()], server="new-server"), key=PARENT)
    assert result.outcome is ZoteroReadOutcome.SERVER_ID_MISMATCH
    assert result.item is None


def test_server_precondition_failure_is_instance_mismatch():
    result, _ = resolve(response(None, status=404), response(None, status=412), key=PARENT)
    assert result.outcome is ZoteroReadOutcome.SERVER_ID_MISMATCH


@pytest.mark.parametrize("reply", [
    httpx.ConnectError("SECRET transport details"),
    httpx.ReadTimeout("SECRET timeout details"),
    response({"error": "SECRET"}, status=403),
    response({"error": "SECRET"}, status=500),
    response(None, status=302, Location="https://example.com/SECRET"),
])
def test_api_unavailable_disabled_or_failed_is_sanitized(reply):
    result, requests = resolve(reply)
    assert result.outcome is ZoteroReadOutcome.API_FAILURE
    assert result.item is None
    assert "SECRET" not in result.message
    assert len(requests) == 1


@pytest.mark.parametrize("doi", [None, "", "https://doi.org/", [DOI]])
def test_invalid_input_doi_performs_no_network_read(doi):
    result, requests = resolve(doi=doi)
    assert result.outcome is ZoteroReadOutcome.INVALID_DOI
    assert result.item is None
    assert requests == []


@pytest.mark.parametrize("payload", [b"SECRET", [], {"data": {}}, item("OTHER001")])
def test_unreliable_existing_key_response_is_not_used_as_identity(payload):
    result, _ = resolve(response(payload), key=PARENT)
    assert result.outcome is ZoteroReadOutcome.INVALID_RESPONSE
    assert result.item is None
    assert "SECRET" not in result.message


def attachment(key="PDF00001", content_type="application/pdf", **metadata):
    return item(key, item_type="attachment", contentType=content_type, parentItem=PARENT, **metadata)


def inspect(*replies):
    transport = ReadTransport(*replies)
    with ZoteroLocalClient(transport=transport) as client:
        result = client.inspect_attachments(VerifiedZoteroItem(PARENT, DOI, SERVER))
    assert transport.replies == []
    assert all(request.headers["Zotero-Server-ID"] == SERVER for request in transport.requests)
    return result, transport.requests


CHILD_PATH = f"/users/0/items/{PARENT}/children"


def stored_file(**headers):
    return response(None, status=302, Location="file:///private/SENTINEL_FILE_PATH/paper.pdf", **headers)


@pytest.mark.parametrize("children,has_pdf", [
    ([], False),
    ([attachment(content_type="text/html", filename="looks-like.pdf")], False),
    ([attachment()], True),
    ([item("NOTE0001", item_type="note", parentItem=PARENT), attachment("HTML0001", "text/html"), attachment()], True),
])
def test_pdf_existence_requires_current_metadata_and_confirmed_file(children, has_pdf):
    result, requests = inspect(page(children, path=CHILD_PATH), *([stored_file()] if has_pdf else []))
    assert result.outcome is ZoteroReadOutcome.CHECKED
    assert result.has_pdf is has_pdf
    assert len(requests) == (2 if has_pdf else 1)
    assert requests[0].url.path == "/api" + CHILD_PATH


def test_child_pagination_is_completed_even_after_finding_pdf():
    children = [attachment(f"FILE{i:04d}", "application/pdf" if i == 0 else "text/html") for i in range(100)]
    result, requests = inspect(
        page(children, total=101, next_start=100, path=CHILD_PATH, version="9"),
        page([attachment("LAST0001", "text/html")], total=101, path=CHILD_PATH, version="9"),
        stored_file(),
    )
    assert result.outcome is ZoteroReadOutcome.CHECKED
    assert result.has_pdf is True
    assert len(requests) == 3
    assert requests[-1].url.path == "/api/users/0/items/FILE0000/file"


@pytest.mark.parametrize("version", [None, "malformed"])
def test_child_first_page_requires_a_usable_library_version(version):
    result, _ = inspect(page([attachment()], path=CHILD_PATH, version=version))
    assert result.outcome is ZoteroReadOutcome.INVALID_RESPONSE
    assert result.has_pdf is None


@pytest.mark.parametrize("version", [None, "malformed", "5"])
def test_child_later_version_failure_leaves_pdf_existence_unknown(version):
    children = [attachment(f"FILE{i:04d}", "application/pdf" if i == 0 else "text/html") for i in range(100)]
    result, _ = inspect(
        page(children, total=101, next_start=100, path=CHILD_PATH),
        page([attachment("LAST0001", "text/html")], total=101, path=CHILD_PATH, version=version),
    )
    assert result.outcome is ZoteroReadOutcome.INVALID_RESPONSE
    assert result.has_pdf is None


@pytest.mark.parametrize("reply,outcome", [
    (response(None, status=500), ZoteroReadOutcome.API_FAILURE),
    (page([], path=CHILD_PATH, server="other-server"), ZoteroReadOutcome.SERVER_ID_MISMATCH),
    (page([], path=CHILD_PATH, server=None), ZoteroReadOutcome.INVALID_RESPONSE),
    (response(b"SECRET", **{"Total-Results": "0"}), ZoteroReadOutcome.INVALID_RESPONSE),
    (page([item()], path=CHILD_PATH), ZoteroReadOutcome.INVALID_RESPONSE),
    (page([item("ATTACH01", item_type="attachment", parentItem=PARENT)], path=CHILD_PATH), ZoteroReadOutcome.INVALID_RESPONSE),
])
def test_child_read_failure_is_unknown_pdf_existence(reply, outcome):
    result, _ = inspect(reply)
    assert result.outcome is outcome
    assert result.has_pdf is None
    assert "SECRET" not in result.message


def test_complete_attachment_result_exposes_ordered_pdf_child_keys():
    children=[attachment(f'FILE{i:04d}', 'application/pdf' if i in {0,99} else 'text/html') for i in range(100)]
    result,_=inspect(page(children,total=101,next_start=100,path=CHILD_PATH),page([attachment('LAST0001')],total=101,path=CHILD_PATH),
        stored_file(), response(None,status=404), stored_file())
    assert result.outcome is ZoteroReadOutcome.CHECKED and result.has_pdf is True
    assert result.pdf_keys==('FILE0000','FILE0099','LAST0001')
    assert result.pdf_file_keys==('FILE0000','LAST0001')


def test_failed_attachment_enumeration_never_exposes_partial_pdf_keys():
    children=[attachment(f'FILE{i:04d}') for i in range(100)]
    result,_=inspect(page(children,total=101,next_start=100,path=CHILD_PATH),page([attachment('LAST0001')],total=101,path=CHILD_PATH,version='5'))
    assert result.has_pdf is None and result.pdf_keys==()
    assert result.pdf_file_keys==()


@pytest.mark.parametrize("missing", [response(None, status=404), response(None, status=302, Location="false")])
def test_metadata_only_pdf_child_is_not_a_stored_pdf(missing):
    result, requests = inspect(page([attachment()], path=CHILD_PATH), missing)
    assert result.outcome is ZoteroReadOutcome.CHECKED
    assert result.has_pdf is False and result.pdf_keys == ("PDF00001",)
    assert result.pdf_file_keys == ()
    assert len(requests) == 2


@pytest.mark.parametrize("reply,outcome", [
    (httpx.ReadTimeout("SENTINEL_FILE_PATH"), ZoteroReadOutcome.API_FAILURE),
    (response(None, status=500), ZoteroReadOutcome.API_FAILURE),
    (stored_file(server="another-instance"), ZoteroReadOutcome.SERVER_ID_MISMATCH),
    (response(None, status=404, server="another-instance"), ZoteroReadOutcome.SERVER_ID_MISMATCH),
    (stored_file(server=None), ZoteroReadOutcome.INVALID_RESPONSE),
    (stored_file(server=" "), ZoteroReadOutcome.INVALID_RESPONSE),
    (response(None, status=302), ZoteroReadOutcome.INVALID_RESPONSE),
    (response(None, status=302, Location="https://example.com/SENTINEL_FILE_PATH"), ZoteroReadOutcome.INVALID_RESPONSE),
    (response(None, status=302, Location="file://remote/SENTINEL_FILE_PATH"), ZoteroReadOutcome.INVALID_RESPONSE),
    (response(None, status=302, Location="file:relative/SENTINEL_FILE_PATH"), ZoteroReadOutcome.INVALID_RESPONSE),
    (response(None, status=302, Location="file:///"), ZoteroReadOutcome.INVALID_RESPONSE),
    (response(None, status=302, Location="file:///private/SENTINEL_FILE_PATH?secret=x"), ZoteroReadOutcome.INVALID_RESPONSE),
    (response(None, status=200), ZoteroReadOutcome.INVALID_RESPONSE),
])
def test_unreliable_file_state_is_unknown_and_sanitized(reply, outcome):
    result, requests = inspect(page([attachment()], path=CHILD_PATH), reply)
    assert result.outcome is outcome and result.has_pdf is None
    assert result.pdf_file_keys == ()
    assert "SENTINEL_FILE_PATH" not in repr(result)
    assert len(requests) == 2


def test_file_read_failure_after_confirmed_file_is_still_unknown():
    result, _ = inspect(page([attachment(), attachment("PDF00002")], path=CHILD_PATH),
        stored_file(), response(None, status=500))
    assert result.outcome is ZoteroReadOutcome.API_FAILURE and result.has_pdf is None
    assert result.pdf_file_keys == ()


def test_file_location_is_not_followed_exposed_or_logged(caplog):
    import logging

    transport = ReadTransport(page([attachment()], path=CHILD_PATH))
    respond = transport.respond
    def handler(request):
        if request.url.path.endswith("/file"):
            logging.getLogger("httpcore.http11").debug("Location: %s", "SENTINEL_FILE_PATH")
            return stored_file()
        return respond(request)
    with caplog.at_level(logging.DEBUG), ZoteroLocalClient(transport=httpx.MockTransport(handler)) as client:
        result = client.inspect_attachments(VerifiedZoteroItem(PARENT, DOI, SERVER))
    assert result.has_pdf is True
    assert "SENTINEL_FILE_PATH" not in caplog.text + repr(result)
