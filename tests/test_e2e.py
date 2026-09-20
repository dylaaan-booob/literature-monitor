from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse
from uuid import UUID

import pytest
import yaml

from literature_monitor.cli import main
from literature_monitor.crossref import CrossrefClient
from literature_monitor.inbox import render_default_inbox_base
from literature_monitor.naming import paper_filename
from literature_monitor.openalex import OpenAlexClient


FIXTURES = Path(__file__).parent / "fixtures"


def fixture(provider: str, name: str) -> dict[str, Any]:
    return json.loads(
        (FIXTURES / provider / name).read_text(encoding="utf-8")
    )


class FakeResponse:
    def __init__(self, payload: object) -> None:
        self.payload = payload

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode()


class SequenceOpener:
    def __init__(self, *payloads: object) -> None:
        self.payloads = list(payloads)
        self.requests: list[tuple[Any, float]] = []

    def __call__(self, request: Any, *, timeout: float) -> FakeResponse:
        self.requests.append((request, timeout))
        if not self.payloads:
            raise AssertionError(f"unexpected HTTP request: {request.full_url}")
        return FakeResponse(self.payloads.pop(0))


class RoutingOpener:
    def __init__(self, route: Callable[[Any], object]) -> None:
        self.route = route
        self.requests: list[tuple[Any, float]] = []

    def __call__(self, request: Any, *, timeout: float) -> FakeResponse:
        self.requests.append((request, timeout))
        return FakeResponse(self.route(request))


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
            "next-cursor": "unused",
        },
    }


