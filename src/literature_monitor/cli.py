"""Command-line entry point for configuration and discovery diagnostics."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from literature_monitor.application.monitor import (
    MonitorIssue,
    MonitorIssueComponent,
    MonitorIssueSeverity,
    RunResult,
    RunOutcome,
    ValidationOutcome,
    _CanonicalCoreResult,
    _materialize_canonical_result,
    _run_canonical_core,
    run_monitor,
    validate_monitor,
)
from literature_monitor.cli_progress import _CliProgressRenderer
from literature_monitor.config import ConfigurationError, load_config
from literature_monitor.crossref import (
    CrossrefClient,
    CrossrefDiscoveryIssue,
    EnrichmentIssue,
    EnrichmentIssueSeverity,
    discover_crossref_journals,
    enrich_records,
)
from literature_monitor.date_range import (
    DateRangeError,
    DateRangeSpec,
    ResolvedDateRange,
    resolve_date_range,
)
from literature_monitor.keywords import (
    KeywordExpression,
    KeywordSyntaxError,
    parse_keyword_expression,
)
from literature_monitor.kept_export import export_kept_papers
from literature_monitor.logging_setup import configure_logging
from literature_monitor.openalex import (
    DiscoveryResult,
    IssueSeverity,
    OpenAlexClient,
    ResolvedSource,
    discover_journals,
)
from literature_monitor.search import (
    SearchBackendError,
    SearchExpressionError,
    SearchableProjection,
    build_metadata_searchable_projection,
    match_searchable_projections,
    validate_search_expression,
)

_GUI_HOST = "127.0.0.1"
_GUI_PORT = 8000
_GUI_WORKERS = 1


def _date_argument(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"expected YYYY-MM-DD, got {value!r}") from error


def _add_monitor_date_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--from-date", type=_date_argument)
    parser.add_argument("--to-date", type=_date_argument)
    parser.add_argument("--window-days", type=int)


def _add_discovery_arguments(parser: argparse.ArgumentParser) -> None:
    _add_monitor_date_arguments(parser)
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
    gui_parser = subparsers.add_parser(
        "gui",
        help="launch the local Web UI",
    )
    gui_parser.add_argument("--config", type=Path, required=True)
    run_parser = subparsers.add_parser(
        "run",
        help="run a persistent monitor and materialize its workspace",
    )
    _add_monitor_date_arguments(run_parser)
    discover = subparsers.add_parser(
        "openalex-discover",
        help="diagnose OpenAlex discovery (NDJSON output is not a stable export)",
    )
    _add_discovery_arguments(discover)
    crossref_discover = subparsers.add_parser(
        "crossref-discover",
        help="diagnose Crossref journal discovery (NDJSON is not a stable export)",
    )
    _add_discovery_arguments(crossref_discover)
    filter_parser = subparsers.add_parser(
        "openalex-filter",
        help="diagnose local keyword filtering (NDJSON is not a stable export)",
    )
    _add_discovery_arguments(filter_parser)
    filter_parser.add_argument(
        "--keyword-expression",
        help="override the configured expression for this diagnostic run only",
    )
    enrich_parser = subparsers.add_parser(
        "crossref-enrich",
        help="diagnose Crossref DOI enrichment (NDJSON is not a stable export)",
    )
    _add_discovery_arguments(enrich_parser)
    enrich_parser.add_argument(
        "--keyword-expression",
        help="override the configured expression for this diagnostic run only",
    )
    canonicalize_parser = subparsers.add_parser(
        "canonicalize",
        help="diagnose canonicalization (NDJSON is not a stable export)",
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


def _log_monitor_source(logger: logging.Logger, source: ResolvedSource) -> None:
    logger.info(
        "resolved %s to %s (%s)",
        source.journal,
        source.display_name,
        source.openalex_id,
    )


def _log_monitor_issue(
    logger: logging.Logger,
    issue: MonitorIssue,
    *,
    cli_date_fields: bool = False,
) -> None:
    log = (
        logger.error
        if issue.severity is MonitorIssueSeverity.ERROR
        else logger.warning
    )
    if issue.component is MonitorIssueComponent.OPENALEX:
        detail = issue.message
        if issue.issn is not None:
            detail = f"ISSN {issue.issn}: {detail}"
        if issue.record_id is not None:
            detail = f"{issue.record_id}: {detail}"
        log("%s [%s]: %s", issue.journal, issue.stage, detail)
        return
    if issue.component is MonitorIssueComponent.CROSSREF_DISCOVERY:
        detail = f"ISSN {issue.issn}: {issue.message}"
        if issue.doi is not None:
            detail = f"DOI {issue.doi}: {detail}"
        if issue.record_id is not None:
            detail = f"{issue.record_id}: {detail}"
        log("%s [%s]: %s", issue.journal, issue.stage, detail)
        return
    if issue.component is MonitorIssueComponent.CROSSREF_SUPPLEMENT:
        detail = issue.message
        if issue.doi is not None:
            detail = f"DOI {issue.doi}: {detail}"
        if issue.record_id is not None:
            detail = f"{issue.record_id}: {detail}"
        log("Crossref [%s]: %s", issue.stage, detail)
        return
    if issue.component in {
        MonitorIssueComponent.CONSOLIDATION,
        MonitorIssueComponent.CANONICALIZATION,
    }:
        detail = issue.message
        if issue.record_ids:
            detail = f"{', '.join(issue.record_ids)}: {detail}"
        label = (
            "Consolidation"
            if issue.component is MonitorIssueComponent.CONSOLIDATION
            else "Canonicalization"
        )
        log("%s [%s]: %s", label, issue.stage, detail)
        return
    if issue.component is MonitorIssueComponent.MATERIALIZATION:
        log("Materialization [%s]: %s", issue.path, issue.message)
        return
    if issue.component is MonitorIssueComponent.SEARCH:
        if issue.stage == "fts5_backend":
            log("Local search / FTS5 backend failure: %s", issue.message)
        elif issue.stage == "keyword_override":
            log("--keyword-expression: %s", issue.message)
        elif issue.config_path is not None:
            log(
                "%s: field 'keyword_expression': %s",
                issue.config_path,
                issue.message,
            )
        else:
            log("%s", issue.message)
        return
    if issue.component is MonitorIssueComponent.DATE_RANGE:
        message = issue.message
        field = issue.field or "date_range"
        if cli_date_fields:
            for source, flag in (
                ("from_date", "--from-date"),
                ("to_date", "--to-date"),
                ("window_days", "--window-days"),
            ):
                message = message.replace(source, flag)
            log("%s", message)
        elif issue.config_path is not None:
            log("%s: field '%s': %s", issue.config_path, field, message)
        else:
            log("%s", message)
        return
    log("%s", issue.message)


def _log_monitor_issues(
    logger: logging.Logger,
    warnings: Sequence[MonitorIssue],
    errors: Sequence[MonitorIssue],
    *,
    cli_date_fields: bool = False,
) -> None:
    for issue in (*warnings, *errors):
        _log_monitor_issue(logger, issue, cli_date_fields=cli_date_fields)


def _log_canonicalization_summary(
    logger: logging.Logger,
    result: _CanonicalCoreResult,
) -> None:
    stats = result.statistics
    logger.info(
        "Canonicalization diagnostic completed: %d OpenAlex records, "
        "%d Crossref discovery records, %d Crossref DOI supplement records, "
        "%d evidence clusters, %d retained clusters, %d canonical papers, "
        "%d consolidation issues, %d canonicalization issues, %d provider issues",
        stats.openalex_records,
        stats.crossref_discovery_records,
        stats.crossref_supplement_records,
        stats.evidence_clusters,
        stats.retained_clusters,
        len(result.papers),
        stats.consolidation_issues,
        stats.canonicalization_issues,
        stats.provider_issues,
    )


def _log_materialization_summary(
    logger: logging.Logger,
    result: RunResult,
) -> None:
    stats = result.statistics
    logger.info(
        "Materialization completed: %d OpenAlex records, %d Crossref "
        "discovery records, %d Crossref DOI supplement records, %d evidence "
        "clusters, %d retained clusters, %d canonical papers, %d paper files "
        "created, %d paper files matched, %d paper files updated, %d author "
        "files created, %d author files existing, %d materialization issues, "
        "%d consolidation issues, %d canonicalization issues, %d provider issues",
        stats.openalex_records,
        stats.crossref_discovery_records,
        stats.crossref_supplement_records,
        stats.evidence_clusters,
        stats.retained_clusters,
        result.canonical_paper_count,
        result.created_papers,
        result.matched_existing_papers,
        result.updated_papers,
        result.created_authors,
        result.existing_authors,
        stats.materialization_issues,
        stats.consolidation_issues,
        stats.canonicalization_issues,
        stats.provider_issues,
    )


def _match_local_search(
    logger: logging.Logger,
    expression: KeywordExpression,
    projections: Sequence[SearchableProjection],
) -> tuple[bool, ...] | None:
    try:
        return match_searchable_projections(expression, projections)
    except SearchExpressionError as error:
        logger.error("Invalid local search expression: %s", error)
        return None
    except SearchBackendError as error:
        logger.error("Local search / FTS5 backend failure: %s", error)
        return None


def _resolve_invocation_date_range(
    args: argparse.Namespace,
    config_spec: DateRangeSpec,
) -> ResolvedDateRange:
    override = _date_override_from_args(args)
    return resolve_date_range(override or config_spec, today=date.today())


def _date_override_from_args(args: argparse.Namespace) -> DateRangeSpec | None:
    has_cli_override = (
        args.from_date is not None
        or args.to_date is not None
        or args.window_days is not None
    )
    if not has_cli_override:
        return None
    return DateRangeSpec(
        from_date=args.from_date,
        to_date=args.to_date,
        window_days=args.window_days,
    )


def _format_cli_date_error(error: DateRangeError) -> str:
    message = str(error)
    for field, flag in (
        ("from_date", "--from-date"),
        ("to_date", "--to-date"),
        ("window_days", "--window-days"),
    ):
        message = message.replace(field, flag)
    return message


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logger = configure_logging()
    if args.command == "gui":
        import uvicorn

        from literature_monitor.web.app import create_app

        app = create_app(args.config)
        logger.info("Local Web UI: http://%s:%d", _GUI_HOST, _GUI_PORT)
        uvicorn.run(
            app,
            host=_GUI_HOST,
            port=_GUI_PORT,
            workers=_GUI_WORKERS,
        )
        return 0
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
        progress = _CliProgressRenderer(
            sys.stderr,
            show_run_stages=False,
        )
        try:
            validation = validate_monitor(
                args.config,
                progress_callback=progress,
            )
        finally:
            progress.close()
        if validation.log_level is not None:
            logger = configure_logging(validation.log_level.value)
        for source in validation.resolved_sources:
            _log_monitor_source(logger, source)
        _log_monitor_issues(logger, validation.warnings, validation.errors)
        if validation.outcome is ValidationOutcome.INVALID_CONFIGURATION:
            return 2
        logger.info(
            "Validation completed: %d configured journals, %d configured ISSNs, "
            "%d resolved sources, %d warnings, %d errors",
            validation.configured_journal_count,
            validation.configured_issn_count,
            len(validation.resolved_sources),
            len(validation.warnings),
            len(validation.errors),
        )
        return 1 if validation.outcome is ValidationOutcome.SOURCE_ERRORS else 0

    if args.command == "run":
        progress = _CliProgressRenderer(
            sys.stderr,
            show_run_stages=True,
        )
        try:
            result = run_monitor(
                args.config,
                date_override=_date_override_from_args(args),
                progress_callback=progress,
            )
        finally:
            progress.close()
        if result.log_level is not None:
            logger = configure_logging(result.log_level.value)
        for source in result.resolved_sources:
            _log_monitor_source(logger, source)
        _log_monitor_issues(
            logger,
            result.warnings,
            result.errors,
            cli_date_fields=True,
        )
        if result.outcome is RunOutcome.INVALID_CONFIGURATION:
            return 2
        _log_materialization_summary(logger, result)
        return 1 if result.outcome is RunOutcome.COMPLETED_WITH_ERRORS else 0

    if args.command in {"canonicalize", "materialize"}:
        core = _run_canonical_core(
            args.config,
            date_override=_date_override_from_args(args),
            journal_name=args.journal,
            keyword_expression=args.keyword_expression,
        )
        if core.log_level is not None:
            logger = configure_logging(core.log_level.value)
        if core.outcome is RunOutcome.INVALID_CONFIGURATION:
            _log_monitor_issues(
                logger,
                core.warnings,
                core.errors,
                cli_date_fields=True,
            )
            return 2
        if args.command == "canonicalize":
            for source in core.resolved_sources:
                _log_monitor_source(logger, source)
            _log_monitor_issues(
                logger,
                core.warnings,
                core.errors,
                cli_date_fields=True,
            )
            for paper in core.papers:
                print(paper.model_dump_json())
            _log_canonicalization_summary(logger, core)
            return 1 if core.errors else 0

        result = _materialize_canonical_result(core, args.output_dir)
        for source in result.resolved_sources:
            _log_monitor_source(logger, source)
        _log_monitor_issues(
            logger,
            result.warnings,
            result.errors,
            cli_date_fields=True,
        )
        _log_materialization_summary(logger, result)
        return 1 if result.outcome is RunOutcome.COMPLETED_WITH_ERRORS else 0

    if args.command in {
        "openalex-discover",
        "openalex-filter",
        "crossref-discover",
        "crossref-enrich",
    }:
        try:
            config = load_config(args.config)
        except ConfigurationError as error:
            logger.error("%s", error)
            return 2
        logger = configure_logging(config.log_level.value)

        keyword_ast = config.keyword_ast
        if args.command in {
            "openalex-filter",
            "crossref-enrich",
        }:
            try:
                validate_search_expression(config.keyword_ast)
            except SearchExpressionError as error:
                logger.error(
                    "%s: field 'keyword_expression': %s",
                    args.config,
                    error,
                )
                return 2
            except SearchBackendError as error:
                logger.error("Local search / FTS5 backend failure: %s", error)
                return 2

            if args.keyword_expression is not None:
                try:
                    keyword_ast = parse_keyword_expression(args.keyword_expression)
                except KeywordSyntaxError as error:
                    logger.error("--keyword-expression: %s", error)
                    return 2
                try:
                    validate_search_expression(keyword_ast)
                except SearchExpressionError as error:
                    logger.error("--keyword-expression: %s", error)
                    return 2
                except SearchBackendError as error:
                    logger.error("Local search / FTS5 backend failure: %s", error)
                    return 2

        try:
            resolved_date_range = _resolve_invocation_date_range(
                args,
                config.date_spec,
            )
        except DateRangeError as error:
            logger.error("%s", _format_cli_date_error(error))
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
        }:
            crossref_client = CrossrefClient(
                mailto=os.environ.get("CROSSREF_MAILTO")
            )

        if args.command == "crossref-discover":
            assert crossref_client is not None
            discovery = discover_crossref_journals(
                crossref_client,
                journals,
                resolved_date_range.from_date,
                resolved_date_range.to_date,
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
            resolved_date_range.from_date,
            resolved_date_range.to_date,
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
            records = openalex.records
            projections = tuple(
                build_metadata_searchable_projection(record.metadata)
                for record in records
            )
            matches = _match_local_search(logger, keyword_ast, projections)
            if matches is None:
                return 2
            filtered = tuple(
                record
                for record, matched in zip(records, matches, strict=True)
                if matched
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
    return 2
