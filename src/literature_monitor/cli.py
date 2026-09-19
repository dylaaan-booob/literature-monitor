"""Command-line entry point for configuration and discovery diagnostics."""

from __future__ import annotations

import argparse
import logging
import os
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from literature_monitor.canonicalize import (
    CanonicalizationIssue,
    canonicalize_records,
    consolidate_evidence,
)
from literature_monitor.config import ConfigurationError, load_config
from literature_monitor.crossref import (
    CrossrefClient,
    CrossrefDiscoveryIssue,
    EnrichmentIssue,
    EnrichmentIssueSeverity,
    discover_crossref_journals,
    enrich_records,
)
from literature_monitor.keywords import (
    KeywordSyntaxError,
    build_searchable_projection,
    evaluate_keyword_expression,
    evaluate_searchable_projection,
    parse_keyword_expression,
)
from literature_monitor.kept_export import export_kept_papers
from literature_monitor.logging_setup import configure_logging
from literature_monitor.materialize import (
    MaterializationIssueSeverity,
    materialize_papers,
)
from literature_monitor.openalex import (
    DiscoveryResult,
    IssueSeverity,
    OpenAlexClient,
    discover_journals,
    resolve_journal_source,
)
from literature_monitor.retrieval import assemble_provider_evidence
from literature_monitor.semantic_scholar import (
    SemanticScholarIssue,
    SemanticScholarIssueSeverity,
    augment_with_semantic_scholar,
    create_semantic_scholar_client,
)


def _date_argument(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"expected YYYY-MM-DD, got {value!r}") from error


def _add_discovery_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--from-date", type=_date_argument, required=True)
    parser.add_argument("--to-date", type=_date_argument, required=True)
    parser.add_argument(
        "--journal",
        help="limit diagnostics to one exact configured journal name",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="literature-monitor")
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser(
        "validate",
        help="validate configuration and OpenAlex venue resolution",
    )
    validate.add_argument("--config", type=Path, required=True)
    discover = subparsers.add_parser(
        "openalex-discover",
        help="diagnose Task 2 OpenAlex discovery (NDJSON output is not a stable export)",
    )
    _add_discovery_arguments(discover)
    crossref_discover = subparsers.add_parser(
        "crossref-discover",
        help="diagnose Crossref journal discovery (NDJSON is not a stable export)",
    )
    _add_discovery_arguments(crossref_discover)
    filter_parser = subparsers.add_parser(
        "openalex-filter",
        help="diagnose Task 3 local keyword filtering (NDJSON is not a stable export)",
    )
    _add_discovery_arguments(filter_parser)
    filter_parser.add_argument(
        "--keyword-expression",
        help="override the configured expression for this diagnostic run only",
    )
    enrich_parser = subparsers.add_parser(
        "crossref-enrich",
        help="diagnose Task 4 Crossref enrichment (NDJSON is not a stable export)",
    )
    _add_discovery_arguments(enrich_parser)
    enrich_parser.add_argument(
        "--keyword-expression",
        help="override the configured expression for this diagnostic run only",
    )
    canonicalize_parser = subparsers.add_parser(
        "canonicalize",
        help="diagnose Task 5 canonicalization (NDJSON is not a stable export)",
    )
    _add_discovery_arguments(canonicalize_parser)
    canonicalize_parser.add_argument(
        "--keyword-expression",
        help="override the configured expression for this diagnostic run only",
    )
    materialize_parser = subparsers.add_parser(
        "materialize",
        help="create or incrementally update Obsidian Paper and Author Markdown files",
    )
    _add_discovery_arguments(materialize_parser)
    materialize_parser.add_argument(
        "--keyword-expression",
        help="override the configured expression for this run only",
    )
    materialize_parser.add_argument("--output-dir", type=Path, required=True)
    export_parser = subparsers.add_parser(
        "export-kept",
        help="export kept Papers from durable Markdown for Zotero import",
    )
    export_parser.add_argument("--output-dir", type=Path, required=True)
    return parser