def test_full_cli_cycle_preserves_human_state_and_exports_kept_paper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    config_path = repository_root / "config.example.yaml"
    output_dir = tmp_path / "vault"
    openalex_openers: list[SequenceOpener] = []
    crossref_openers: list[SequenceOpener] = []
    semantic_clients: list[object] = []
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
    discovery_payload = crossref_list_payload(
        [matching_payload, crossref_only_payload]
    )

    def openalex_client(**kwargs: object) -> OpenAlexClient:
        opener = SequenceOpener(
            fixture("openalex", "source_biometrics.json"),
            fixture("openalex", "works_page_1.json"),
            fixture("openalex", "works_page_2.json"),
        )
        openalex_openers.append(opener)
        return OpenAlexClient(
            api_key=kwargs.get("api_key"),  # type: ignore[arg-type]
            opener=opener,
            sleep=lambda _delay: None,
        )

    def crossref_client(**kwargs: object) -> CrossrefClient:
        opener = SequenceOpener(discovery_payload)
        crossref_openers.append(opener)
        return CrossrefClient(
            mailto=kwargs.get("mailto"),  # type: ignore[arg-type]
            opener=opener,
            sleep=lambda _delay: None,
        )

    class FakeSemanticScholar:
        def __init__(self, *, fail_search: bool) -> None:
            self.fail_search = fail_search
            self.batch_calls: list[list[str]] = []
            self.search_calls: list[dict[str, object]] = []

        def get_papers(
            self,
            paper_ids: list[str],
            **kwargs: object,
        ) -> tuple[list[dict[str, object]], list[str]]:
            self.batch_calls.append(paper_ids)
            requested = {identifier.casefold() for identifier in paper_ids}
            records: list[dict[str, object]] = []
            if "doi:10.1093/biomtc/ujag004" in requested:
                records.append(
                    {
                        "paperId": "S2-three-provider",
                        "corpusId": 1001,
                        "externalIds": {"DOI": "10.1093/biomtc/ujag004"},
                        "title": "A Semiparametric Approach to the Cox Model",
                        "abstract": "Semantic rescue evidence for the retained paper.",
                        "authors": [{"name": "Thomas Ding"}],
                        "publicationDate": "2026-01-10",
                        "year": 2026,
                        "journal": {"name": "Biometrics"},
                        "publicationVenue": {
                            "name": "Biometrics",
                            "issn": "0006-341X",
                        },
                        "venue": "Biometrics",
                        "fieldsOfStudy": ["Medicine"],
                        "s2FieldsOfStudy": [
                            {"category": "Medicine", "source": "s2-fos-model"}
                        ],
                    }
                )
            missing = [
                identifier
                for identifier in paper_ids
                if identifier.casefold() != "doi:10.1093/biomtc/ujag004"
            ]
            return records, missing

        def search_paper(self, query: str, **kwargs: object) -> list[dict[str, object]]:
            self.search_calls.append({"query": query, **kwargs})
            if self.fail_search:
                raise ConnectionError("Semantic Scholar search unavailable")
            return [
                {
                    "paperId": "S2-only",
                    "corpusId": 1002,
                    "externalIds": {"DOI": "10.5555/s2-only"},
                    "title": "Semantic only supplemental study",
                    "abstract": "Provider-only discovery evidence.",
                    "authors": [{"name": "Semantic Author"}],
                    "publicationDate": "2026-01-22",
                    "year": 2026,
                    "journal": {"name": "Biometrics"},
                    "publicationVenue": {
                        "name": "Biometrics",
                        "issn": "0006-341X",
                    },
                    "venue": "Biometrics",
                    "fieldsOfStudy": [],
                    "s2FieldsOfStudy": [],
                }
            ]

    def semantic_scholar_client(api_key: str | None) -> FakeSemanticScholar:
        client = FakeSemanticScholar(fail_search=bool(semantic_clients))
        semantic_clients.append(client)
        return client

    monkeypatch.delenv("OPENALEX_API_KEY", raising=False)
    monkeypatch.delenv("CROSSREF_MAILTO", raising=False)
    monkeypatch.setattr("literature_monitor.cli.OpenAlexClient", openalex_client)
    monkeypatch.setattr("literature_monitor.cli.CrossrefClient", crossref_client)
    monkeypatch.setattr(
        "literature_monitor.cli.create_semantic_scholar_client",
        semantic_scholar_client,
    )

    materialize_args = (
        "materialize",
        "--config",
        str(config_path),
        "--journal",
        "Biometrics",
        "--from-date",
        "2026-01-01",
        "--to-date",
        "2026-01-31",
        "--keyword-expression",
        '"semantic rescue" OR "semantic only"',
        "--output-dir",
        str(output_dir),
    )

    assert main(materialize_args) == 0
    first_cli = capsys.readouterr()
    assert first_cli.out == ""

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
        assert values["versions"]

    doi_path = next(
        path
        for path, values in values_by_path.items()
        if values["doi"] == "10.1093/biomtc/ujag004"
    )
    semantic_only_path = next(
        path
        for path, values in values_by_path.items()
        if values["doi"] == "10.5555/s2-only"
    )
    doi_values = values_by_path[doi_path]
    assert doi_values["doi"] == "10.1093/biomtc/ujag004"
    assert doi_values["external_ids"]["crossref"] == "10.1093/biomtc/ujag004"
    assert doi_values["external_ids"]["semantic_scholar"] == "S2-three-provider"
    assert {source["provider"] for source in doi_values["sources"]} == {
        "crossref",
        "openalex",
        "semantic_scholar",
    }
    assert {
        source["provider"]
        for source in values_by_path[semantic_only_path]["sources"]
    } == {"semantic_scholar"}
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
    replace_once(semantic_only_path, "status: candidate\n", "status: rejected\n")
    replace_once(
        semantic_only_path,
        "## Notes\n",
        "## Notes\n\nHuman rejected note.\n",
    )
    custom_inbox = b"human-owned custom Inbox bytes\n"
    inbox_path.write_bytes(custom_inbox)

    expected_paths = set(paper_paths)
    expected_ids = dict(ids_by_path)
    expected_authors = set(author_paths)

    assert main(materialize_args) == 1
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
    rejected_contents = semantic_only_path.read_text(encoding="utf-8")
    assert frontmatter(doi_path)["status"] == "kept"
    assert frontmatter(doi_path)["reviewer_state"] == {"priority": "high"}
    assert "Human kept note." in kept_contents
    assert "## Review Context\n\nRetain this custom section." in kept_contents
    assert frontmatter(semantic_only_path)["status"] == "rejected"
    assert "Human rejected note." in rejected_contents
    assert inbox_path.read_bytes() == custom_inbox
    assert list(output_dir.glob("*.base")) == [inbox_path]

    assert len(openalex_openers) == 2
    assert len(crossref_openers) == 2
    assert len(semantic_clients) == 2
    assert semantic_clients[0].search_calls  # type: ignore[attr-defined]
    assert semantic_clients[1].search_calls  # type: ignore[attr-defined]
    assert "Semantic Scholar search unavailable" in second_cli.err
    for opener in openalex_openers:
        assert opener.payloads == []
        parsed_requests = [urlparse(request.full_url) for request, _ in opener.requests]
        assert [request.path for request in parsed_requests] == [
            "/sources/issn:0006-341X",
            "/works",
            "/works",
        ]
        for request in parsed_requests[1:]:
            query = parse_qs(request.query)
            assert "search" not in query
            assert "q" not in query
            assert query["filter"] == [
                "primary_location.source.id:S8265502,"
                "from_publication_date:2026-01-01,"
                "to_publication_date:2026-01-31"
            ]
    for opener in crossref_openers:
        assert opener.payloads == []
        assert len(opener.requests) == 1
        request, _timeout = opener.requests[0]
        parsed = urlparse(request.full_url)
        assert parsed.path == "/v1/journals/0006-341X/works"
        query = parse_qs(parsed.query)
        assert query["filter"] == [
            "from-pub-date:2026-01-01,until-pub-date:2026-01-31"
        ]
        assert query["cursor"] == ["*"]

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
        "# Representative journals\n\n"
        "## Journals\n\n"
        "| Journal | ISSN/EISSN |\n"
        "| --- | --- |\n"
        "| BIOMETRICS | 0006-341X |\n"
        "| IEEE Transactions on Pattern Analysis and Machine Intelligence "
        "| 0162-8828 |\n"
        "| Nature Methods | 1548-7091 / 1548-7105 |\n",
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
        parsed = urlparse(request.full_url)
        if parsed.path.startswith("/sources/issn:"):
            return sources[parsed.path.removeprefix("/sources/issn:")]
        assert parsed.path == "/works"
        query = parse_qs(parsed.query)
        filters = query["filter"][0].split(",")
        source_id = filters[0].removeprefix("primary_location.source.id:")
        assert filters[1] in {
            "from_publication_date:2026-01-01",
            "from_publication_date:2026-01-15",
        }
        assert filters[2] in {
            "to_publication_date:2026-01-20",
            "to_publication_date:2026-01-31",
        }
        return works_payload(works[source_id])

    def route_crossref(request: Any) -> object:
        parsed = urlparse(request.full_url)
        if parsed.path.startswith("/v1/journals/"):
            return crossref_list_payload([])
        doi = unquote(parsed.path.removeprefix("/v1/works/")).casefold()
        return crossref_works[doi]

    openalex_opener = RoutingOpener(route_openalex)
    crossref_opener = RoutingOpener(route_crossref)

    def openalex_client(**kwargs: object) -> OpenAlexClient:
        return OpenAlexClient(
            api_key=kwargs.get("api_key"),  # type: ignore[arg-type]
            opener=openalex_opener,
            sleep=lambda _delay: None,
        )

    def crossref_client(**kwargs: object) -> CrossrefClient:
        return CrossrefClient(
            mailto=kwargs.get("mailto"),  # type: ignore[arg-type]
            opener=crossref_opener,
            sleep=lambda _delay: None,
        )

    monkeypatch.delenv("OPENALEX_API_KEY", raising=False)
    monkeypatch.delenv("CROSSREF_MAILTO", raising=False)
    monkeypatch.setattr("literature_monitor.cli.OpenAlexClient", openalex_client)
    monkeypatch.setattr("literature_monitor.cli.CrossrefClient", crossref_client)
    monkeypatch.setattr(
        "literature_monitor.cli.create_semantic_scholar_client",
        lambda api_key: type(
            "EmptySemanticScholar",
            (),
            {
                "get_papers": lambda self, paper_ids, **kwargs: ([], []),
                "search_paper": lambda self, query, **kwargs: [],
            },
        )(),
    )

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
        urlparse(request.full_url) for request, _timeout in openalex_opener.requests
    ]
    source_requests = [
        request for request in openalex_requests if request.path.startswith("/sources/")
    ]
    assert [request.path for request in source_requests] == [
        "/sources/issn:0006-341X",
        "/sources/issn:0162-8828",
        "/sources/issn:1548-7091",
        "/sources/issn:1548-7105",
    ] * 2
    works_requests = [
        request for request in openalex_requests if request.path == "/works"
    ]
    assert len(works_requests) == 6
    expected_filters = [
        (
            f"primary_location.source.id:{source_id},"
            "from_publication_date:2026-01-01,"
            "to_publication_date:2026-01-20"
        )
        for source_id in ("S8265502", "S199944782", "S127827428")
    ] + [
        (
            f"primary_location.source.id:{source_id},"
            "from_publication_date:2026-01-15,"
            "to_publication_date:2026-01-31"
        )
        for source_id in ("S8265502", "S199944782", "S127827428")
    ]
    for request, expected_filter in zip(works_requests, expected_filters):
        query = parse_qs(request.query)
        assert "search" not in query
        assert "q" not in query
        assert query["filter"] == [expected_filter]

    crossref_urls = [
        urlparse(request.full_url)
        for request, _timeout in crossref_opener.requests
    ]
    journal_requests = [
        request for request in crossref_urls if request.path.startswith("/v1/journals/")
    ]
    assert [request.path for request in journal_requests] == [
        "/v1/journals/0006-341X/works",
        "/v1/journals/0162-8828/works",
        "/v1/journals/1548-7091/works",
        "/v1/journals/1548-7105/works",
    ] * 2
    for request in journal_requests[:4]:
        assert parse_qs(request.query)["filter"] == [
            "from-pub-date:2026-01-01,until-pub-date:2026-01-20"
        ]
    for request in journal_requests[4:]:
        assert parse_qs(request.query)["filter"] == [
            "from-pub-date:2026-01-15,until-pub-date:2026-01-31"
        ]
    crossref_requests = [
        unquote(urlparse(request.full_url).path.removeprefix("/v1/works/"))
        for request, _timeout in crossref_opener.requests
        if urlparse(request.full_url).path.startswith("/v1/works/")
    ]
    assert crossref_requests == [
        "10.1000/biometrics-overlap",
        "10.1000/nature-methods-overlap",
        "10.1000/tpami-overlap",
    ] * 2

    before_export = snapshot_files(output_dir)
    assert main(("export-kept", "--output-dir", str(output_dir))) == 0
    exported = capsys.readouterr()
    assert exported.out == "10.1000/biometrics-overlap\n"
    assert "Kept export completed: 1 entries, 0 issues" in exported.err
    assert snapshot_files(output_dir) == before_export
