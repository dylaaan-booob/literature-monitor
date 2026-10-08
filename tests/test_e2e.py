from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse
from uuid import UUID

import httpx
import pytest
import yaml

from literature_monitor.cli import main
from literature_monitor.application.provider_state import ProviderStateStatus, read_provider_state
from literature_monitor.crossref import CrossrefClient
from literature_monitor.inbox import render_default_inbox_base
from literature_monitor.naming import paper_filename
from literature_monitor.openalex import OpenAlexClient


FIXTURES = Path(__file__).parent / "fixtures"


def fixture(provider: str, name: str) -> dict[str, Any]:
    return json.loads(
        (FIXTURES / provider / name).read_text(encoding="utf-8")
    )


class SequenceTransport(httpx.MockTransport):
    def __init__(self, *outcomes: object) -> None:
        self.outcomes = list(outcomes)
        self.requests: list[httpx.Request] = []
        self.closed = False
        super().__init__(self._respond)

    def _respond(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self.outcomes:
            raise AssertionError(f"unexpected HTTP request: {request.url}")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        if isinstance(outcome, httpx.Response):
            return outcome
        return httpx.Response(200, json=outcome)

    def close(self) -> None:
        self.closed = True
        super().close()

class RoutingTransport(httpx.MockTransport):
    def __init__(self, route: Callable[[httpx.Request], object]) -> None:
        self.route = route
        self.closed = False
        self.requests: list[httpx.Request] = []
        super().__init__(self._respond)

    def _respond(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200, json=self.route(request))

    def close(self):
        self.closed = True
        super().close()


def frontmatter(path: Path) -> dict[str, Any]:
    opening, payload, _body = path.read_text(encoding="utf-8").split(
        "---", maxsplit=2
    )
    assert opening == ""
    parsed = yaml.safe_load(payload)
    assert isinstance(parsed, dict)
    return parsed


def replace_once(path: Path, old: str, new: str) -> None:
    contents = path.read_text(encoding="utf-8")
    assert contents.count(old) == 1
    path.write_text(contents.replace(old, new, 1), encoding="utf-8")


def snapshot_files(root: Path) -> dict[Path, bytes]:
    return {
        path.relative_to(root): path.read_bytes()
        for path in sorted(root.rglob("*.md"))
    }


def source_payload(
    source_id: str,
    display_name: str,
    issn_l: str,
    issns: list[str],
) -> dict[str, object]:
    return {
        "id": f"https://openalex.org/{source_id}",
        "display_name": display_name,
        "issn_l": issn_l,
        "issn": issns,
        "type": "journal",
        "alternate_titles": [],
        "abbreviated_title": None,
    }


def work_payload(
    work_id: str,
    doi: str,
    title: str,
    publication_date: str,
    source_id: str,
    journal: str,
    author_id: str,
    author_name: str,
) -> dict[str, object]:
    return {
        "id": f"https://openalex.org/{work_id}",
        "doi": f"https://doi.org/{doi}",
        "title": title,
        "publication_date": publication_date,
        "abstract_inverted_index": None,
        "authorships": [
            {
                "author": {
                    "id": f"https://openalex.org/{author_id}",
                    "display_name": author_name,
                    "orcid": None,
                },
                "raw_author_name": author_name,
            }
        ],
        "primary_location": {
            "source": {
                "id": f"https://openalex.org/{source_id}",
                "display_name": journal,
            }
        },
    }


def works_payload(work: dict[str, object]) -> dict[str, object]:
    return {
        "meta": {"count": 1, "per_page": 100, "next_cursor": None},
        "results": [work],
        "group_by": [],
    }


def crossref_payload(
    doi: str,
    title: str,
    journal: str,
    publication_date: tuple[int, int, int],
) -> dict[str, object]:
    return {
        "status": "ok",
        "message-type": "work",
        "message-version": "1.0.0",
        "message": {
            "DOI": doi,
            "title": [title],
            "container-title": [journal],
            "abstract": "<jats:p>Fixture abstract.</jats:p>",
            "published": {"date-parts": [[*publication_date]]},
            "relation": {},
            "type": "journal-article",
        },
    }


def crossref_list_payload(messages: list[dict[str, object]]) -> dict[str, object]:
    return {
        "status": "ok",
        "message-type": "work-list",
        "message-version": "1.0.0",
        "message": {
            "items": messages,
            "total-results": len(messages),
            "next-cursor": "unused",
        },
    }


def test_run_full_cli_cycle_preserves_human_state_and_exports_kept_paper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    whitelist_path = tmp_path / "list.md"
    whitelist_path.write_text(
        '# E2E journals\n\n## Journals\n\n| Journal | ISSN-L | Publisher ID | Group |\n|---|---|---|---|\n| Biometrics | 0006-341X |  |  |\n',
        encoding="utf-8",
    )
    config_path = tmp_path / "monitor.yaml"
    config_path.write_text(
        "keyword_expression: 'cox OR \"crossref only\"'\n"
        "output_dir: ./workspace\n"
        "from_date: 2026-01-01\n"
        "to_date: 2026-01-31\n"
        "log_level: INFO\n",
        encoding="utf-8",
    )
    output_dir = tmp_path / "workspace"
    openalex_transports: list[RoutingTransport] = []
    crossref_transports: list[RoutingTransport] = []
    matching_payload = fixture("crossref", "work_complete.json")["message"]
    matching_payload["author"] = [{"given": "Thomas", "family": "Ding"}]
    matching_payload["ISSN"] = ["0006-341X"]
    crossref_only_payload = {
        "DOI": "10.5555/crossref-only",
        "title": ["Crossref-only monitoring study"],
        "container-title": ["Biometrics"],
        "abstract": "<jats:p>A crossref only result.</jats:p>",
        "author": [{"given": "Crossref", "family": "Author"}],
        "ISSN": ["0006-341X"],
        "published-online": {"date-parts": [[2026, 1, 25]]},
        "relation": {},
        "type": "journal-article",
    }
    matching_payload["indexed"] = {"date-time": "2026-02-01T00:00:00Z"}
    crossref_only_payload["indexed"] = {"date-time": "2026-02-01T00:00:00Z"}
    discovery_payload = crossref_list_payload(
        [matching_payload, crossref_only_payload]
    )

    def openalex_client(**kwargs: object) -> OpenAlexClient:
        def route(request):
            if request.url.path == "/sources":
                return {"meta": {"count": 1}, "results": [fixture("openalex", "source_biometrics.json")]}
            assert not {"locations", "updated_date"}.intersection(request.url.params["select"].split(","))
            page = fixture("openalex", "works_page_2.json" if request.url.params.get("cursor") == "next-page" else "works_page_1.json")
            return page
        transport = RoutingTransport(route)
        openalex_transports.append(transport)
        return OpenAlexClient(
            api_key=kwargs.get("api_key"),  # type: ignore[arg-type]
            transport=transport,
            sleep=lambda _delay: None,
        )

    def crossref_client(**kwargs: object) -> CrossrefClient:
        transport = RoutingTransport(lambda request: discovery_payload)
        crossref_transports.append(transport)
        return CrossrefClient(
            mailto=kwargs.get("mailto"),  # type: ignore[arg-type]
            transport=transport,
            sleep=lambda _delay: None,
        )

    monkeypatch.delenv("OPENALEX_API_KEY", raising=False)
    monkeypatch.delenv("CROSSREF_MAILTO", raising=False)
    monkeypatch.setattr("literature_monitor.application.monitor.OpenAlexClient", openalex_client)
    monkeypatch.setattr("literature_monitor.application.monitor.CrossrefClient", crossref_client)

    run_args = ("run", "--config", str(config_path))

    assert main(run_args) == 0
    first_cli = capsys.readouterr()
    assert first_cli.out == ""
    state_result = read_provider_state(output_dir)
    assert state_result.status is ProviderStateStatus.AVAILABLE

    paper_paths = tuple(sorted((output_dir / "Papers").glob("*.md")))
    author_paths = tuple(sorted((output_dir / "Authors").glob("*.md")))
    inbox_path = output_dir / "Inbox.base"
    assert len(paper_paths) == 2
    assert len(author_paths) == 2
    assert inbox_path.read_text(encoding="utf-8") == render_default_inbox_base()

    values_by_path = {path: frontmatter(path) for path in paper_paths}
    ids_by_path = {
        path: UUID(str(values["id"])) for path, values in values_by_path.items()
    }
    all_ids = tuple(ids_by_path.values())
    for path, values in values_by_path.items():
        assert values["type"] == "paper"
        assert values["status"] == "candidate"
        assert values["journal"].casefold() == "biometrics"
        assert path.name == paper_filename(values["title"], ids_by_path[path], all_ids)
        assert values["authors"]
        assert all(
            value.startswith("[[Authors/") and value.endswith("]]"
            )
            for value in values["authors"]
        )
        assert values["sources"]
        assert "versions" not in values and "preferred_version" not in values

    doi_path = next(
        path
        for path, values in values_by_path.items()
        if values["doi"] == "10.1093/biomtc/ujag004"
    )
    crossref_only_path = next(
        path
        for path, values in values_by_path.items()
        if values["doi"] == "10.5555/crossref-only"
    )
    doi_values = values_by_path[doi_path]
    assert doi_values["doi"] == "10.1093/biomtc/ujag004"
    assert doi_values["external_ids"]["crossref"] == "10.1093/biomtc/ujag004"
    assert {source["provider"] for source in doi_values["sources"]} == {
        "crossref",
        "openalex",
    }
    assert {
        source["provider"]
        for source in values_by_path[crossref_only_path]["sources"]
    } == {"crossref"}
    assert "The Cox model" in doi_path.read_text(encoding="utf-8")

    replace_once(
        doi_path,
        "status: candidate\n",
        "status: kept\nreviewer_state:\n  priority: high\n",
    )
    replace_once(
        doi_path,
        "## Notes\n",
        "## Notes\n\nHuman kept note.\n\n"
        "## Review Context\n\nRetain this custom section.\n",
    )
    replace_once(crossref_only_path, "status: candidate\n", "status: rejected\n")
    replace_once(
        crossref_only_path,
        "## Notes\n",
        "## Notes\n\nHuman rejected note.\n",
    )
    custom_inbox = b"human-owned custom Inbox bytes\n"
    inbox_path.write_bytes(custom_inbox)

    expected_paths = set(paper_paths)
    expected_ids = dict(ids_by_path)
    expected_authors = set(author_paths)

    before_reuse = snapshot_files(output_dir)
    assert main(run_args) == 0
    reuse_cli = capsys.readouterr()
    assert "Provider-state reuse is now automatic." not in reuse_cli.err
    assert openalex_transports[-1].requests and crossref_transports[-1].requests
    assert set(snapshot_files(output_dir)) == set(before_reuse)
    assert "Human kept note." in doi_path.read_text()
    assert "Human rejected note." in crossref_only_path.read_text()

    assert main(run_args) == 0
    second_cli = capsys.readouterr()
    assert second_cli.out == ""

    rerun_paths = set((output_dir / "Papers").glob("*.md"))
    rerun_authors = set((output_dir / "Authors").glob("*.md"))
    assert rerun_paths == expected_paths
    assert rerun_authors == expected_authors
    assert {
        path: UUID(str(frontmatter(path)["id"])) for path in rerun_paths
    } == expected_ids

    kept_contents = doi_path.read_text(encoding="utf-8")
    rejected_contents = crossref_only_path.read_text(encoding="utf-8")
    assert frontmatter(doi_path)["status"] == "kept"
    assert frontmatter(doi_path)["reviewer_state"] == {"priority": "high"}
    assert "Human kept note." in kept_contents
    assert "## Review Context\n\nRetain this custom section." in kept_contents
    assert frontmatter(crossref_only_path)["status"] == "rejected"
    assert "Human rejected note." in rejected_contents
    assert inbox_path.read_bytes() == custom_inbox
    assert list(output_dir.glob("*.base")) == [inbox_path]

    assert len(openalex_transports) == 3
    assert len(crossref_transports) == 3
    for transport in openalex_transports:
        assert transport.closed
        assert [request.url.path for request in transport.requests] == ["/sources", "/works", "/works"]
        thin = [r for r in transport.requests if r.url.path == "/works"]
        for request in thin:
            assert "primary_location.source.id:S8265502" in request.url.params["filter"]
            assert not {"locations", "updated_date"}.intersection(request.url.params["select"].split(","))
            assert "search" not in request.url.params and "q" not in request.url.params
    for index, transport in enumerate(crossref_transports):
        assert transport.closed
        assert len(transport.requests) == (2 if index == 0 else 1)
        manifest = transport.requests[0]
        assert manifest.url.path == "/v1/works"
        assert manifest.url.params["select"] == "DOI,ISSN,indexed"
        assert "issn:0006-341X" in manifest.url.params["filter"]
    assert state_result.state is not None
    assert read_provider_state(output_dir).state.crossref_records

    before_export = snapshot_files(output_dir)
    assert main(("export-kept", "--output-dir", str(output_dir))) == 0
    first_export = capsys.readouterr()
    assert first_export.out == "10.1093/biomtc/ujag004\n"
    assert "Kept export completed: 1 entries, 0 issues" in first_export.err
    assert snapshot_files(output_dir) == before_export
    assert inbox_path.read_bytes() == custom_inbox

    replace_once(doi_path, "status: kept\n", "status: in_zotero\n")
    after_manual_import = snapshot_files(output_dir)
    assert main(("export-kept", "--output-dir", str(output_dir))) == 0
    second_export = capsys.readouterr()
    assert second_export.out == ""
    assert "Kept export completed: 0 entries, 0 issues" in second_export.err
    assert snapshot_files(output_dir) == after_manual_import


def test_representative_multi_journal_cycle_handles_overlapping_rerun(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    whitelist_path = tmp_path / "representative-list.md"
    whitelist_path.write_text(
        '# Representative journals\n\n## Journals\n\n| Journal | ISSN-L | Publisher ID | Group |\n|---|---|---|---|\n| BIOMETRICS | 0006-341X |  |  |\n| IEEE Transactions on Pattern Analysis and Machine Intelligence | 0162-8828 |  |  |\n| Nature Methods | 1548-7091 |  |  |\n',
        encoding="utf-8",
    )
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "venue_whitelist: representative-list.md\n"
        "keyword_expression: biomarker OR vision OR genomics\n"
        "log_level: INFO\n",
        encoding="utf-8",
    )
    output_dir = tmp_path / "vault"

    sources = {
        "0006-341X": source_payload(
            "S8265502", "Biometrics", "0006-341X", ["0006-341X", "1541-0420"]
        ),
        "0162-8828": source_payload(
            "S199944782",
            "IEEE Transactions on Pattern Analysis and Machine Intelligence",
            "0162-8828",
            ["0162-8828", "1939-3539", "2160-9292"],
        ),
        "1548-7091": source_payload(
            "S127827428",
            "Nature Methods",
            "1548-7091",
            ["1548-7091", "1548-7105"],
        ),
        "1548-7105": source_payload(
            "S127827428",
            "Nature Methods",
            "1548-7091",
            ["1548-7091", "1548-7105"],
        ),
    }
    works = {
        "S8265502": work_payload(
            "W900000001",
            "10.1000/biometrics-overlap",
            "Biomarker models for survival studies",
            "2026-01-16",
            "S8265502",
            "Biometrics",
            "A900000001",
            "Ada Statistician",
        ),
        "S199944782": work_payload(
            "W900000002",
            "10.1000/tpami-overlap",
            "Vision representations for robust recognition",
            "2026-01-17",
            "S199944782",
            "IEEE Transactions on Pattern Analysis and Machine Intelligence",
            "A900000002",
            "Grace Vision",
        ),
        "S127827428": work_payload(
            "W900000003",
            "10.1000/nature-methods-overlap",
            "Genomics workflows for single cells",
            "2026-01-18",
            "S127827428",
            "Nature Methods",
            "A900000003",
            "Lin Methodologist",
        ),
    }
    crossref_works = {
        "10.1000/biometrics-overlap": crossref_payload(
            "10.1000/biometrics-overlap",
            "Biomarker models for survival studies",
            "Biometrics",
            (2026, 1, 16),
        ),
        "10.1000/tpami-overlap": crossref_payload(
            "10.1000/tpami-overlap",
            "Vision representations for robust recognition",
            "IEEE Transactions on Pattern Analysis and Machine Intelligence",
            (2026, 1, 17),
        ),
        "10.1000/nature-methods-overlap": crossref_payload(
            "10.1000/nature-methods-overlap",
            "Genomics workflows for single cells",
            "Nature Methods",
            (2026, 1, 18),
        ),
    }

    def route_openalex(request: Any) -> object:
        assert request.url.path in {"/sources", "/works"}
        if request.url.path == "/sources":
            unique = {source["id"]: source for source in sources.values()}
            return {"meta": {"count": len(unique)}, "results": list(unique.values())}
        filters = request.url.params["filter"]
        assert not {"locations", "updated_date"}.intersection(request.url.params["select"].split(","))
        assert filters.startswith("primary_location.source.id:")
        assert "from_publication_date:" in filters and "to_publication_date:" in filters
        return {"meta": {"count": 3, "next_cursor": None}, "results": list(works.values())}

    def route_crossref(request: Any) -> object:
        assert request.url.path == "/v1/works"
        filters = request.url.params["filter"].split(",")
        if any(value.startswith("issn:") for value in filters):
            return crossref_list_payload([])
        dois = [value.removeprefix("doi:") for value in filters]
        return crossref_list_payload([crossref_works[doi]["message"] for doi in dois])

    openalex_transport = RoutingTransport(route_openalex)
    crossref_transport = RoutingTransport(route_crossref)

    def openalex_client(**kwargs: object) -> OpenAlexClient:
        return OpenAlexClient(
            api_key=kwargs.get("api_key"),  # type: ignore[arg-type]
            transport=openalex_transport,
            sleep=lambda _delay: None,
        )

    def crossref_client(**kwargs: object) -> CrossrefClient:
        return CrossrefClient(
            mailto=kwargs.get("mailto"),  # type: ignore[arg-type]
            transport=crossref_transport,
            sleep=lambda _delay: None,
        )

    monkeypatch.delenv("OPENALEX_API_KEY", raising=False)
    monkeypatch.delenv("CROSSREF_MAILTO", raising=False)
    monkeypatch.setattr("literature_monitor.application.monitor.OpenAlexClient", openalex_client)
    monkeypatch.setattr("literature_monitor.application.monitor.CrossrefClient", crossref_client)

    def materialize(from_date: str, to_date: str) -> int:
        return main(
            (
                "materialize",
                "--config",
                str(config_path),
                "--from-date",
                from_date,
                "--to-date",
                to_date,
                "--output-dir",
                str(output_dir),
            )
        )

    assert materialize("2026-01-01", "2026-01-20") == 0
    first_cli = capsys.readouterr()
    assert first_cli.out == ""

    paper_paths = tuple(sorted((output_dir / "Papers").glob("*.md")))
    author_paths = tuple(sorted((output_dir / "Authors").glob("*.md")))
    assert len(paper_paths) == 3
    assert len(author_paths) == 3
    paths_by_journal = {
        str(frontmatter(path)["journal"]): path for path in paper_paths
    }
    assert set(paths_by_journal) == {
        "Biometrics",
        "IEEE Transactions on Pattern Analysis and Machine Intelligence",
        "Nature Methods",
    }
    for path in paper_paths:
        values = frontmatter(path)
        assert values["status"] == "candidate"
        assert values["doi"] in crossref_works
        assert {source["provider"] for source in values["sources"]} == {
            "crossref",
            "openalex",
        }

    kept_path = paths_by_journal["Biometrics"]
    rejected_path = paths_by_journal[
        "IEEE Transactions on Pattern Analysis and Machine Intelligence"
    ]
    replace_once(
        kept_path,
        "status: candidate\n",
        "status: kept\nreviewer_state:\n  priority: high\n",
    )
    replace_once(kept_path, "## Notes\n", "## Notes\n\nHuman kept note.\n")
    replace_once(rejected_path, "status: candidate\n", "status: rejected\n")
    replace_once(
        rejected_path,
        "## Notes\n",
        "## Notes\n\nHuman rejected note.\n",
    )

    expected_paths = set(paper_paths)
    expected_ids = {
        path: UUID(str(frontmatter(path)["id"])) for path in paper_paths
    }
    expected_authors = set(author_paths)

    assert materialize("2026-01-15", "2026-01-31") == 0
    second_cli = capsys.readouterr()
    assert second_cli.out == ""

    rerun_paths = set((output_dir / "Papers").glob("*.md"))
    rerun_authors = set((output_dir / "Authors").glob("*.md"))
    assert rerun_paths == expected_paths
    assert rerun_authors == expected_authors
    assert {
        path: UUID(str(frontmatter(path)["id"])) for path in rerun_paths
    } == expected_ids
    assert frontmatter(kept_path)["status"] == "kept"
    assert frontmatter(kept_path)["reviewer_state"] == {"priority": "high"}
    assert "Human kept note." in kept_path.read_text(encoding="utf-8")
    assert frontmatter(rejected_path)["status"] == "rejected"
    assert "Human rejected note." in rejected_path.read_text(encoding="utf-8")

    openalex_requests = [
        urlparse(str(request.url)) for request in openalex_transport.requests
    ]
    source_requests = [request for request in openalex_requests if request.path == "/sources"]
    assert len(source_requests) == 2
    works_requests = [request for request in openalex_transport.requests
                      if request.url.path == "/works"]
    assert len(works_requests) == 2
    for request, start, end in zip(works_requests, ("2026-01-01", "2026-01-15"), ("2026-01-20", "2026-01-31"), strict=True):
        filters = request.url.params["filter"]
        assert "S8265502|S199944782|S127827428" in filters
        assert f"from_publication_date:{start}" in filters and f"to_publication_date:{end}" in filters
        assert "search" not in request.url.params and "q" not in request.url.params
    assert not any("locations" in r.url.params.get("select", "").split(",") for r in openalex_transport.requests)
    assert all(r.url.path == "/v1/works" for r in crossref_transport.requests)
    manifests = [r for r in crossref_transport.requests if "issn:" in r.url.params["filter"]]
    assert len(manifests) == 2
    assert all(r.url.params["select"] == "DOI,ISSN,indexed" for r in manifests)
    assert len([r for r in crossref_transport.requests if "doi:" in r.url.params["filter"]]) == 4

    before_export = snapshot_files(output_dir)
    assert main(("export-kept", "--output-dir", str(output_dir))) == 0
    exported = capsys.readouterr()
    assert exported.out == "10.1000/biometrics-overlap\n"
    assert "Kept export completed: 1 entries, 0 issues" in exported.err
    assert snapshot_files(output_dir) == before_export
