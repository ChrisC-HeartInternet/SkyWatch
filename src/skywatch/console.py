"""Console output.

Two modes: pretty (rich, for humans) and quiet (JSON only, for launchd runs).
Everything human-facing goes to stderr so that `--json` keeps stdout clean and pipeable.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

from rich.console import Console
from rich.logging import RichHandler

_err = Console(stderr=True)
_out = Console()

_json_mode = False


def setup(json_mode: bool = False, verbose: bool = False) -> None:
    """Configure logging. In JSON mode, human chatter is suppressed to warnings."""
    global _json_mode
    _json_mode = json_mode
    level = logging.DEBUG if verbose else (logging.WARNING if json_mode else logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=_err, rich_tracebacks=True, show_path=verbose)],
        force=True,
    )


def log() -> logging.Logger:
    return logging.getLogger("skywatch")


def is_json_mode() -> bool:
    return _json_mode


def err() -> Console:
    """Console for human-facing output (stderr)."""
    return _err


def out() -> Console:
    """Console for primary output (stdout)."""
    return _out


def emit_json(payload: Any) -> None:
    """Write a machine-readable result to stdout."""
    json.dump(payload, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")
