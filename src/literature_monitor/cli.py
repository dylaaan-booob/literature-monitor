"""Command-line entry point for offline Task 1 validation."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from literature_monitor.config import ConfigurationError, load_config
from literature_monitor.logging_setup import configure_logging


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="literature-monitor")
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate", help="validate local configuration")
    validate.add_argument("--config", type=Path, required=True)
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
    return 2