def _log_openalex_discovery(logger: logging.Logger, result: DiscoveryResult) -> None:
    for source in result.sources:
        logger.info(
            "resolved %s to %s (%s)",
            source.journal,
            source.display_name,
            source.openalex_id,
        )
    for issue in result.issues:
        detail = issue.message
        if issue.issn is not None:
            detail = f"ISSN {issue.issn}: {detail}"
        if issue.record_id is not None:
            detail = f"{issue.record_id}: {detail}"
        log = (
            logger.error
            if issue.severity is IssueSeverity.ERROR
            else logger.warning
        )
        log("%s [%s]: %s", issue.journal, issue.stage, detail)


def _log_crossref_discovery_issues(
    logger: logging.Logger,
    issues: Sequence[CrossrefDiscoveryIssue],
) -> None:
    for issue in issues:
        detail = f"ISSN {issue.issn}: {issue.message}"
        if issue.doi is not None:
            detail = f"DOI {issue.doi}: {detail}"
        if issue.record_id is not None:
            detail = f"{issue.record_id}: {detail}"
        log = (
            logger.error
            if issue.severity is EnrichmentIssueSeverity.ERROR
            else logger.warning
        )
        log("%s [%s]: %s", issue.journal, issue.stage, detail)


def _log_enrichment_issues(
    logger: logging.Logger,
    issues: Sequence[EnrichmentIssue],
) -> None:
    for issue in issues:
        detail = issue.message
        if issue.doi is not None:
            detail = f"DOI {issue.doi}: {detail}"
        if issue.record_id is not None:
            detail = f"{issue.record_id}: {detail}"
        log = (
            logger.error
            if issue.severity is EnrichmentIssueSeverity.ERROR
            else logger.warning
        )
        log("Crossref [%s]: %s", issue.stage, detail)


def _log_canonicalization_issue(
    logger: logging.Logger,
    label: str,
    issue: CanonicalizationIssue,
) -> None:
    detail = issue.message
    if issue.record_ids:
        detail = f"{', '.join(issue.record_ids)}: {detail}"
    logger.warning(
        "%s [%s]: %s",
        label,
        issue.stage,
        detail,
    )


