from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]


@dataclass(frozen=True)
class DailyConfig:
    tail_days: int = 7


@dataclass(frozen=True)
class HourlyConfig:
    enabled: bool = True
    top: int = 10
    history_days: int = 365
    tail_hours: int = 168


@dataclass(frozen=True)
class OpenInterestConfig:
    enabled: bool = True
    history_days: int = 365
    tail_hours: int = 168
    request_delay_seconds: float = 0.25
    binance: bool = True
    bybit: bool = True
    okx: bool = True


FRED_SERIES: list[str] = [
    "M2SL",
    "WALCL",
    "DGS10",
    "DGS2",
    "T10YIE",
    "FEDFUNDS",
    "DTWEXBGS",
    "VIXCLS",
]


@dataclass(frozen=True)
class MacroConfig:
    enabled: bool = True
    series_ids: list[str] = field(default_factory=lambda: list(FRED_SERIES))
    request_delay_seconds: float = 1.0


@dataclass(frozen=True)
class UniverseConfig:
    size: int = 100
    include_symbols: list[str] = field(default_factory=list)
    source_preference: str = "binance"
    request_delay_seconds: float = 0.25


@dataclass(frozen=True)
class AppConfig:
    database: Path
    universe: UniverseConfig = field(default_factory=UniverseConfig)
    daily: DailyConfig = field(default_factory=DailyConfig)
    hourly: HourlyConfig = field(default_factory=HourlyConfig)
    open_interest: OpenInterestConfig = field(
        default_factory=OpenInterestConfig
    )
    macro: MacroConfig = field(default_factory=MacroConfig)


def load_config(path: str | Path | None = None) -> AppConfig:
    data: dict[str, Any] = {}
    if path is not None:
        with Path(path).open("r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle) or {}
            if not isinstance(raw, dict):
                raise ValueError("config root must be a mapping")
            data = raw

    database = Path(
        os.environ.get("CCQUANT_DB")
        or os.path.expandvars(str(data.get("database", "data/ccquant.duckdb")))
    )
    universe_data = data.get("universe", {}) or {}
    daily_data = data.get("daily", {}) or {}
    hourly_data = data.get("hourly", {}) or {}
    oi_data = data.get("open_interest", {}) or {}
    macro_data = data.get("macro", {}) or {}
    return AppConfig(
        database=database,
        universe=UniverseConfig(
            size=int(universe_data.get("size", 100)),
            include_symbols=[
                str(symbol).upper()
                for symbol in universe_data.get("include_symbols", [])
            ],
            source_preference=str(universe_data.get("source_preference", "binance")),
            request_delay_seconds=float(
                universe_data.get("request_delay_seconds", 0.25)
            ),
        ),
        daily=DailyConfig(tail_days=int(daily_data.get("tail_days", 7))),
        hourly=HourlyConfig(
            enabled=bool(hourly_data.get("enabled", True)),
            top=int(hourly_data.get("top", 10)),
            history_days=int(hourly_data.get("history_days", 365)),
            tail_hours=int(hourly_data.get("tail_hours", 168)),
        ),
        open_interest=OpenInterestConfig(
            enabled=bool(oi_data.get("enabled", True)),
            history_days=int(oi_data.get("history_days", 365)),
            tail_hours=int(oi_data.get("tail_hours", 168)),
            request_delay_seconds=float(
                oi_data.get("request_delay_seconds", 0.25)
            ),
            binance=bool(oi_data.get("binance", True)),
            bybit=bool(oi_data.get("bybit", True)),
            okx=bool(oi_data.get("okx", True)),
        ),
        macro=MacroConfig(
            enabled=bool(macro_data.get("enabled", True)),
            series_ids=[
                str(sid) for sid in macro_data.get("series_ids", FRED_SERIES)
            ],
            request_delay_seconds=float(
                macro_data.get("request_delay_seconds", 1.0)
            ),
        ),
    )
