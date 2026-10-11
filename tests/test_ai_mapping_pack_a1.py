"""A1: verified CARSI inline directory, byte-exact Pack and Import refusal."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient

from literature_monitor.application.ai_mapping_pack import (
    AI_EDITING_PROMPT_V1, AI_MAPPING_PACK_FILENAME, CARSI_DIRECTORY_URL,
    MAX_CARSI_HTML_BYTES, CarsiResource, MappingPackError,
    build_ai_mapping_pack, fetch_carsi_directory, generate_ai_mapping_pack,
    parse_carsi_directory,
)
from literature_monitor.application.full_markdown_import import (
    AI_MAPPING_PACK_MARKER, confirm_full_markdown, preview_full_markdown,
)
from literature_monitor.config import ConfigurationError
from literature_monitor.web.app import create_app
from test_access_mapping_a4 import prepared, stamp
from test_full_markdown_import_a5 import candidate, preview, submission


RECORDS = [
    {
        "production": "中国·统计与 Markdown | 资源 `示例`",
        "vendor_name": "出版方\u00a0[甲] & Co.",
        "url": "https://catalog.example/resource?lang=zh&group=a",
        "searchStr": "not exported", "step": "not exported",
    },
    {
        "production": "Springer Nature",
        "vendor_name": "Springer Nature GmbH",
        "url": "https://link.springer.com",
        "univ": "not exported",
    },
]


def official_html(records=RECORDS) -> bytes:
    """The actual CARSI page embeds JSON in: <script>var SPList=[...];var vendornum...</script>."""
    data = json.dumps(records, ensure_ascii=False)
    return (
        '<!doctype html><html><head><meta charset="utf-8"></head>'
        '<body><table><tbody id="splist"></tbody></table>'
        f'<script>var SPList={data};var vendornum=2;var prodnum=2</script>'
        '<script>showlist(1,false);</script></body></html>'
    ).encode("utf-8")


def transport(body=None, status=200, headers=None, calls=None):
    def respond(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append((str(request.url), request.headers.get("user-agent")))
        return httpx.Response(status, content=official_html() if body is None else body,
                              headers={"content-type": "text/html", **(headers or {})})
    return httpx.MockTransport(respond)


def test_verified_inline_splist_exact_fields_and_unicode():
    services = parse_carsi_directory(official_html().decode("utf-8"))
    assert services == (
        CarsiResource(RECORDS[0]["production"], RECORDS[0]["vendor_name"], RECORDS[0]["url"]),
        CarsiResource(RECORDS[1]["production"], RECORDS[1]["vendor_name"], RECORDS[1]["url"]),
    )
    assert all(len(vars(resource)) == 3 for resource in services)


def test_official_schemeless_url_remains_exact_and_is_not_upgraded():
    # Verified live CARSI SPList includes scheme-less Web of Science URL.
    record = dict(RECORDS[0], production="Web of Science", url="www.webofscience.com")
    saved = parse_carsi_directory(official_html([record]).decode("utf-8"))
    assert saved[0].url == "www.webofscience.com"
    pack = build_ai_mapping_pack("## Human\n", saved)
    assert '"url": "www.webofscience.com"' in pack.markdown
    assert "https://www.webofscience.com" not in pack.markdown
    assert "never invent a scheme" in pack.markdown


def test_fresh_request_each_call_and_only_official_url():
    calls = []
    first = fetch_carsi_directory(transport=transport(calls=calls))
    second = fetch_carsi_directory(transport=transport(
        body=official_html([dict(RECORDS[0], production="New resource")]), calls=calls,
    ))
    assert first != second
    assert second[0].production == "New resource"
    assert [url for url, _ in calls] == [CARSI_DIRECTORY_URL, CARSI_DIRECTORY_URL]
    assert all(agent.startswith("literature-monitor/") for _, agent in calls)


@pytest.mark.parametrize("body", [
    b"not HTML", b"<html></html>",
    b"<script>var SPList=</script>",
    b"<script>var SPList={};</script>",
    b"<script>var SPList=[];</script>",
    b"<script>var SPList=javascriptIsNotJSON();</script>",
    b"<script>var SPList=[]</script>",
    official_html([{"production": "Name", "vendor_name": "Vendor"}]),
    official_html([{"production": "Name", "vendor_name": "Vendor", "url": ""}]),
    official_html([{"production": "Name", "vendor_name": 123, "url": "https://example.org"}]),
    official_html([{"production": "Name", "vendor_name": "Vendor", "url": "file:///private"}]),
    official_html([{"production": "Name", "vendor_name": "Vendor", "url": "https://user:pwd@example.org"}]),
    official_html([{"production": "Name", "vendor_name": "Vendor", "url": "www.example.org\nother"}]),
    official_html(RECORDS) + b"<script>var SPList=[];</script>",
    b"<script>var SPList=\xff;</script>",
])
def test_malformed_or_missing_official_resource_data_fails_closed(body):
    with pytest.raises(MappingPackError):
        fetch_carsi_directory(transport=transport(body=body))


@pytest.mark.parametrize("status", [302, 307, 400, 403, 500])
def test_non_success_redirect_and_http_error_fails(status):
    calls = []
    with pytest.raises(MappingPackError, match="redirected" if status < 400 else str(status)):
        fetch_carsi_directory(transport=transport(
            status=status, headers={"Location": "https://untrusted.example/auth"}, calls=calls,
        ))
    assert len(calls) == 1


@pytest.mark.parametrize("headers,body", [
    ({"Content-Type": "application/json"}, official_html()),
    ({"Content-Length": str(MAX_CARSI_HTML_BYTES + 1)}, official_html()),
    ({}, b"<html>" + b"a" * MAX_CARSI_HTML_BYTES + b"</html>"),
])
def test_wrong_mime_or_oversize_response_fails(headers, body):
    with pytest.raises(MappingPackError):
        fetch_carsi_directory(transport=transport(headers=headers, body=body))


def test_timeout_and_network_error_are_sanitized():
    def timeout(_request):
        raise httpx.ReadTimeout("secret private address", request=_request)
    with pytest.raises(MappingPackError, match="timed out") as exc:
        fetch_carsi_directory(transport=httpx.MockTransport(timeout))
    assert "secret private address" not in str(exc.value)

    def network(_request):
        raise httpx.ConnectError("private token", request=_request)
    with pytest.raises(MappingPackError, match="request failed") as exc:
        fetch_carsi_directory(transport=httpx.MockTransport(network))
    assert "private token" not in str(exc.value)


def test_pack_marker_prompt_original_bytes_fences_and_no_extraneous_fields():
    saved = (
        "# Preserved human note\r\n"
        "## Journals\r\n\r\n| Journal | ISSN-L | Publisher ID | Group |\r\n"
        "## Conferences\r\n\r\n~~~ `\u2603`~~~\r\n"
        "## Unknown\r\n\r\nAuthor's detail: `````` | \u6d4b\u8bd5"
    )
    pack = build_ai_mapping_pack(saved, fetch_carsi_directory(transport=transport()))
    assert pack.filename == AI_MAPPING_PACK_FILENAME
    assert pack.markdown.splitlines()[0] == AI_MAPPING_PACK_MARKER
    raw = saved.encode("utf-8")
    assert f"UTF-8 byte length: {len(raw)}" in pack.markdown
    assert f"SHA-256: {hashlib.sha256(raw).hexdigest()}" in pack.markdown
    assert len(pack.markdown.encode("utf-8").split(raw)) == 2
    # Source is not ended with LF, so the byte following its exact contents is a separator.
    assert pack.markdown.encode("utf-8").split(raw, 1)[1].startswith(b"\n")
    assert "searchStr" not in pack.markdown and '"step"' not in pack.markdown
    assert "CARSI official public resource directory" in pack.markdown
    assert "\u6d4b\u8bd5" in pack.markdown


def test_fixed_prompt_covers_all_mapping_invariants():
    prompt = AI_EDITING_PROMPT_V1.lower()
    for requirement in (
        "access services", "access service id", "journal", "issn-l", "group",
        "publisher id", "publisher name", "publisher url", "conferences",
        "unknown sections", "human notes", "production", "vendor_name",
        "accessservice.name", "accessservice.access_url", "multiple",
        "unassigned", "reuse", "all existing service ids", "duplicate",
        "svc_[0-9a-f]{32}", "uuid", "wayfless", "idp login",
        "aliases", "complete", "importable", "marker", "code fences",
    ):
        # 'multiple' services are intentionally described by several Publishers.
        if requirement == "multiple":
            assert "several publishers may map to one service" in prompt
        else:
            assert requirement in prompt, requirement


def test_fixed_prompt_separates_complete_import_from_unassigned_pdf_research():
    prompt = AI_EDITING_PROMPT_V1.lower()
    assert prompt.index("first output") < prompt.index("### unassigned publishers research")
    assert "one standalone markdown code block tagged markdown" in prompt
    assert "fence longer than any backtick run" in prompt
    assert "the fences are not part of the importable file" in prompt
    assert "only the complete valid list.md" in prompt
    assert "after closing that block" in prompt
    assert "never put research inside list.md" in prompt
    assert "the sole final response is a complete valid list.md" not in prompt

    research = " ".join(prompt.split("### unassigned publishers research", 1)[1].split())
    for requirement in (
        "after completing the carsi access mapping", "every publisher",
        "final access service id remains empty", "openalex publisher id",
        "all journals", "each journal name and issn-l", "for each associated journal",
        "1–3 recent", "article title", "real doi", "observed redirect landing",
        "actual article/full-text hosting platform", "never invent doi",
        "publicly downloadable pdf", "institutional authentication",
        "personal-account or payment-only pdf", "third-party platform",
        "no pdf found", "undetermined", "unauthenticated retrieval outcome",
        "pdf button or open-access metadata alone never proves",
        "documented institutional login option", "carsi directory service",
        "reusable access service", "manual verification",
        "keep ambiguous publishers unassigned", "doi-to-pdf",
        "limited publisher-site utility", "insufficient evidence",
        "working evidence links", "plausible hypotheses and unknowns",
        "browsing, redirect resolution", "never simulate network results",
        "after the list.md code block", "do not create an additional report file",
        "alter protected machine fields", "automatically delete access services",
        "research is not permission to change",
    ):
        assert requirement in research, requirement


def test_fixed_prompt_separates_pdf_host_from_access_and_limits_publisher_claims():
    research = " ".join(AI_EDITING_PROMPT_V1.lower().split()).split(
        "### unassigned publishers research", 1,
    )[1]
    assert "two independent dimensions for that doi" in research
    assert (
        "(1) actual pdf hosting platform: publisher-operated, third-party "
        "platform (name the platform), or unknown"
    ) in research
    assert (
        "(2) pdf access status: publicly downloadable pdf (verified without login); "
        "restricted pdf requiring institutional authentication; personal-account "
        "or payment-only pdf; no pdf found; or cannot verify (undetermined)"
    ) in research
    assert "a pdf on a third-party platform may be public or restricted" in research
    assert "pdf hosting and access status are not mutually exclusive" in research
    assert "separate pdf hosting platform and access status for each doi sample" in research
    assert "only for the checked doi samples" in research
    assert "sample coverage and limits" in research
    assert "never generalize from a few articles to a publisher-wide claim" in research
    assert "classify explicitly: publicly downloadable pdf" not in research


def test_access_aware_prompt_inspects_and_preserves_existing_manual_services():
    prompt = " ".join(AI_EDITING_PROMPT_V1.lower().split())
    for rule in (
        "before any modification, inspect the complete list.md",
        "all journals and their direct publisher links",
        "all publishers and their access service id references",
        "all existing access services and their publisher members",
        "officially evidenced in this carsi catalog",
        "user-maintained without confirmed carsi status",
        "absence from the catalog alone never makes a user-maintained service invalid",
        "preserving each existing service id, name, access url",
        "every publisher assignment",
        "do not silently change it",
        "does not establish compatibility with carsi portal preparation",
        "do not disturb already assigned publishers",
    ):
        assert rule in prompt, rule


def test_access_aware_manual_platform_is_not_auto_promoted_to_carsi():
    prompt = " ".join(AI_EDITING_PROMPT_V1.lower().split())
    assert "a non-carsi platform supporting institutional access" in prompt
    assert "manual service candidate only" in prompt
    assert "never automatically create a service row" in prompt
    assert "rather than inventing a carsi service" in prompt
    # Representative user cases are not baked into the exported instructions.
    assert "project euclid" not in prompt
    assert "jmlr" not in prompt


def test_access_aware_journal_policy_distinguishes_formal_free_and_conditional():
    research = " ".join(AI_EDITING_PROMPT_V1.lower().split()).split(
        "### unassigned publishers research", 1,
    )[1]
    for rule in (
        "every associated journal", "official publishing and access policy",
        "all formal published pdfs free", "mixed free and subscription",
        "delayed free access/embargo", "public accepted manuscripts or preprints",
        "primarily subscription", "or unknown", "free to read",
        "open access license", "formal published pdf",
        "accepted manuscript", "preprint", "abstract or supplemental material",
        "1–3 recent", "real doi", "doi unavailable",
        "verified official article url", "do not fabricate one",
        "doi-only paper identity contract",
    ):
        assert rule in research, rule
    assert "a publicly available manuscript alone never establishes" in research


def test_access_aware_research_keeps_pdf_host_access_version_and_errors_separate():
    research = " ".join(AI_EDITING_PROMPT_V1.lower().split()).split(
        "### unassigned publishers research", 1,
    )[1]
    assert "two independent dimensions for that doi" in research
    assert "pdf hosting and access status are not mutually exclusive" in research
    assert "pdf version separately" in research
    for rule in (
        "formal version of record", "accepted manuscript", "preprint",
        "supplemental material", "timeout", "anti-bot block",
        "redirect error", "download-tool failure",
        "cannot verify (undetermined)", "never proof of a paywall",
        "samples constrain conclusions about the checked dois",
        "documented journal policy",
    ):
        assert rule in research, rule


def test_access_aware_recommendations_are_advisory_and_do_not_change_list():
    prompt = " ".join(AI_EDITING_PROMPT_V1.lower().split())
    research = prompt.split("### unassigned publishers research", 1)[1]
    for recommendation in (
        "no service needed", "keep unassigned — conditional access",
        "reuse existing carsi service", "manual service candidate",
        "needs verification",
    ):
        assert recommendation in research
    assert "exactly one primary recommendation" in research
    assert "official policy and sample evidence" in research
    assert "as a manual follow-up rather than an edit" in research
    assert "never change publisher url in the importable list.md" in research
    assert "do not recommend journal deletion simply to reduce unassigned" in research
    assert "do not revise that block" in research
    assert "no additional changes to the already generated list.md" in prompt


def test_access_aware_pack_orders_five_sections_without_changing_pack_protocol():
    prompt = " ".join(AI_EDITING_PROMPT_V1.lower().split())
    ordered = (
        "first output the final, complete",
        "### mapping changes summary",
        "### existing manual services review",
        "### unassigned publishers research",
        "### manual action items",
    )
    offsets = [prompt.index(item) for item in ordered]
    assert offsets == sorted(offsets)
    assert "no mapping changes" in prompt
    assert "its current assigned publishers" in prompt
    assert "only the complete valid list.md" in prompt

    source = "## Preserved human section\nA user's data.\n"
    pack = build_ai_mapping_pack(
        source, (CarsiResource("Official", "Vendor", "https://example.org"),),
    )
    assert pack.markdown.startswith(AI_MAPPING_PACK_MARKER + "\n")
    assert pack.filename == AI_MAPPING_PACK_FILENAME
    assert pack.markdown.count(source) == 1
    assert pack.markdown.count("## CARSI official public resource directory") == 1


def test_generated_pack_embeds_new_fixed_prompt_without_changing_saved_list():
    saved = "## Journals\n\nJournal name and user text: `````\n"
    pack = build_ai_mapping_pack(saved, fetch_carsi_directory(transport=transport()))
    assert pack.markdown.count(AI_EDITING_PROMPT_V1) == 1
    assert saved in pack.markdown
    assert pack.markdown.index("Unassigned Publishers research") < pack.markdown.index(
        "## Complete saved list.md — byte-exact UTF-8 source"
    )


def test_saved_disk_configuration_verbatim_and_monitor_not_loaded(tmp_path):
    config, path, _, _ = prepared(tmp_path)
    path.write_bytes(path.read_bytes() + b"\r\n## Arbitrary notes\r\n\r\nAuthor detail: \xe4\xb8\xad\xe6\x96\x87 | ```\r\n")
    saved = path.read_bytes()
    before = stamp(path), stamp(config)
    pack = generate_ai_mapping_pack(path, transport=transport())
    assert saved in pack.markdown.encode("utf-8")
    assert b"## Conferences" in pack.markdown.encode("utf-8") or b"## Arbitrary notes" in pack.markdown.encode("utf-8")
    assert b"Author detail" in pack.markdown.encode("utf-8")
    assert b"monitor.yaml" not in pack.markdown.encode("utf-8")
    assert (stamp(path), stamp(config)) == before


def test_saved_file_not_draft_and_changes_during_fetch_refused(tmp_path, monkeypatch):
    from literature_monitor.application import ai_mapping_pack as mapping
    config, path, _, _ = prepared(tmp_path)
    original = stamp(path), stamp(config)
    saved = path.read_text()
    assert saved in generate_ai_mapping_pack(path, transport=transport()).markdown

    def edit_during_fetch(*, transport=None):
        path.write_bytes(path.read_bytes() + b"\n## External edit\n")
        return (CarsiResource("Official", "Publisher", "https://publisher.example"),)
    monkeypatch.setattr(mapping, "fetch_carsi_directory", edit_during_fetch)
    with pytest.raises(MappingPackError, match="changed during CARSI retrieval"):
        generate_ai_mapping_pack(path, transport=transport())
    assert stamp(config) == original[1]
    assert path.read_text().endswith("## External edit\n")


def test_invalid_schema_legacy_unsafe_paths_do_not_fetch(tmp_path):
    config, path, _, _ = prepared(tmp_path)
    before = stamp(path), stamp(config)
    calls = []
    link = tmp_path / "link.md"
    link.symlink_to(path)
    with pytest.raises(MappingPackError):
        generate_ai_mapping_pack(link, transport=transport(calls=calls))
    assert calls == []
    path.write_text("## Journals\n", encoding="utf-8")
    with pytest.raises(MappingPackError):
        generate_ai_mapping_pack(path, transport=transport(calls=calls))
    assert calls == []
    assert stamp(config) == before[1]


def test_fail_fetch_leaves_saved_bytes_and_no_work_file(tmp_path):
    config, path, _, _ = prepared(tmp_path)
    before = stamp(path), stamp(config)
    with pytest.raises(MappingPackError):
        generate_ai_mapping_pack(path, transport=transport(status=503))
    assert (stamp(path), stamp(config)) == before
    assert not (tmp_path / AI_MAPPING_PACK_FILENAME).exists()


@pytest.mark.parametrize("filename", [AI_MAPPING_PACK_FILENAME, "list.md"])
def test_pack_upload_rejected_at_preview_even_when_renamed(tmp_path, filename):
    config, path, _, _ = prepared(tmp_path)
    pack = generate_ai_mapping_pack(path, transport=transport())
    before = stamp(path), stamp(config)
    with pytest.raises(ConfigurationError, match="AI Mapping Pack"):
        preview_full_markdown(path, pack.markdown, filename=filename)
    with TestClient(create_app(config), base_url="http://localhost") as client:
        response = preview(client, submission(client), pack.markdown, filename=filename)
        assert "Import Preview blocked" in response.text
        assert "AI Mapping Pack" in response.text
        assert "data-markdown-preview" not in response.text
    assert (stamp(path), stamp(config)) == before


def test_forged_confirm_source_rechecked_and_cannot_write_pack(tmp_path):
    config, path, _, _ = prepared(tmp_path)
    original = stamp(path), stamp(config)
    proposed = candidate(path)
    valid = preview_full_markdown(path, proposed, filename="regular.md")
    pack = generate_ai_mapping_pack(path, transport=transport())
    forged = replace(valid, proposed=pack.markdown)
    with pytest.raises(ConfigurationError, match="AI Mapping Pack"):
        confirm_full_markdown(forged)
    # The signed web Confirm endpoint also recomputes Preview from source payload.
    with TestClient(create_app(config), base_url="http://localhost") as client:
        ordinary = preview(client, submission(client), proposed)
        assert "data-markdown-preview" in ordinary.text
        from test_web_run_settings import browser_settings_submission
        data = browser_settings_submission(ordinary.text)
        data["markdown_import_payload"] = [
            base64.b64encode(pack.markdown.encode("utf-8")).decode("ascii"),
        ]
        response = client.post("/settings/import/confirm", data=data)
        assert "Import not written" in response.text
        assert "AI Mapping Pack" in response.text
    assert (stamp(path), stamp(config)) == original
