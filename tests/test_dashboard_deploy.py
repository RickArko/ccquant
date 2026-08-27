"""Deploy-contract tests for the static Market Tracker (no live Fly/HTTP)."""

from __future__ import annotations

import importlib.util
from datetime import date, timedelta
from pathlib import Path
from types import ModuleType

import polars as pl
import pytest

from ccquant.dashboard import build_snapshot_from_panels, render_dashboard_html

ROOT = Path(__file__).resolve().parents[1]
CONNECT_SRC_HOSTS = (
    "https://data-api.binance.vision",
    "https://api.binance.com",
    "https://api.binance.us",
    "https://api.coinbase.com",
    "https://api.exchange.coinbase.com",
)


def _synthetic_daily(
    *,
    n_days: int = 260,
    n_symbols: int = 8,
    end: date | None = None,
) -> pl.DataFrame:
    end = end or date(2026, 7, 18)
    rows: list[dict[str, object]] = []
    symbols = ["BTC", "ETH"] + [f"A{i}" for i in range(n_symbols - 2)]
    for i in range(n_days):
        d = end - timedelta(days=n_days - 1 - i)
        for j, sym in enumerate(symbols):
            base = 50_000.0 if sym == "BTC" else (2_000.0 if sym == "ETH" else 10.0)
            drift = 1.0 + (0.001 if sym == "BTC" else (-0.002 if j % 2 else 0.0005))
            close = base * (drift**i)
            rows.append(
                {
                    "symbol": sym,
                    "date": d,
                    "open": close,
                    "high": close,
                    "low": close,
                    "close": close,
                    "volume": 1_000.0,
                    "source": "test",
                }
            )
    return pl.DataFrame(rows)


def _load_dashboard_check() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "dashboard_check",
        ROOT / "scripts" / "dashboard_check.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_dashboard_check = _load_dashboard_check()
check_dashboard_html = _dashboard_check.check_dashboard_html
DashboardCheckError = _dashboard_check.DashboardCheckError


def test_dashboard_check_rejects_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "nope.html"
    with pytest.raises(DashboardCheckError, match="missing"):
        check_dashboard_html(missing)


def test_dashboard_check_rejects_secret_like_html(tmp_path: Path) -> None:
    pytest.importorskip("plotly")
    page = render_dashboard_html(build_snapshot_from_panels(_synthetic_daily()))
    staged = tmp_path / "index.html"
    staged.write_text(page + "\nFRED_API_KEY=not-a-real-key\n", encoding="utf-8")
    with pytest.raises(DashboardCheckError, match="secret-like"):
        check_dashboard_html(staged)


def test_dashboard_check_accepts_synthetic_render(tmp_path: Path) -> None:
    pytest.importorskip("plotly")
    page = render_dashboard_html(build_snapshot_from_panels(_synthetic_daily()))
    staged = tmp_path / "index.html"
    staged.write_text(page, encoding="utf-8")
    check_dashboard_html(staged)


def test_nginx_conf_has_healthz_and_security_headers() -> None:
    conf = (ROOT / "deploy" / "nginx.conf").read_text(encoding="utf-8")
    assert "healthz" in conf
    assert "X-Frame-Options" in conf
    assert "Content-Security-Policy" in conf
    assert "listen 8080" in conf
    assert "no-cache" in conf
    for host in CONNECT_SRC_HOSTS:
        assert host in conf


def test_dockerfile_is_nginx_not_python() -> None:
    text = (ROOT / "deploy" / "Dockerfile").read_text(encoding="utf-8")
    assert "FROM nginx:1.30.4-alpine" in text
    lowered = text.lower()
    assert "src/ccquant" not in lowered
    assert "duckdb" not in lowered
    assert "uv sync" not in lowered
    assert "1.27" not in text


def test_fly_toml_autostop_and_domain_port() -> None:
    text = (ROOT / "fly.toml").read_text(encoding="utf-8")
    assert 'app = "ccquant-btc"' in text
    assert "internal_port = 8080" in text
    assert "min_machines_running = 0" in text
    assert 'auto_stop_machines = "stop"' in text


def test_gitignore_excludes_staged_html() -> None:
    text = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "deploy/public/*.html" in text


def test_refresh_script_is_lean_and_deploys() -> None:
    text = (ROOT / "scripts" / "dashboard_refresh.sh").read_text(encoding="utf-8")
    assert "--no-wallets" in text
    assert "--no-tweets" in text
    assert "--no-depth" in text
    assert "--no-mev" in text
    assert "make dashboard.deploy" in text


def test_launchd_plist_is_twice_daily() -> None:
    text = (ROOT / "deploy" / "com.ccquant.dashboard-refresh.plist.in").read_text(
        encoding="utf-8"
    )
    assert "StartCalendarInterval" in text
    assert "<integer>2</integer>" in text
    assert "<integer>18</integer>" in text
    assert "dashboard_refresh.sh" in text
    assert "__CCQUANT_ROOT__" in text
    assert "__LAUNCH_LABEL__" in text
    assert "<string>com.ccquant.dashboard-refresh</string>" not in text


def test_makefile_has_dashboard_refresh() -> None:
    text = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "dashboard.refresh" in text
    assert "dashboard.schedule" in text
    assert "scripts/dashboard_refresh.sh" in text
    assert "DASHBOARD_STAGE := deploy/public/index.html" in text
    assert "DASHBOARD_OUT" not in text
    assert "__LAUNCH_LABEL__|$(LAUNCH_LABEL)" in text
