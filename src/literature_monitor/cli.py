"""Launch the local Web UI. Journal Run is available through the UI only."""
from __future__ import annotations
import argparse
from collections.abc import Sequence
from pathlib import Path

_GUI_HOST = "127.0.0.1"
_GUI_PORT = 8000
_GUI_WORKERS = 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="literature-monitor")
    commands = parser.add_subparsers(dest="command", required=True)
    gui = commands.add_parser("gui", help="launch the local Web UI")
    gui.add_argument("--config", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    import uvicorn
    from literature_monitor.logging_setup import configure_logging
    from literature_monitor.web.app import create_app
    logger = configure_logging()
    logger.info("Local Web UI: http://%s:%d", _GUI_HOST, _GUI_PORT)
    uvicorn.run(create_app(args.config), host=_GUI_HOST, port=_GUI_PORT, workers=_GUI_WORKERS)
    return 0
