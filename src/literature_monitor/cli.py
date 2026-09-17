"""Command-line entry point for configuration and discovery diagnostics."""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from literature_monitor.config import ConfigurationError, load_config
from literature_monitor.logging_setup import configure_logging
from literature_monitor.openalex import (
    IssueSeverity,
    OpenAlexClient,
    discover_journals,
)


def _date_argument(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"expected YYYY-MM-DD, got {value!r}") from error


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="literature-monitor")
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate", help="validate local configuration")
    validate.add_argument("--config", type=Path, required=True)
    discover = subparsers.add_parser(
        "openalex-discover",
        help="diagnose Task 2 OpenAlex discovery (NDJSON output is not a stable export)",
    )
    discover.add_argument("--config", type=Path, required=True)
    discover.add_argument("--from-date", type=_date_argument, required=True)
    discover.add_argument("--to-date", type=_date_argument, required=True)
    discover.add_argument(
        "--journal",
        help="limit diagnostics to one exact configured journal name",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logger = configure_logging()
    if args.command == "validate":
        try:
            config = load_config(args.config)
        except ConfigurationError as error:
            logger.error("%s", error)
            return 2

        logger = configure_logging(config.log_level.value)
        issn_count = sum(len(journal.issn) for journal in config.journals)
        logger.info(
            "validated %d journals and %d ISSNs from %s",
            len(config.journals),
            issn_count,
            config.venue_whitelist,
        )
        return 0
    if args.command == "openalex-discover":
        try:
            config = load_config(args.config)
        except ConfigurationError as error:
            logger.error("%s", error)
            return 2
        logger = configure_logging(config.log_level.value)
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

        client = OpenAlexClient(api_key=os.environ.get("OPENALEX_API_KEY"))
        result = discover_journals(
            client,
            journals,
            args.from_date,
            args.to_date,
        )
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
            log = logger.error if issue.severity is IssueSeverity.ERROR else logger.warning
            log("%s [%s]: %s", issue.journal, issue.stage, detail)
        for record in result.records:
            print(record.model_dump_json())
        logger.info(
            "OpenAlex diagnostic completed: %d sources, %d records, %d issues",
            len(result.sources),
            len(result.records),
            len(result.issues),
        )
        return 1 if result.has_errors else 0
    return 2