def _log_semantic_scholar_issues(
    logger: logging.Logger,
    issues: Sequence[SemanticScholarIssue],
) -> None:
    for issue in issues:
        detail = issue.message
        if issue.doi is not None:
            detail = f"DOI {issue.doi}: {detail}"
        if issue.paper_id is not None:
            detail = f"{issue.paper_id}: {detail}"
        if issue.journal is not None:
            detail = f"{issue.journal}: {detail}"
        log = (
            logger.error
            if issue.severity is SemanticScholarIssueSeverity.ERROR
            else logger.warning
        )
        log("Semantic Scholar [%s]: %s", issue.stage, detail)


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logger = configure_logging()
    if args.command == "export-kept":
        result = export_kept_papers(args.output_dir)
        for entry in result.entries:
            print(entry)
        for issue in result.issues:
            logger.error("Kept export [%s]: %s", issue.path, issue.message)
        logger.info(
            "Kept export completed: %d entries, %d issues",
            len(result.entries),
            len(result.issues),
        )
        return 1 if result.has_errors else 0
    if args.command == "validate":
        try:
            config = load_config(args.config)
        except ConfigurationError as error:
            logger.error("%s", error)
            return 2

        logger = configure_logging(config.log_level.value)
        issn_count = sum(len(journal.issn) for journal in config.journals)
        client = OpenAlexClient(api_key=os.environ.get("OPENALEX_API_KEY"))
        resolved_count = 0
        warning_count = 0
        error_count = 0
        for journal in config.journals:
            source, issues = resolve_journal_source(client, journal)
            if source is not None:
                resolved_count += 1
                logger.info(
                    "resolved %s to %s (%s)",
                    source.journal,
                    source.display_name,
                    source.openalex_id,
                )
            for issue in issues:
                detail = issue.message
                if issue.issn is not None:
                    detail = f"ISSN {issue.issn}: {detail}"
                log = (
                    logger.error
                    if issue.severity is IssueSeverity.ERROR
                    else logger.warning
                )
                log("%s [%s]: %s", issue.journal, issue.stage, detail)
                if issue.severity is IssueSeverity.ERROR:
                    error_count += 1
                else:
                    warning_count += 1
        logger.info(
            "Validation completed: %d configured journals, %d configured ISSNs, "
            "%d resolved sources, %d warnings, %d errors",
            len(config.journals),
            issn_count,
            resolved_count,
            warning_count,
            error_count,
        )
        return 1 if error_count else 0
    if args.command in {
        "openalex-discover",
        "openalex-filter",
        "crossref-discover",
        "crossref-enrich",
        "canonicalize",
        "materialize",
    }:
        try:
            config = load_config(args.config)
        except ConfigurationError as error:
            logger.error("%s", error)
            return 2
        logger = configure_logging(config.log_level.value)

        keyword_ast = config.keyword_ast
        if (
            args.command
            in {"openalex-filter", "crossref-enrich", "canonicalize", "materialize"}
            and args.keyword_expression is not None
        ):
            try:
                keyword_ast = parse_keyword_expression(args.keyword_expression)
            except KeywordSyntaxError as error:
                logger.error("--keyword-expression: %s", error)
                return 2

        if args.from_date > args.to_date:
            logger.error("--from-date must not be after --to-date")
            return 2

        journals = config.journals
        if args.journal is not None:
            journals = tuple(
                journal
                for journal in journals
                if journal.name.casefold() == args.journal.strip().casefold()
            )
            if not journals:
                logger.error("unknown configured journal %r", args.journal)
                return 2

        crossref_client = None
        if args.command in {
            "crossref-discover",
            "crossref-enrich",
            "canonicalize",
            "materialize",
        }:
            crossref_client = CrossrefClient(
                mailto=os.environ.get("CROSSREF_MAILTO")
            )

        if args.command == "crossref-discover":
            assert crossref_client is not None
            discovery = discover_crossref_journals(
                crossref_client,
                journals,
                args.from_date,
                args.to_date,
            )
            _log_crossref_discovery_issues(logger, discovery.issues)
            for record in discovery.records:
                print(record.model_dump_json())
            logger.info(
                "Crossref diagnostic completed: %d records, %d issues",
                len(discovery.records),
                len(discovery.issues),
            )
            return 1 if discovery.has_errors else 0

        openalex_client = OpenAlexClient(
            api_key=os.environ.get("OPENALEX_API_KEY")
        )
        openalex = discover_journals(
            openalex_client,
            journals,
            args.from_date,
            args.to_date,
        )
        _log_openalex_discovery(logger, openalex)

        if args.command == "openalex-discover":
            for record in openalex.records:
                print(record.model_dump_json())
            logger.info(
                "OpenAlex diagnostic completed: %d sources, %d records, %d issues",
                len(openalex.sources),
                len(openalex.records),
                len(openalex.issues),
            )
            return 1 if openalex.has_errors else 0

        if args.command in {"openalex-filter", "crossref-enrich"}:
            filtered = tuple(
                record
                for record in openalex.records
                if evaluate_keyword_expression(keyword_ast, record.metadata)
            )
            if args.command == "openalex-filter":
                for record in filtered:
                    print(record.model_dump_json())
                logger.info(
                    "OpenAlex filter diagnostic completed: %d discovered, "
                    "%d retained, %d filtered out, %d issues",
                    len(openalex.records),
                    len(filtered),
                    len(openalex.records) - len(filtered),
                    len(openalex.issues),
                )
                return 1 if openalex.has_errors else 0

            assert crossref_client is not None
            enrichment = enrich_records(crossref_client, filtered)
            _log_enrichment_issues(logger, enrichment.issues)
            for record in enrichment.records:
                print(record.model_dump_json())
            enriched_count = sum(
                record.crossref is not None for record in enrichment.records
            )
            logger.info(
                "Crossref enrichment diagnostic completed: %d discovered, "
                "%d retained, %d enriched, %d without DOI, %d unavailable, "
                "%d failed, %d OpenAlex issues, %d Crossref issues",
                len(openalex.records),
                len(filtered),
                enriched_count,
                sum(issue.stage == "missing_doi" for issue in enrichment.issues),
                sum(issue.stage == "not_found" for issue in enrichment.issues),
                sum(
                    issue.severity is EnrichmentIssueSeverity.ERROR
                    for issue in enrichment.issues
                ),
                len(openalex.issues),
                len(enrichment.issues),
            )
            return 1 if openalex.has_errors or enrichment.has_errors else 0

        assert crossref_client is not None
        crossref = discover_crossref_journals(
            crossref_client,
            journals,
            args.from_date,
            args.to_date,
        )
        _log_crossref_discovery_issues(logger, crossref.issues)
        retrieval = assemble_provider_evidence(
            crossref_client,
            openalex.records,
            crossref.records,
        )
        _log_enrichment_issues(logger, retrieval.issues)
        semantic_scholar_client = create_semantic_scholar_client(
            os.environ.get("SEMANTIC_SCHOLAR_API_KEY")
        )
        semantic_scholar = augment_with_semantic_scholar(
            semantic_scholar_client,
            retrieval.evidence,
            journals,
            args.from_date,
            args.to_date,
            keyword_ast,
        )
        _log_semantic_scholar_issues(logger, semantic_scholar.issues)
        all_evidence = (*retrieval.evidence, *semantic_scholar.evidence)
        consolidation = consolidate_evidence(all_evidence)
        for issue in consolidation.issues:
            _log_canonicalization_issue(logger, "Consolidation", issue)
        retained_clusters = tuple(
            cluster
            for cluster in consolidation.clusters
            if evaluate_searchable_projection(
                keyword_ast,
                build_searchable_projection(cluster.evidence),
            )
        )
        retained_evidence = tuple(
            evidence
            for cluster in retained_clusters
            for evidence in cluster.evidence
        )
        canonicalization = canonicalize_records(retained_evidence)
        for issue in canonicalization.issues:
            _log_canonicalization_issue(logger, "Canonicalization", issue)

        provider_errors = (
            openalex.has_errors
            or crossref.has_errors
            or retrieval.has_errors
            or semantic_scholar.has_errors
        )
        provider_issue_count = (
            len(openalex.issues)
            + len(crossref.issues)
            + len(retrieval.issues)
            + len(semantic_scholar.issues)
        )
        if args.command == "canonicalize":
            for paper in canonicalization.papers:
                print(paper.model_dump_json())
            logger.info(
                "Canonicalization diagnostic completed: %d OpenAlex records, "
                "%d Crossref discovery records, %d Crossref DOI supplement "
                "records, %d Semantic Scholar batch supplement records, "
                "%d Semantic Scholar discovery records, %d evidence clusters, "
                "%d retained clusters, %d canonical papers, %d consolidation "
                "issues, %d canonicalization issues, %d provider issues",
                len(openalex.records),
                len(crossref.records),
                len(retrieval.supplement_records),
                len(semantic_scholar.supplement_records),
                len(semantic_scholar.discovered_records),
                len(consolidation.clusters),
                len(retained_clusters),
                len(canonicalization.papers),
                len(consolidation.issues),
                len(canonicalization.issues),
                provider_issue_count,
            )
            return 1 if provider_errors else 0

        materialization = materialize_papers(
            canonicalization.papers,
            args.output_dir,
        )
        for issue in materialization.issues:
            log = (
                logger.error
                if issue.severity is MaterializationIssueSeverity.ERROR
                else logger.warning
            )
            log("Materialization [%s]: %s", issue.path, issue.message)
        logger.info(
            "Materialization completed: %d OpenAlex records, %d Crossref "
            "discovery records, %d Crossref DOI supplement records, %d Semantic "
            "Scholar batch supplement records, %d Semantic Scholar discovery "
            "records, %d evidence clusters, %d retained clusters, %d canonical papers, "
            "%d paper files created, %d paper files matched, %d paper files updated, "
            "%d author files created, %d author files existing, %d materialization "
            "issues, %d consolidation issues, %d canonicalization issues, %d "
            "provider issues",
            len(openalex.records),
            len(crossref.records),
            len(retrieval.supplement_records),
            len(semantic_scholar.supplement_records),
            len(semantic_scholar.discovered_records),
            len(consolidation.clusters),
            len(retained_clusters),
            len(canonicalization.papers),
            len(materialization.created_papers),
            len(materialization.existing_papers),
            len(materialization.updated_papers),
            len(materialization.created_authors),
            len(materialization.existing_authors),
            len(materialization.issues),
            len(consolidation.issues),
            len(canonicalization.issues),
            provider_issue_count,
        )
        return 1 if provider_errors or materialization.has_errors else 0
    return 2
