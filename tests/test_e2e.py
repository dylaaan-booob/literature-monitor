from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
from uuid import UUID

import pytest
import yaml

from literature_monitor.cli import main
from literature_monitor.crossref import CrossrefClient
from literature_monitor.markdown_state import MISSING_ABSTRACT
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
        opener = SequenceOpener(fixture("crossref", "work_complete.json"))
        crossref_openers.append(opener)
        return CrossrefClient(
            mailto=kwargs.get("mailto"),  # type: ignore[arg-type]
            opener=opener,
            sleep=lambda _delay: None,
        )

    monkeypatch.delenv("OPENALEX_API_KEY", raising=False)
    monkeypatch.delenv("CROSSREF_MAILTO", raising=False)
    monkeypatch.setattr("literature_monitor.cli.OpenAlexClient", openalex_client)
    monkeypatch.setattr("literature_monitor.cli.CrossrefClient", crossref_client)

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
        '"Cox regression" OR "point processes"',
        "--output-dir",
        str(output_dir),
    )

    assert main(materialize_args) == 0
    first_cli = capsys.readouterr()
    assert first_cli.out == ""

    paper_paths = tuple(sorted((output_dir / "Papers").glob("*.md")))
    author_paths = tuple(sorted((output_dir / "Authors").glob("*.md")))
    assert len(paper_paths) == 2
    assert len(author_paths) == 2

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
        path for path, values in values_by_path.items() if values["doi"] is not None
    )
    no_doi_path = next(
        path for path, values in values_by_path.items() if values["doi"] is None
    )
    doi_values = values_by_path[doi_path]
    assert doi_values["doi"] == "10.1093/biomtc/ujag004"
    assert doi_values["external_ids"]["crossref"] == "10.1093/biomtc/ujag004"
    assert {source["provider"] for source in doi_values["sources"]} == {
        "crossref",
        "openalex",
    }
    assert MISSING_ABSTRACT in no_doi_path.read_text(encoding="utf-8")

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
    replace_once(no_doi_path, "status: candidate\n", "status: rejected\n")
    replace_once(
        no_doi_path,
        "## Notes\n",
        "## Notes\n\nHuman rejected note.\n",
    )

    expected_paths = set(paper_paths)
    expected_ids = dict(ids_by_path)
    expected_authors = set(author_paths)

    assert main(materialize_args) == 0
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
    rejected_contents = no_doi_path.read_text(encoding="utf-8")
    assert frontmatter(doi_path)["status"] == "kept"
    assert frontmatter(doi_path)["reviewer_state"] == {"priority": "high"}
    assert "Human kept note." in kept_contents
    assert "## Review Context\n\nRetain this custom section." in kept_contents
    assert frontmatter(no_doi_path)["status"] == "rejected"
    assert "Human rejected note." in rejected_contents

    assert len(openalex_openers) == 2
    assert len(crossref_openers) == 2
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
        assert urlparse(request.full_url).path == (
            "/v1/works/10.1093%2Fbiomtc%2Fujag004"
        )

    before_export = snapshot_files(output_dir)
    assert main(("export-kept", "--output-dir", str(output_dir))) == 0
    first_export = capsys.readouterr()
    assert first_export.out == "10.1093/biomtc/ujag004\n"
    assert "Kept export completed: 1 entries, 0 issues" in first_export.err
    assert snapshot_files(output_dir) == before_export

    replace_once(doi_path, "status: kept\n", "status: in_zotero\n")
    after_manual_import = snapshot_files(output_dir)
    assert main(("export-kept", "--output-dir", str(output_dir))) == 0
    second_export = capsys.readouterr()
    assert second_export.out == ""
    assert "Kept export completed: 0 entries, 0 issues" in second_export.err
    assert snapshot_files(output_dir) == after_manual_import
