#!/usr/bin/env python3
"""Pre-deploy gate for staged Market Tracker HTML.

Fail closed: missing file, undersize stub, missing page markers, or
secret-like substrings abort with exit 1.

Usage: uv run python scripts/dashboard_check.py PATH
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

MIN_BYTES = 20_000
TITLE_MARKER = "<title>ccquant — Market Tracker</title>"
LIVE_MARKER = "live-candle-plot"

# Case-insensitive. Keep in sync with tests/test_dashboard_deploy.py.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"sk-[A-Za-z0-9]{10,}",
        r"AKIA[0-9A-Z]{16}",
        r"BITCOIN_IS_DATA",
        r"FRED_API",
        r"CG_DEMO",
        r"BEGIN PRIVATE KEY",
        r"\.duckdb",
        r"\.env",
        r"FRED_API_KEY=",
        r"BITCOIN_IS_DATA_KEY=",
    )
)
_PATH_LEAK = re.compile(
    r"/Users/[^\s\"']+\.(?:duckdb|env)",
    re.IGNORECASE,
)


class DashboardCheckError(Exception):
    """Staged HTML failed a deploy gate."""


def check_dashboard_html(path: Path) -> None:
    """Raise DashboardCheckError if ``path`` is not safe to publish."""
    if not path.is_file():
        raise DashboardCheckError(f"missing dashboard HTML: {path}")
    size = path.stat().st_size
    if size < MIN_BYTES:
        raise DashboardCheckError(
            f"dashboard HTML too small ({size} bytes, need >= {MIN_BYTES}): {path}"
        )
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise DashboardCheckError(f"dashboard HTML is not UTF-8: {path}") from exc
    if TITLE_MARKER not in text:
        raise DashboardCheckError(f"missing title marker: {path}")
    if LIVE_MARKER not in text:
        raise DashboardCheckError(f"missing live-tape marker: {path}")
    for pat in _SECRET_PATTERNS:
        if pat.search(text):
            raise DashboardCheckError(
                f"secret-like pattern {pat.pattern!r} in {path}"
            )
    if _PATH_LEAK.search(text):
        raise DashboardCheckError(f"workspace path leak (.duckdb/.env) in {path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="Staged HTML path")
    args = parser.parse_args(argv)
    try:
        check_dashboard_html(args.path)
    except DashboardCheckError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(args.path.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
