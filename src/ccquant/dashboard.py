"""Lightweight single-page Market Tracker dashboard (HTML + Plotly).

Condenses the notebook surface into one viewport: brand, headline, near-live
BTC tape, key metrics, daily market chart, regime strip, outlook, and a
BTC monthly-gains heatmap. No HTTP server — write a self-contained HTML
file via ``ccquant dashboard``. The live tape seeds from Binance/Coinbase
at render time; the browser can poll Binance every 15s to keep the
headline last price fresh.
"""

from __future__ import annotations

import calendar
import html
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

import duckdb
import numpy as np
import polars as pl

from ccquant.forecasting import load_daily_panel, load_signals_panel
from ccquant.live_price import (
    DEFAULT_INTERVAL_FOR_RANGE,
    INTERVALS_FOR_RANGE,
    DailyFill,
    LiveInterval,
    LiveRange,
    LiveTape,
    fetch_live_tape,
    fetch_recent_daily_btc,
)

MOM_LOOKBACK = 12
LIQ_LOOKBACK = 52
VOL_SMA_DAYS = 20
REL_VOL_HIGH = 1.2
REL_VOL_LOW = 0.8
MTD_VOL_HIGH = 1.2
MTD_VOL_LOW = 0.8
# Length presets for the long-term chart (full history remains embedded).
CHART_LENGTH_DAYS: dict[str, int | None] = {
    "3m": 90,
    "1y": 365,
    "2y": 730,
    "5y": 1825,
    "all": None,
}
CHART_PERIOD_KEYS: tuple[str, ...] = ("mtd", "qtd", "ytd")
CHART_DEFAULT_LENGTH = "2y"
STALE_WARN_DAYS = 3
DASHBOARD_TZ = ZoneInfo("America/Chicago")
# Trading-desk presets exposed in the live tape toolbar (default: Chicago).
LIVE_TZ_PRESETS: tuple[tuple[str, str, str], ...] = (
    ("ny", "America/New_York", "NY"),
    ("utc", "UTC", "UTC"),
    ("ct", "America/Chicago", "CT"),
)
DEFAULT_LIVE_TZ = "ct"
MONTH_LABELS: tuple[str, ...] = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)
# Cap |return| for the diverging colorscale so a few outliers don't flatten
# the rest of the calendar (values still shown in cell text / hover).
HEATMAP_RET_CAP_PCT = 40.0
# Fat-finger wicks (e.g. Coinbase BTC 2017-04-15 low=0.06 vs ~$1,178 close).
# Floor/ceil are vs the candle body so real crashes (2020-03-12) are kept.
WICK_FLOOR = 0.5
WICK_CEIL = 2.0
# Bitcoin subsidy cuts (blocks 210k / 420k / 630k / 840k). H5 is 210k blocks
# after H4 at a 10-minute average and is labeled as an estimate.
BTC_GENESIS = date(2009, 1, 3)
BTC_HALVINGS: tuple[tuple[date, str], ...] = (
    (date(2012, 11, 28), "50 → 25 BTC"),
    (date(2016, 7, 9), "25 → 12.5 BTC"),
    (date(2020, 5, 11), "12.5 → 6.25 BTC"),
    (date(2024, 4, 20), "6.25 → 3.125 BTC"),
)
NEXT_HALVING_EST = date(2028, 4, 17)
# Stock Trader's Almanac presidential cycle: Y4 = US election year (year % 4
# == 0), then Y1 post-election, Y2 midterm, Y3 pre-election.
PRES_CYCLE_LABELS: tuple[str, ...] = (
    "Y1 post-election",
    "Y2 midterm",
    "Y3 pre-election",
    "Y4 election",
)
# Inauguration-to-inauguration (20 Jan). Covers BTC's traded history.
US_ADMINS: tuple[tuple[date, date, str], ...] = (
    (date(2009, 1, 20), date(2013, 1, 20), "Obama I"),
    (date(2013, 1, 20), date(2017, 1, 20), "Obama II"),
    (date(2017, 1, 20), date(2021, 1, 20), "Trump I"),
    (date(2021, 1, 20), date(2025, 1, 20), "Biden"),
    (date(2025, 1, 20), date(2029, 1, 20), "Trump II"),
)
# First Tuesday after the first Monday in November.
US_ELECTION_DATES: tuple[date, ...] = (
    date(2008, 11, 4),
    date(2012, 11, 6),
    date(2016, 11, 8),
    date(2020, 11, 3),
    date(2024, 11, 5),
    date(2028, 11, 7),
)


def _to_tz(dt: datetime, tz: ZoneInfo) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=tz)
    return dt.astimezone(tz)


def _session_today() -> date:
    """Calendar date in the dashboard timezone (Chicago)."""
    return datetime.now(DASHBOARD_TZ).date()


def _chart_period_start(end: date, key: str) -> date:
    """Calendar start for MTD / QTD / YTD windows ending at ``end``."""
    if key == "mtd":
        return date(end.year, end.month, 1)
    if key == "qtd":
        month = ((end.month - 1) // 3) * 3 + 1
        return date(end.year, month, 1)
    if key == "ytd":
        return date(end.year, 1, 1)
    raise ValueError(f"unknown chart period {key!r}")


def _merge_live_bar(
    dates: list[date],
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float],
    volumes: list[float],
    live: LiveTape | None,
    *,
    through: date,
) -> None:
    """Update or append today's in-progress bar from the live tape."""
    if live is None or not dates:
        return
    day = _to_tz(live.as_of, DASHBOARD_TZ).date()
    if day < dates[-1] or day > through:
        return
    # Don't draw a diagonal across a multi-day hole (stale daily panel).
    if day > dates[-1] + timedelta(days=1):
        return
    last_px = live.last
    if not (last_px > 0):
        return
    if day == dates[-1]:
        highs[-1] = max(highs[-1], last_px)
        lows[-1] = min(lows[-1], last_px)
        closes[-1] = last_px
        return
    prev = closes[-1]
    dates.append(day)
    opens.append(prev)
    highs.append(max(prev, last_px))
    lows.append(min(prev, last_px))
    closes.append(last_px)
    volumes.append(0.0)


def _clamp_ohlc_wicks(
    opens: tuple[float, ...] | list[float],
    highs: tuple[float, ...] | list[float],
    lows: tuple[float, ...] | list[float],
    closes: tuple[float, ...] | list[float],
    *,
    wick_floor: float = WICK_FLOOR,
    wick_ceil: float = WICK_CEIL,
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """Replace impossible wicks; leave open/close (returns) unchanged."""
    new_highs: list[float] = []
    new_lows: list[float] = []
    for open_px, high, low, close in zip(opens, highs, lows, closes, strict=True):
        body_lo = min(open_px, close)
        body_hi = max(open_px, close)
        if low <= 0.0 or (body_lo > 0.0 and low < wick_floor * body_lo):
            low = body_lo
        if body_hi > 0.0 and high > wick_ceil * body_hi:
            high = body_hi
        high = max(high, body_hi, low)
        low = min(low, body_lo, high)
        new_highs.append(high)
        new_lows.append(low)
    return tuple(new_highs), tuple(new_lows)


def _splice_daily_tail(
    dates: list[date],
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float],
    volumes: list[float],
    bars: tuple[DailyFill, ...] | None,
    *,
    through: date,
) -> None:
    """Append missing daily bars after the last stored date (oldest → newest)."""
    if not bars or not dates:
        return
    last = dates[-1]
    for day, open_px, high, low, close, volume in bars:
        if day <= last or day > through:
            continue
        dates.append(day)
        opens.append(open_px)
        highs.append(high)
        lows.append(low)
        closes.append(close)
        volumes.append(volume)
        last = day


def _fmt_tz(dt: datetime, tz: ZoneInfo = DASHBOARD_TZ) -> str:
    """Format an aware datetime in ``tz`` (e.g. 2026-07-19 07:00 CDT)."""
    return _to_tz(dt, tz).strftime("%Y-%m-%d %H:%M %Z")


def _ms(dt: datetime) -> int:
    aware = dt if dt.tzinfo is not None else dt.replace(tzinfo=ZoneInfo("UTC"))
    return int(aware.timestamp() * 1000)

Headline = Literal["Risk-on", "Mixed", "Risk-off"]
StackLabel = Literal["Constructive", "Neutral / mixed", "Defensive"]


@dataclass(frozen=True)
class MarketSnapshot:
    """Condensed market state for a one-page dashboard."""

    as_of: date
    btc_close: float
    ret_1d: float | None
    ret_7d: float | None
    ret_30d: float | None
    ret_ytd: float | None
    pct_up_7d: float | None
    n_universe: int
    pct_above_50: float | None
    headline: Headline
    stack_label: StackLabel
    stack_score: int
    liq_signal: int  # -1 / 0 / +1
    oc_signal: int
    breadth_signal: int
    liq_label: str
    oc_label: str
    breadth_label: str
    demand_signal: int
    demand_label: str
    vol_signal: int
    vol_label: str
    rel_vol_20: float | None
    mtd_vol_ratio: float | None
    etf_flow_7d_m: float | None
    mstr_rel_20d: float | None
    outlook: str
    supporting: str
    freshness_note: str
    btc_dates: tuple[date, ...]
    btc_opens: tuple[float, ...]
    btc_highs: tuple[float, ...]
    btc_lows: tuple[float, ...]
    btc_closes: tuple[float, ...]
    btc_volumes: tuple[float, ...]


def _table_exists(conn: duckdb.DuckDBPyConnection, schema: str, table: str) -> bool:
    row = conn.execute(
        "select count(*) from information_schema.tables"
        " where table_schema = ? and table_name = ?",
        [schema, table],
    ).fetchone()
    return bool(row and row[0] > 0)


def _load_macro(database: Path) -> pl.DataFrame:
    with duckdb.connect(str(database), read_only=True) as conn:
        if not _table_exists(conn, "main_signals", "fct_macro_series"):
            return pl.DataFrame()
        df = pl.from_arrow(
            conn.execute(
                """
                select date, m2sl, walcl, dgs10, dgs2, t10yie, fedfunds,
                       dtwexbgs, vixcls
                from main_signals.fct_macro_series
                order by date
                """
            ).to_arrow_table()
        )
    return df if isinstance(df, pl.DataFrame) else df.to_frame()


def _load_onchain(database: Path) -> pl.DataFrame:
    with duckdb.connect(str(database), read_only=True) as conn:
        if not _table_exists(conn, "main_signals", "fct_onchain_signals"):
            return pl.DataFrame()
        df = pl.from_arrow(
            conn.execute(
                """
                select date, mvrv, nupl, active_addresses, hashrate, fees_usd
                from main_signals.fct_onchain_signals
                order by date
                """
            ).to_arrow_table()
        )
    return df if isinstance(df, pl.DataFrame) else df.to_frame()


def _load_etf_total_flows(database: Path) -> pl.DataFrame:
    with duckdb.connect(str(database), read_only=True) as conn:
        if not _table_exists(conn, "main", "etf_flows_daily"):
            return pl.DataFrame()
        df = pl.from_arrow(
            conn.execute(
                """
                select date, net_flow_usd_m
                from main.etf_flows_daily
                where ticker = 'TOTAL' and source = 'farside'
                order by date
                """
            ).to_arrow_table()
        )
    return df if isinstance(df, pl.DataFrame) else df.to_frame()


def _load_equity(database: Path, symbol: str) -> pl.DataFrame:
    with duckdb.connect(str(database), read_only=True) as conn:
        if not _table_exists(conn, "main", "equity_daily"):
            return pl.DataFrame()
        df = pl.from_arrow(
            conn.execute(
                """
                select date, close
                from main.equity_daily
                where symbol = ?
                order by date
                """,
                [symbol.upper()],
            ).to_arrow_table()
        )
    return df if isinstance(df, pl.DataFrame) else df.to_frame()


def _load_raw_ohlcv_daily(database: Path) -> pl.DataFrame:
    """Raw ``ohlcv_daily`` (may be ahead of a lagging dbt mart)."""
    with duckdb.connect(str(database), read_only=True) as conn:
        if not _table_exists(conn, "main", "ohlcv_daily"):
            return pl.DataFrame()
        df = pl.from_arrow(
            conn.execute(
                """
                select symbol, date, open, high, low, close, volume, source
                from main.ohlcv_daily
                order by symbol, date, source
                """
            ).to_arrow_table()
        )
    return df if isinstance(df, pl.DataFrame) else df.to_frame()


def _extend_daily_panel_with_raw(
    daily: pl.DataFrame, raw: pl.DataFrame
) -> pl.DataFrame:
    """Append raw OHLCV rows newer than the mart panel (per symbol)."""
    if daily.is_empty() or raw.is_empty():
        return daily
    need = ["symbol", "date", "open", "high", "low", "close", "volume", "source"]
    if any(c not in daily.columns or c not in raw.columns for c in need):
        return daily
    daily = daily.with_columns(pl.col("date").cast(pl.Date))
    raw = raw.with_columns(pl.col("date").cast(pl.Date))
    mart_max = daily.group_by("symbol").agg(pl.col("date").max().alias("_max_d"))
    extra = (
        raw.join(mart_max, on="symbol", how="inner")
        .filter(pl.col("date") > pl.col("_max_d"))
        .drop("_max_d")
        .select(need)
        .unique(subset=["symbol", "date"], keep="last")
    )
    if extra.is_empty():
        return daily
    return pl.concat([daily.select(need), extra], how="vertical").sort(
        ["symbol", "date"]
    )


def _etf_mstr_demand(
    *,
    etf_flows: pl.DataFrame,
    mstr: pl.DataFrame,
    btc: pl.DataFrame,
) -> tuple[int, str, float | None, float | None]:
    from ccquant.etf_flows import mstr_etf_health

    etf_7d: float | None = None
    if not etf_flows.is_empty() and "net_flow_usd_m" in etf_flows.columns:
        tail = etf_flows.sort("date").tail(7)
        if tail.height:
            etf_7d = _as_float(tail["net_flow_usd_m"].sum())

    mstr_rel: float | None = None
    if not mstr.is_empty() and not btc.is_empty():
        m = mstr.with_columns(pl.col("date").cast(pl.Date)).sort("date")
        b = btc.select(["date", "close"]).with_columns(
            pl.col("date").cast(pl.Date)
        )
        joined = m.join(b, on="date", how="inner", suffix="_btc").sort("date")
        if joined.height > 20:
            m_ret = _pct_ret(joined["close"], 20)
            b_ret = _pct_ret(joined["close_btc"], 20)
            if m_ret is not None and b_ret is not None:
                mstr_rel = m_ret - b_ret

    sig, label = mstr_etf_health(etf_flow_7d_m=etf_7d, mstr_rel_20d=mstr_rel)
    if etf_7d is None and mstr_rel is None:
        return 0, "MISSING", None, None
    detail = label
    if etf_7d is not None:
        detail = f"{label} · ETF 7d {etf_7d:+.0f}m"
    return sig, detail, etf_7d, mstr_rel


def _as_float(value: object) -> float:
    if value is None:
        raise TypeError("expected numeric value, got None")
    out = pl.Series([value]).cast(pl.Float64).item()
    if not isinstance(out, (int, float)):
        raise TypeError(f"expected numeric value, got {type(out)!r}")
    return float(out)


def _pct_ret(closes: pl.Series, lag: int) -> float | None:
    if closes.len() <= lag:
        return None
    a, b = _as_float(closes[-(lag + 1)]), _as_float(closes[-1])
    if a == 0 or np.isnan(a) or np.isnan(b):
        return None
    return b / a - 1.0


def _z_expr(col: str) -> pl.Expr:
    return (pl.col(col) - pl.col(col).mean()) / (pl.col(col).std() + 1e-12)


def _liq_signal(macro_raw: pl.DataFrame) -> tuple[int, str]:
    if macro_raw.is_empty():
        return 0, "MISSING"
    need = {"m2sl", "walcl", "dgs10", "t10yie"}
    if not need.issubset(set(macro_raw.columns)):
        return 0, "MISSING"
    m = (
        macro_raw.with_columns(pl.col("date").cast(pl.Date))
        .sort("date")
        .with_columns(pl.col("date").dt.truncate("1w").alias("week"))
        .group_by("week")
        .agg([pl.col(c).drop_nulls().last().alias(c) for c in need])
        .sort("week")
        .with_columns((pl.col("dgs10") - pl.col("t10yie")).alias("real_10y"))
        .with_columns(
            (pl.col("m2sl").log() - pl.col("m2sl").log().shift(LIQ_LOOKBACK)).alias(
                "m2_grow_yoy"
            ),
            (pl.col("walcl").log() - pl.col("walcl").log().shift(LIQ_LOOKBACK)).alias(
                "fedbs_grow_yoy"
            ),
            (pl.col("real_10y") - pl.col("real_10y").shift(LIQ_LOOKBACK)).alias(
                "real_rate_delta"
            ),
        )
        .drop_nulls(subset=["m2_grow_yoy", "fedbs_grow_yoy", "real_rate_delta"])
    )
    if m.height <= MOM_LOOKBACK + 5:
        return 0, "MISSING"
    m = m.with_columns(
        (
            _z_expr("m2_grow_yoy")
            + _z_expr("fedbs_grow_yoy")
            - _z_expr("real_rate_delta")
        ).alias("liq_raw")
    )
    mu = _as_float(m["liq_raw"].mean())
    sd_raw = m["liq_raw"].std()
    sd = _as_float(sd_raw) if sd_raw is not None else 0.0
    m = m.with_columns(
        ((pl.col("liq_raw") - mu) / (sd if sd > 1e-12 else 1.0)).alias("liq_index"),
    ).with_columns(
        (pl.col("liq_index") - pl.col("liq_index").shift(MOM_LOOKBACK)).alias(
            "liq_mom"
        ),
    )
    mom = m["liq_mom"][-1]
    if mom is None:
        return 0, "MISSING"
    mom_f = _as_float(mom)
    if np.isnan(mom_f):
        return 0, "MISSING"
    expanding = mom_f > 0
    return (1 if expanding else -1), ("expanding" if expanding else "contracting")


def _oc_signal(onchain: pl.DataFrame) -> tuple[int, str]:
    """On-chain regime from forward-filled fundamentals (sparse pivot-safe).

    Prefers hashrate / active_addresses / fees; uses MVRV/NUPL when they have
    real variance (not short BID samples or constant fixtures).
    """
    if onchain.is_empty():
        return 0, "MISSING"
    oc = onchain.with_columns(pl.col("date").cast(pl.Date)).sort("date")
    # Fundamentals first — valuation columns are often sparse or short samples.
    preferred = [
        c
        for c in (
            "hashrate",
            "active_addresses",
            "fees_usd",
            "mvrv",
            "nupl",
        )
        if c in oc.columns and oc[c].drop_nulls().len() >= 30
    ]
    if not preferred:
        return 0, "MISSING"

    oc = oc.with_columns([pl.col(c).forward_fill() for c in preferred])
    # Require overlapping coverage after fill
    oc = oc.drop_nulls(subset=preferred)
    if oc.height <= MOM_LOOKBACK + 5:
        return 0, "MISSING"

    varying: list[str] = []
    for c in preferred:
        std_v = oc[c].std()
        if std_v is not None and _as_float(std_v) > 1e-12:
            varying.append(c)
    # Drop near-constant valuation stubs (e.g. short BID sample / fixtures)
    varying = [
        c
        for c in varying
        if c not in {"mvrv", "nupl"} or oc[c].drop_nulls().len() >= 90
    ]
    if not varying:
        return 0, "MISSING"

    # Weekly spine for momentum horizon comparable to Macro notebook
    oc = (
        oc.with_columns(pl.col("date").dt.truncate("1w").alias("week"))
        .group_by("week")
        .agg([pl.col(c).last().alias(c) for c in varying])
        .sort("week")
        .rename({"week": "date"})
    )
    if oc.height <= MOM_LOOKBACK + 2:
        return 0, "MISSING"

    expr = _z_expr(varying[0])
    for c in varying[1:]:
        expr = expr + _z_expr(c)
    oc = oc.with_columns(expr.alias("cycle_raw"))
    mu = _as_float(oc["cycle_raw"].mean())
    sd_raw = oc["cycle_raw"].std()
    sd = _as_float(sd_raw) if sd_raw is not None else 0.0
    oc = oc.with_columns(
        ((pl.col("cycle_raw") - mu) / (sd if sd > 1e-12 else 1.0)).alias(
            "cycle_index"
        ),
    ).with_columns(
        (pl.col("cycle_index") - pl.col("cycle_index").shift(MOM_LOOKBACK)).alias(
            "cycle_mom"
        ),
    )
    mom = oc["cycle_mom"][-1]
    if mom is None:
        return 0, "MISSING"
    mom_f = _as_float(mom)
    if np.isnan(mom_f):
        return 0, "MISSING"
    bullish = mom_f > 0
    label = "bullish mom" if bullish else "bearish mom"
    return (1 if bullish else -1), f"{label} ({'+'.join(varying[:3])})"


def _btc_volume_signal(
    dates: tuple[date, ...] | list[date],
    volumes: tuple[float, ...] | list[float],
    *,
    as_of: date,
    ret_7d: float | None,
) -> tuple[int, str, float | None, float | None]:
    """BTC participation: 20d relative volume + MTD pace vs prior months.

    Combines with 7d return into a confirm/deny chip:
    sponsored / fragile / distribution / quiet / heavy / normal.
    """
    n = len(volumes)
    if n < VOL_SMA_DAYS + 1 or len(dates) != n:
        return 0, "MISSING", None, None

    window = list(volumes[-(VOL_SMA_DAYS + 1) :])
    last_vol = window[-1]
    base = window[:-1]
    if last_vol <= 0 or not base or sum(base) <= 0:
        return 0, "MISSING", None, None
    avg20 = sum(base) / len(base)
    if avg20 <= 0:
        return 0, "MISSING", None, None
    rel_vol = last_vol / avg20

    # MTD volume pace vs mean of prior 3 full calendar months.
    month_totals: dict[tuple[int, int], float] = {}
    for d, v in zip(dates, volumes, strict=True):
        key = (d.year, d.month)
        month_totals[key] = month_totals.get(key, 0.0) + v
    mtd_ratio: float | None = None
    cur_key = (as_of.year, as_of.month)
    mtd_vol = month_totals.get(cur_key, 0.0)
    prior_keys = sorted(k for k in month_totals if k < cur_key)[-3:]
    prior_vols = [month_totals[k] for k in prior_keys if month_totals[k] > 0]
    if prior_vols and mtd_vol > 0:
        days_in_month = calendar.monthrange(as_of.year, as_of.month)[1]
        pace = mtd_vol * (days_in_month / max(as_of.day, 1))
        mtd_ratio = pace / (sum(prior_vols) / len(prior_vols))

    elevated = rel_vol >= REL_VOL_HIGH or (
        mtd_ratio is not None and mtd_ratio >= MTD_VOL_HIGH
    )
    quiet = rel_vol <= REL_VOL_LOW and (
        mtd_ratio is None or mtd_ratio <= MTD_VOL_LOW
    )
    up = ret_7d is not None and ret_7d > 0.01
    down = ret_7d is not None and ret_7d < -0.01

    if elevated and up:
        sig, name = 1, "sponsored"
    elif elevated and down:
        sig, name = -1, "distribution"
    elif quiet and up:
        sig, name = -1, "fragile"
    elif quiet and down:
        sig, name = 0, "washed out"
    elif elevated:
        sig, name = 0, "heavy"
    elif quiet:
        sig, name = 0, "quiet"
    else:
        sig, name = 0, "normal"

    parts = [name, f"{rel_vol:.1f}×"]
    if mtd_ratio is not None:
        parts.append(f"MTD {mtd_ratio:.1f}×")
    return sig, " · ".join(parts), rel_vol, mtd_ratio


def _breadth_metrics(
    daily: pl.DataFrame, as_of: date
) -> tuple[float | None, float | None, int, int]:
    """Return pct_up_7d, pct_above_50, n_universe, breadth_signal."""
    latest = daily.select(pl.col("date").max()).item()
    sym_last = (
        daily.sort("date")
        .group_by("symbol")
        .agg(pl.col("close").last().alias("last_close"))
    )
    target = latest - timedelta(days=7)
    lagged = (
        daily.filter(pl.col("date") <= target)
        .sort("date")
        .group_by("symbol")
        .agg(pl.col("close").last().alias("lag_close"))
    )
    r7 = (
        sym_last.join(lagged, on="symbol", how="inner")
        .with_columns((pl.col("last_close") / pl.col("lag_close") - 1.0).alias("ret"))
        .filter(pl.col("lag_close") > 0)
    )
    pct_up = _as_float((r7["ret"] > 0).mean()) if r7.height else None
    n_uni = int(r7.height)

    panel = (
        daily.sort(["symbol", "date"])
        .unique(subset=["symbol", "date"], keep="last")
        .with_columns(pl.col("close").rolling_mean(50).over("symbol").alias("ma50"))
        .filter(pl.col("ma50").is_not_null())
    )
    if panel.is_empty():
        pct50 = None
    else:
        last_d = panel.select(pl.col("date").max()).item()
        day = panel.filter(pl.col("date") == last_d)
        pct50 = (
            _as_float((day["close"] > day["ma50"]).mean()) if day.height else None
        )

    if pct50 is not None:
        br = 1 if pct50 > 0.55 else (-1 if pct50 < 0.45 else 0)
    elif pct_up is not None:
        br = 1 if pct_up > 0.55 else (-1 if pct_up < 0.45 else 0)
    else:
        br = 0
    _ = as_of  # reserved for future as-of filtering
    return pct_up, pct50, n_uni, br


def _headline(ret_7d: float | None, pct_up_7d: float | None) -> Headline:
    bits: list[int] = []
    if ret_7d is not None:
        bits.append(1 if ret_7d > 0.02 else (-1 if ret_7d < -0.02 else 0))
    if pct_up_7d is not None:
        bits.append(1 if pct_up_7d > 0.55 else (-1 if pct_up_7d < 0.45 else 0))
    score = sum(bits) if bits else 0
    if score >= 2:
        return "Risk-on"
    if score <= -2:
        return "Risk-off"
    return "Mixed"


def _stack(liq: int, oc: int, br: int) -> tuple[int, StackLabel]:
    score = liq + oc + br
    if score >= 2:
        return score, "Constructive"
    if score <= -2:
        return score, "Defensive"
    return score, "Neutral / mixed"


def _outlook(stack_label: StackLabel, drivers: list[str]) -> str:
    joined = "; ".join(drivers) if drivers else "limited regime inputs"
    if stack_label == "Constructive":
        body = (
            "Constructive bias — liquidity, on-chain, and/or breadth line up "
            "positively. Compare conditional history in Market_Tracker.ipynb "
            "before acting; not a price target."
        )
    elif stack_label == "Defensive":
        body = (
            "Defensive bias — multiple regime legs are negative. Confirm with "
            "Macro / OnChain notebooks; not a trade signal."
        )
    else:
        body = (
            "Neutral / mixed — regime legs disagree or data are incomplete. "
            "Prefer confirmation over a forced directional view."
        )
    return f"{body} Drivers: {joined}."


def build_snapshot_from_panels(
    daily: pl.DataFrame,
    *,
    macro: pl.DataFrame | None = None,
    onchain: pl.DataFrame | None = None,
    etf_flows: pl.DataFrame | None = None,
    mstr: pl.DataFrame | None = None,
    freshness_note: str = "",
) -> MarketSnapshot:
    """Build a dashboard snapshot from in-memory panels (tests / notebooks)."""
    if daily.is_empty():
        raise ValueError("daily panel is empty")

    daily = daily.with_columns(pl.col("date").cast(pl.Date)).sort(["symbol", "date"])
    btc = (
        daily.filter(pl.col("symbol") == "BTC")
        .unique(subset=["date"], keep="last")
        .sort("date")
    )
    if btc.is_empty():
        raise ValueError("daily panel has no BTC rows")

    as_of_raw = btc["date"][-1]
    if not isinstance(as_of_raw, date):
        raise TypeError(f"expected date as_of, got {type(as_of_raw)!r}")
    as_of = as_of_raw
    closes = btc["close"]
    btc_close = _as_float(closes[-1])
    ret_1d = _pct_ret(closes, 1)
    ret_7d = _pct_ret(closes, 7)
    ret_30d = _pct_ret(closes, 30)
    ytd = btc.filter(pl.col("date") >= date(as_of.year, 1, 1))
    ret_ytd = None
    if ytd.height >= 2:
        ret_ytd = _as_float(ytd["close"][-1]) / _as_float(ytd["close"][0]) - 1.0

    pct_up, pct50, n_uni, br_sig = _breadth_metrics(daily, as_of)
    liq_sig, liq_label = _liq_signal(macro if macro is not None else pl.DataFrame())
    oc_sig, oc_label = _oc_signal(onchain if onchain is not None else pl.DataFrame())
    br_label = (
        "broad" if br_sig == 1 else ("narrow" if br_sig == -1 else "balanced")
    )

    headline = _headline(ret_7d, pct_up)
    stack_score, stack_label = _stack(liq_sig, oc_sig, br_sig)
    demand_sig, demand_label, etf_7d, mstr_rel = _etf_mstr_demand(
        etf_flows=etf_flows if etf_flows is not None else pl.DataFrame(),
        mstr=mstr if mstr is not None else pl.DataFrame(),
        btc=btc,
    )

    chart_cols = ["date", "open", "high", "low", "close"]
    if "volume" in btc.columns:
        chart_cols.append("volume")
    chart = btc.select(chart_cols)
    if "volume" not in chart.columns:
        chart = chart.with_columns(pl.lit(0.0).alias("volume"))
    btc_dates = tuple(d for d in chart["date"].to_list() if isinstance(d, date))
    btc_opens = tuple(_as_float(x) for x in chart["open"].to_list())
    btc_highs = tuple(_as_float(x) for x in chart["high"].to_list())
    btc_lows = tuple(_as_float(x) for x in chart["low"].to_list())
    btc_closes = tuple(_as_float(x) for x in chart["close"].to_list())
    btc_volumes = tuple(_as_float(x) for x in chart["volume"].to_list())
    btc_highs, btc_lows = _clamp_ohlc_wicks(
        btc_opens, btc_highs, btc_lows, btc_closes
    )

    vol_sig, vol_label, rel_vol, mtd_vol = _btc_volume_signal(
        btc_dates,
        btc_volumes,
        as_of=as_of,
        ret_7d=ret_7d,
    )

    drivers: list[str] = []
    if liq_sig != 0:
        drivers.append(f"{liq_label} liquidity")
    if oc_sig != 0:
        drivers.append(f"on-chain {oc_label}")
    drivers.append(f"{br_label} breadth")
    if demand_sig != 0 or (etf_7d is not None):
        drivers.append(f"demand {demand_label}")
    if vol_sig != 0 or (rel_vol is not None and vol_label != "MISSING"):
        drivers.append(f"volume {vol_label}")
    if ret_7d is not None:
        drivers.append(f"BTC 7d {ret_7d * 100:+.1f}%")

    if br_sig == 1:
        supporting = "Tape is broad across the research universe."
    elif br_sig == -1:
        supporting = "Tape is narrow — leadership is concentrated."
    else:
        supporting = "Breadth is balanced; wait for confirmation."
    if vol_sig == 1:
        supporting += " Volume sponsors the move."
    elif vol_sig == -1 and "fragile" in vol_label:
        supporting += " Rally lacks volume confirmation."
    elif vol_sig == -1:
        supporting += " Selling is volume-backed."

    note = freshness_note or f"BTC daily through {as_of}"

    return MarketSnapshot(
        as_of=as_of,
        btc_close=btc_close,
        ret_1d=ret_1d,
        ret_7d=ret_7d,
        ret_30d=ret_30d,
        ret_ytd=ret_ytd,
        pct_up_7d=pct_up,
        n_universe=n_uni,
        pct_above_50=pct50,
        headline=headline,
        stack_label=stack_label,
        stack_score=stack_score,
        liq_signal=liq_sig,
        oc_signal=oc_sig,
        breadth_signal=br_sig,
        liq_label=liq_label,
        oc_label=oc_label,
        breadth_label=br_label,
        demand_signal=demand_sig,
        demand_label=demand_label,
        vol_signal=vol_sig,
        vol_label=vol_label,
        rel_vol_20=rel_vol,
        mtd_vol_ratio=mtd_vol,
        etf_flow_7d_m=etf_7d,
        mstr_rel_20d=mstr_rel,
        outlook=_outlook(stack_label, drivers),
        supporting=supporting,
        freshness_note=note,
        btc_dates=btc_dates,
        btc_opens=btc_opens,
        btc_highs=btc_highs,
        btc_lows=btc_lows,
        btc_closes=btc_closes,
        btc_volumes=btc_volumes,
    )


def build_market_snapshot(database: str | Path) -> MarketSnapshot:
    """Load DuckDB marts and return a condensed MarketSnapshot."""
    path = Path(database)
    if not path.is_file():
        raise FileNotFoundError(
            f"DuckDB not found at {path}. Run: uv run ccquant sync all"
        )
    try:
        signals = load_signals_panel(path)
    except Exception as exc:
        raise RuntimeError(
            "Failed to load main_marts.mart_signals_daily. "
            "Run: uv run dbt build --project-dir dbt --profiles-dir dbt"
        ) from exc
    if signals.is_empty():
        raise RuntimeError("mart_signals_daily is empty — sync + dbt build first")

    daily = load_daily_panel(path)
    raw = _load_raw_ohlcv_daily(path)
    daily = _extend_daily_panel_with_raw(daily, raw)
    macro = _load_macro(path)
    onchain = _load_onchain(path)
    etf_flows = _load_etf_total_flows(path)
    mstr = _load_equity(path, "MSTR")

    btc_max = (
        daily.filter(pl.col("symbol") == "BTC")
        .select(pl.col("date").max())
        .item()
    )
    freshness = f"BTC daily through {btc_max}"
    if isinstance(btc_max, date):
        age = (date.today() - btc_max).days
        if age > STALE_WARN_DAYS:
            freshness += f" · STALE ({age}d) — run sync all"

    _ = signals  # ensures mart exists; price/breadth use daily panel
    return build_snapshot_from_panels(
        daily,
        macro=macro,
        onchain=onchain,
        etf_flows=etf_flows,
        mstr=mstr,
        freshness_note=freshness,
    )


def _fmt_pct(x: float | None) -> str:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "—"
    return f"{100 * x:+.1f}%"


def _fmt_share(x: float | None) -> str:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "—"
    return f"{100 * x:.0f}%"


def _monthly_ohlcv(
    dates: tuple[date, ...],
    opens: tuple[float, ...],
    highs: tuple[float, ...],
    lows: tuple[float, ...],
    closes: tuple[float, ...],
    volumes: tuple[float, ...],
) -> tuple[
    tuple[date, ...],
    tuple[float, ...],
    tuple[float, ...],
    tuple[float, ...],
    tuple[float, ...],
    tuple[float, ...],
]:
    """Aggregate daily OHLC+volume into calendar-month bars."""
    if not dates:
        return (), (), (), (), (), ()
    months: list[date] = []
    m_open: list[float] = []
    m_high: list[float] = []
    m_low: list[float] = []
    m_close: list[float] = []
    m_vol: list[float] = []
    cur_key: tuple[int, int] | None = None
    for d, o, h, lo, c, v in zip(
        dates, opens, highs, lows, closes, volumes, strict=True
    ):
        key = (d.year, d.month)
        if key != cur_key:
            months.append(date(d.year, d.month, 1))
            m_open.append(o)
            m_high.append(h)
            m_low.append(lo)
            m_close.append(c)
            m_vol.append(v)
            cur_key = key
        else:
            m_high[-1] = max(m_high[-1], h)
            m_low[-1] = min(m_low[-1], lo)
            m_close[-1] = c
            m_vol[-1] += v
    return (
        tuple(months),
        tuple(m_open),
        tuple(m_high),
        tuple(m_low),
        tuple(m_close),
        tuple(m_vol),
    )


def _btc_monthly_gains_seed(snapshot: MarketSnapshot) -> dict[str, object]:
    """Year × month close-to-close return matrix (%) for the heatmap."""
    m_dates, _, _, _, m_closes, _ = _monthly_ohlcv(
        snapshot.btc_dates,
        snapshot.btc_opens,
        snapshot.btc_highs,
        snapshot.btc_lows,
        snapshot.btc_closes,
        snapshot.btc_volumes,
    )
    ret_by_ym: dict[tuple[int, int], float] = {}
    for i in range(1, len(m_dates)):
        prev = m_closes[i - 1]
        if prev == 0.0:
            continue
        d = m_dates[i]
        ret_by_ym[(d.year, d.month)] = (m_closes[i] / prev - 1.0) * 100.0

    as_of = snapshot.as_of
    last_dom = calendar.monthrange(as_of.year, as_of.month)[1]
    month_open = as_of.day < last_dom
    # Year-axis suffix for the open month, e.g. "Aug, 24" → "2026 Aug, 24".
    open_through = (
        f"{MONTH_LABELS[as_of.month - 1]}, {as_of.day}" if month_open else None
    )
    years = sorted({d.year for d in m_dates}, reverse=True)
    z: list[list[float | None]] = []
    text: list[list[str]] = []
    for year in years:
        row: list[float | None] = []
        trow: list[str] = []
        for month in range(1, 13):
            val = ret_by_ym.get((year, month))
            row.append(val)
            trow.append("" if val is None else f"{val:+.1f}")
        z.append(row)
        text.append(trow)

    vals = [v for row in z for v in row if v is not None]
    if vals:
        peak = max(abs(v) for v in vals)
        lim = min(HEATMAP_RET_CAP_PCT, max(10.0, peak))
    else:
        lim = HEATMAP_RET_CAP_PCT

    return {
        "months": list(MONTH_LABELS),
        "years": [str(y) for y in years],
        "z": z,
        "text": text,
        "zmin": -lim,
        "zmax": lim,
        # Axis / cell highlight for the still-open (or latest) month.
        "current_month": MONTH_LABELS[as_of.month - 1],
        "current_year": str(as_of.year),
        # Compact "Aug, 24" suffix; None when the month closed.
        "open_through": open_through,
    }


def _sma(values: list[float], window: int) -> list[float | None]:
    """Simple moving average; ``None`` until the window is warm."""
    out: list[float | None] = [None] * len(values)
    if window <= 0:
        return out
    run = 0.0
    for i, v in enumerate(values):
        run += v
        if i >= window:
            run -= values[i - window]
        if i + 1 >= window:
            out[i] = run / window
    return out


def _ema(values: list[float], window: int) -> list[float | None]:
    """Exponential moving average seeded with the SMA of the first window."""
    out: list[float | None] = [None] * len(values)
    if window <= 0 or len(values) < window:
        return out
    alpha = 2.0 / (window + 1.0)
    seed = sum(values[:window]) / window
    out[window - 1] = seed
    prev = seed
    for i in range(window, len(values)):
        prev = alpha * values[i] + (1.0 - alpha) * prev
        out[i] = prev
    return out


def _atr(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    window: int,
) -> list[float | None]:
    """Average True Range (Wilder) over ``window`` periods."""
    n = len(closes)
    out: list[float | None] = [None] * n
    if n == 0 or window <= 0:
        return out
    trs: list[float] = []
    for i in range(n):
        if i == 0:
            trs.append(highs[i] - lows[i])
        else:
            trs.append(
                max(
                    highs[i] - lows[i],
                    abs(highs[i] - closes[i - 1]),
                    abs(lows[i] - closes[i - 1]),
                )
            )
    if n < window:
        return out
    atr = sum(trs[:window]) / window
    out[window - 1] = atr
    for i in range(window, n):
        atr = (atr * (window - 1) + trs[i]) / window
        out[i] = atr
    return out


def _cross_events(
    dates: list[str],
    fast: list[float | None],
    slow: list[float | None],
) -> tuple[list[str], list[float], list[str], list[float]]:
    """Return (up_x, up_y, down_x, down_y) where fast crosses slow."""
    up_x: list[str] = []
    up_y: list[float] = []
    down_x: list[str] = []
    down_y: list[float] = []
    for i in range(1, len(dates)):
        f0, s0 = fast[i - 1], slow[i - 1]
        f1, s1 = fast[i], slow[i]
        if f0 is None or s0 is None or f1 is None or s1 is None:
            continue
        if f0 <= s0 and f1 > s1:
            up_x.append(dates[i])
            up_y.append(f1)
        elif f0 >= s0 and f1 < s1:
            down_x.append(dates[i])
            down_y.append(f1)
    return up_x, up_y, down_x, down_y


def _larsson_states(
    ema_fast: list[float | None],
    ema_slow: list[float | None],
    atr: list[float | None],
    *,
    atr_mult: float = 0.3,
) -> list[str | None]:
    """Larsson-style regime: bull / bear / neutral via EMA gap vs ATR band.

    Reconstruction of the publicly described EMA30/EMA60 + 0.3·ATR(60) filter
    (the commercial Larsson Line parameters are proprietary).
    """
    states: list[str | None] = []
    for f, s, a in zip(ema_fast, ema_slow, atr, strict=True):
        if f is None or s is None or a is None:
            states.append(None)
            continue
        gap = f - s
        zone = atr_mult * a
        if gap > zone:
            states.append("bull")
        elif gap < -zone:
            states.append("bear")
        else:
            states.append("neutral")
    return states


def _mask_by_state(
    values: list[float | None],
    states: list[str | None],
    want: str,
) -> list[float | None]:
    """Keep ``values`` only where ``states == want`` (else ``None`` for gaps)."""
    return [v if s == want else None for v, s in zip(values, states, strict=True)]


def _exclusive_band_end(dates: list[str], end_idx: int, *, bar: str) -> str:
    """Exclusive ``x1`` for a regime band ending at ``end_idx`` (inclusive)."""
    nxt = end_idx + 1
    if nxt < len(dates):
        return dates[nxt]
    last = date.fromisoformat(dates[end_idx])
    if bar == "monthly":
        if last.month == 12:
            return date(last.year + 1, 1, 1).isoformat()
        return date(last.year, last.month + 1, 1).isoformat()
    return (last + timedelta(days=1)).isoformat()


def _larsson_regime_bands(
    dates: list[str],
    states: list[str | None],
    *,
    bar: str = "daily",
) -> list[dict[str, str]]:
    """Collapse consecutive bull/bear states into shaded ``[start, end)`` bands."""
    bands: list[dict[str, str]] = []
    i = 0
    n = len(dates)
    while i < n:
        state = states[i]
        if state not in ("bull", "bear"):
            i += 1
            continue
        j = i + 1
        while j < n and states[j] == state:
            j += 1
        bands.append(
            {
                "start": dates[i],
                "end": _exclusive_band_end(dates, j - 1, bar=bar),
                "state": state,
            }
        )
        i = j
    return bands


def _pres_cycle_year(year: int) -> int:
    """1=post-election, 2=midterm, 3=pre-election, 4=election (year % 4 == 0)."""
    remainder = year % 4
    return 4 if remainder == 0 else remainder


def _halving_overlay() -> dict[str, object]:
    """Halving events + subsidy-epoch bands for the long-term chart overlay."""
    events: list[dict[str, object]] = []
    for i, (when, detail) in enumerate(BTC_HALVINGS):
        events.append(
            {
                "date": when.isoformat(),
                "id": f"H{i + 1}",
                "year": when.year,
                "label": f"Halving {when.year}",
                "short": f"H{when.year}",
                "detail": detail,
                "estimated": False,
            }
        )
    events.append(
        {
            "date": NEXT_HALVING_EST.isoformat(),
            "id": "H5",
            "year": NEXT_HALVING_EST.year,
            "label": f"Halving ~{NEXT_HALVING_EST.year}",
            "short": "H5",
            "detail": "3.125 → 1.5625 BTC (est.)",
            "estimated": True,
        }
    )
    epoch_starts = [BTC_GENESIS, *[when for when, _ in BTC_HALVINGS]]
    epoch_ends = [when for when, _ in BTC_HALVINGS] + [NEXT_HALVING_EST]
    epoch_rewards = ("50 BTC", "25 BTC", "12.5 BTC", "6.25 BTC", "3.125 BTC")
    epochs: list[dict[str, object]] = []
    for i, (start, end, reward) in enumerate(
        zip(epoch_starts, epoch_ends, epoch_rewards, strict=True)
    ):
        epochs.append(
            {
                "start": start.isoformat(),
                "end": end.isoformat(),
                "id": f"E{i}",
                "reward": reward,
                "label": f"{reward} epoch",
                "short": reward,
            }
        )
    return {"events": events, "epochs": epochs}


def _presidential_overlay(*, until: date) -> dict[str, object]:
    """US 4-year cycle year bands, administrations, and election markers."""
    start_year = 2009
    end_year = max(until.year + 1, 2029)
    years: list[dict[str, object]] = []
    for year in range(start_year, end_year + 1):
        cycle = _pres_cycle_year(year)
        years.append(
            {
                "start": date(year, 1, 1).isoformat(),
                "end": date(year + 1, 1, 1).isoformat(),
                "year": year,
                "cycle": cycle,
                "label": PRES_CYCLE_LABELS[cycle - 1],
                "short": f"Y{cycle}",
            }
        )
    admins = [
        {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "label": name,
        }
        for start, end, name in US_ADMINS
    ]
    elections = [
        {"date": when.isoformat(), "label": f"{when.year} election"}
        for when in US_ELECTION_DATES
    ]
    return {"years": years, "admins": admins, "elections": elections}


def _larsson_series(
    dates: list[str],
    highs: list[float],
    lows: list[float],
    closes: list[float],
    *,
    bar: str = "daily",
) -> dict[str, object]:
    """EMA30/60 + ATR regime series and gold/blue shade bands for one bar size."""
    ema30 = _ema(closes, 30)
    ema60 = _ema(closes, 60)
    atr60 = _atr(highs, lows, closes, 60)
    states = _larsson_states(ema30, ema60, atr60)
    latest = next((s for s in reversed(states) if s is not None), None)
    return {
        "dates": dates,
        "ema30": ema30,
        "ema60": ema60,
        "larsson_bull": _mask_by_state(ema30, states, "bull"),
        "larsson_bear": _mask_by_state(ema30, states, "bear"),
        "larsson_neutral": _mask_by_state(ema30, states, "neutral"),
        "larsson_state": latest,
        "larsson_bands": _larsson_regime_bands(dates, states, bar=bar),
    }


def _long_term_indicator_seed(
    snapshot: MarketSnapshot,
    *,
    live: LiveTape | None = None,
    daily_tail: tuple[DailyFill, ...] | None = None,
) -> dict[str, object]:
    """Build JSON-serializable series for the long-term chart controls."""
    date_objs = list(snapshot.btc_dates)
    opens = list(snapshot.btc_opens)
    highs = list(snapshot.btc_highs)
    lows = list(snapshot.btc_lows)
    closes = list(snapshot.btc_closes)
    volumes = list(snapshot.btc_volumes)

    complete_through = date_objs[-1] if date_objs else None
    through = _session_today()
    if complete_through is not None and complete_through > through:
        through = complete_through
    if live is not None:
        live_day = _to_tz(live.as_of, DASHBOARD_TZ).date()
        if live_day > through:
            through = live_day
    _splice_daily_tail(
        date_objs,
        opens,
        highs,
        lows,
        closes,
        volumes,
        daily_tail,
        through=through,
    )
    _merge_live_bar(
        date_objs, opens, highs, lows, closes, volumes, live, through=through
    )
    clamped_h, clamped_l = _clamp_ohlc_wicks(opens, highs, lows, closes)
    highs[:] = list(clamped_h)
    lows[:] = list(clamped_l)

    dates = [d.isoformat() for d in date_objs]
    # Shade only the in-progress session — never the stale hole before it.
    live_from = through if date_objs else None
    live_appended = bool(
        live is not None
        and date_objs
        and date_objs[-1] == _to_tz(live.as_of, DASHBOARD_TZ).date()
    )

    sma50 = _sma(closes, 50)
    sma200 = _sma(closes, 200)
    sma111 = _sma(closes, 111)
    sma350 = _sma(closes, 350)
    pi_double = [None if v is None else 2.0 * v for v in sma350]
    golden_x, golden_y, death_x, death_y = _cross_events(dates, sma50, sma200)
    pi_up_x, pi_up_y, _, _ = _cross_events(dates, sma111, pi_double)

    larsson = _larsson_series(dates, highs, lows, closes, bar="daily")

    m_dates, m_o, m_h, m_l, m_c, m_v = _monthly_ohlcv(
        tuple(date_objs),
        tuple(opens),
        tuple(highs),
        tuple(lows),
        tuple(closes),
        tuple(volumes),
    )
    m_iso = [d.isoformat() for d in m_dates]
    m_closes = list(m_c)
    m_sma50 = _sma(m_closes, 50)
    m_sma200 = _sma(m_closes, 200)
    m_golden_x, m_golden_y, m_death_x, m_death_y = _cross_events(
        m_iso, m_sma50, m_sma200
    )
    monthly_larsson = _larsson_series(
        m_iso, list(m_h), list(m_l), m_closes, bar="monthly"
    )

    end = date_objs[-1] if date_objs else None
    length_starts: dict[str, str | None] = {}
    for key, days in CHART_LENGTH_DAYS.items():
        if not dates:
            length_starts[key] = None
            continue
        if days is None or end is None:
            length_starts[key] = dates[0]
            continue
        target = end - timedelta(days=days)
        length_starts[key] = next(
            (d.isoformat() for d in date_objs if d >= target),
            dates[0],
        )
    anchor = through
    for key in CHART_PERIOD_KEYS:
        if not dates:
            length_starts[key] = None
            continue
        length_starts[key] = _chart_period_start(anchor, key).isoformat()

    return {
        "through_date": through.isoformat(),
        "live_from": live_from.isoformat() if live_from is not None else None,
        "complete_through": (
            complete_through.isoformat() if complete_through is not None else None
        ),
        "live_label": "Live" if live_appended else "Today",
        "dates": dates,
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": volumes,
        "default_length": CHART_DEFAULT_LENGTH,
        "length_starts": length_starts,
        "sma50": sma50,
        "sma200": sma200,
        "sma111": sma111,
        "pi350x2": pi_double,
        "golden_x": golden_x,
        "golden_y": golden_y,
        "death_x": death_x,
        "death_y": death_y,
        "pi_top_x": pi_up_x,
        "pi_top_y": pi_up_y,
        "ema30": larsson["ema30"],
        "ema60": larsson["ema60"],
        "larsson_bull": larsson["larsson_bull"],
        "larsson_bear": larsson["larsson_bear"],
        "larsson_neutral": larsson["larsson_neutral"],
        "larsson_state": larsson["larsson_state"],
        "larsson_bands": larsson["larsson_bands"],
        "halvings": _halving_overlay(),
        "pres_cycle": _presidential_overlay(
            until=through if end is None else max(through, end)
        ),
        "monthly": {
            "dates": m_iso,
            "open": list(m_o),
            "high": list(m_h),
            "low": list(m_l),
            "close": m_closes,
            "volume": list(m_v),
            "sma50": m_sma50,
            "sma200": m_sma200,
            "golden_x": m_golden_x,
            "golden_y": m_golden_y,
            "death_x": m_death_x,
            "death_y": m_death_y,
            "ema30": monthly_larsson["ema30"],
            "ema60": monthly_larsson["ema60"],
            "larsson_bull": monthly_larsson["larsson_bull"],
            "larsson_bear": monthly_larsson["larsson_bear"],
            "larsson_neutral": monthly_larsson["larsson_neutral"],
            "larsson_state": monthly_larsson["larsson_state"],
            "larsson_bands": monthly_larsson["larsson_bands"],
        },
    }


def render_dashboard_html(
    snapshot: MarketSnapshot,
    *,
    live: LiveTape | None = None,
    daily_tail: tuple[DailyFill, ...] | None = None,
) -> str:
    """Return a self-contained single-page HTML dashboard."""
    try:
        import plotly  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            "plotly is required for the dashboard. Install with: uv sync"
        ) from exc

    lt_seed = _long_term_indicator_seed(
        snapshot, live=live, daily_tail=daily_tail
    )
    lt_seed_json = json.dumps(lt_seed, separators=(",", ":"))
    heatmap_seed = _btc_monthly_gains_seed(snapshot)
    heatmap_seed_json = json.dumps(heatmap_seed, separators=(",", ":"))
    heat_years = heatmap_seed["years"]
    n_heat_years = len(heat_years) if isinstance(heat_years, list) else 0
    heat_plot_h = max(320, 40 * max(n_heat_years, 1) + 110)
    chart_html = f"""
      <script charset="utf-8"
              src="https://cdn.plot.ly/plotly-3.6.0.min.js"
              integrity="sha256-QaOVwtVY0T02VaHrr6pnoHLCwayMJp4O5n4YyaE3rJk="
              crossorigin="anonymous"></script>
      <div class="lt-toolbar">
        <div class="live-btn-group" id="lt-bars"
             aria-label="Long-term bar size">
          <button type="button" class="live-btn active"
                  data-lt-bar="daily">Daily</button>
          <button type="button" class="live-btn"
                  data-lt-bar="monthly">Monthly</button>
        </div>
        <div class="live-btn-group" id="lt-styles"
             aria-label="Long-term price style">
          <button type="button" class="live-btn active"
                  data-lt-style="line">Line</button>
          <button type="button" class="live-btn"
                  data-lt-style="candle">Candle</button>
        </div>
        <div class="lt-range-groups">
        <div class="live-btn-group" id="lt-periods"
             aria-label="Calendar period">
          <button type="button" class="live-btn" data-lt-length="mtd"
                  title="Month to date">MTD</button>
          <button type="button" class="live-btn" data-lt-length="qtd"
                  title="Quarter to date">QTD</button>
          <button type="button" class="live-btn" data-lt-length="ytd"
                  title="Year to date">YTD</button>
        </div>
        <div class="live-btn-group" id="lt-lengths"
             aria-label="Lookback length">
          <button type="button" class="live-btn" data-lt-length="3m">3M</button>
          <button type="button" class="live-btn" data-lt-length="1y">1Y</button>
          <button type="button" class="live-btn active"
                  data-lt-length="2y">2Y</button>
          <button type="button" class="live-btn" data-lt-length="5y">5Y</button>
          <button type="button" class="live-btn"
                  data-lt-length="all">All</button>
        </div>
        </div>
        <div class="lt-ind-group" id="lt-indicators"
             aria-label="Long-term indicators">
          <label class="lt-ind" title="50/200 SMA trend filter (golden/death cross)">
            <input type="checkbox" id="lt-ind-sma" /> 50/200 SMA
          </label>
          <label class="lt-ind" title="Pi Cycle Top: 111 DMA vs 2×350 DMA (daily)">
            <input type="checkbox" id="lt-ind-pi" /> Pi Cycle
          </label>
          <label class="lt-ind"
                 title="Larsson EMA30/60 + ATR band; gold bull / blue bear">
            <input type="checkbox" id="lt-ind-larsson" /> Larsson Line
          </label>
          <button type="button" class="live-btn lt-ind-clear" id="lt-ind-clear"
                  title="Turn off all indicator and cycle overlays">Clear</button>
        </div>
        <div class="lt-ind-group lt-cycle-group" id="lt-cycles"
             aria-label="Cycle overlays">
          <label class="lt-ind"
                 title="Shade subsidy epochs and mark Bitcoin halving dates">
            <input type="checkbox" id="lt-ind-halving" /> Halvings
          </label>
          <label class="lt-ind"
                 title="Mark the 4-year US presidential cycle (Y1–Y4)">
            <input type="checkbox" id="lt-ind-pres" /> Pres. cycle
          </label>
        </div>
        <span class="live-chart-label" id="lt-ind-status"></span>
      </div>
      <div class="lt-cycle-legend" id="lt-cycle-legend" hidden
           aria-live="polite"></div>
      <div id="lt-plot" class="lt-daily-plot"></div>
      <script type="application/json" id="lt-seed">{lt_seed_json}</script>
"""
    heatmap_html = f"""
    <section class="heatmap" aria-label="BTC monthly gains heatmap">
      <h2>BTC monthly gains</h2>
      <p class="heatmap-note">
        Calendar-month close-to-close returns (%). Green = up, red = down;
        intensity scales with magnitude (color clipped at
        ±{HEATMAP_RET_CAP_PCT:.0f}%). The open month is highlighted; its
        year label shows the last daily close used.
      </p>
      <div id="btc-month-heatmap" class="month-heatmap-plot"
           style="min-height:{heat_plot_h}px"></div>
      <script type="application/json"
              id="btc-month-heatmap-seed">{heatmap_seed_json}</script>
    </section>
"""
    heatmap_js = """
<script>
(function () {
  const plotEl = document.getElementById("btc-month-heatmap");
  const seedEl = document.getElementById("btc-month-heatmap-seed");
  if (!plotEl || !seedEl || typeof Plotly === "undefined") return;
  const seed = JSON.parse(seedEl.textContent);
  const months = seed.months || [];
  const years = seed.years || [];
  const curMonth = seed.current_month || null;
  const curYear = seed.current_year || null;
  const through = seed.open_through || null;
  const mi = curMonth != null ? months.indexOf(curMonth) : -1;
  const yi = curYear != null ? years.indexOf(curYear) : -1;
  // Tall enough that every year tick has room; months forced via tickmode.
  const h = Math.max(320, 40 * Math.max(years.length, 1) + 110);
  const tickFont = {
    color: "#e8e6e1",
    size: 12,
    family: "IBM Plex Sans, Segoe UI, sans-serif"
  };
  const accent = "#f7931a";
  const asof = "#c9a36a";
  // Bold + accent the current month / year axis labels (Plotly allows <b>/<span>).
  // Open year reads "2026 Aug, 24" — date quieter than the year.
  const monthTickText = months.map(function (m) {
    if (m !== curMonth) return m;
    return "<b style='color:" + accent + "'>" + m + "</b>";
  });
  const yearTickText = years.map(function (y) {
    if (y !== curYear) return y;
    if (through) {
      return (
        "<b style='color:" + accent + "'>" + y + "</b>" +
        "<span style='color:" + asof + ";font-size:11px;font-weight:400'> " +
        through + "</span>"
      );
    }
    return "<b style='color:" + accent + "'>" + y + "</b>";
  });
  const cellText = (seed.text || []).map(function (row, r) {
    return (row || []).map(function (cell, c) {
      if (r === yi && c === mi && cell) {
        return "<b>" + cell + "</b>";
      }
      return cell;
    });
  });
  const hoverText = (seed.text || []).map(function (row, r) {
    return (row || []).map(function (cell, c) {
      if (!cell) return "";
      if (r === yi && c === mi && through) {
        return cell + "% · " + curYear + " " + through;
      }
      return cell + "%";
    });
  });
  const shapes = [];
  if (mi >= 0 && yi >= 0) {
    // Category axes accept serial indices; ±0.5 frames one heatmap cell.
    shapes.push({
      type: "rect",
      xref: "x",
      yref: "y",
      x0: mi - 0.48,
      x1: mi + 0.48,
      y0: yi - 0.48,
      y1: yi + 0.48,
      line: { color: accent, width: 2.5 },
      fillcolor: "rgba(247, 147, 26, 0.12)",
      layer: "above"
    });
  }
  const trace = {
    type: "heatmap",
    x: months,
    y: years,
    z: seed.z,
    text: cellText,
    texttemplate: "%{text}",
    textfont: {
      size: 11,
      color: "#e8e6e1",
      family: "IBM Plex Sans, Segoe UI, sans-serif"
    },
    colorscale: [
      [0.0, "#b71c1c"],
      [0.35, "#e57373"],
      [0.5, "#1c2029"],
      [0.65, "#6fbf73"],
      [1.0, "#1b5e20"]
    ],
    zmid: 0,
    zmin: seed.zmin,
    zmax: seed.zmax,
    customdata: hoverText,
    hovertemplate: "%{y} %{x}<br>%{customdata}<extra></extra>",
    showscale: true,
    colorbar: {
      title: { text: "%", font: { color: "#9a958c", size: 11 } },
      tickfont: { color: "#9a958c", size: 10 },
      thickness: 12,
      len: 0.85,
      outlinewidth: 0,
      bgcolor: "rgba(0,0,0,0)"
    },
    xgap: 2,
    ygap: 2
  };
  const layout = {
    paper_bgcolor: "#0e1014",
    plot_bgcolor: "#12141a",
    margin: { l: through ? 118 : 72, r: through ? 118 : 36, t: 56, b: 52 },
    height: h,
    shapes: shapes,
    // Explicit tickvals so Plotly never skips Jan–Dec or year labels.
    xaxis: {
      title: {
        text: "Month",
        font: { color: "#9a958c", size: 11 },
        standoff: 8
      },
      side: "top",
      type: "category",
      categoryorder: "array",
      categoryarray: months,
      tickmode: "array",
      tickvals: months,
      ticktext: monthTickText,
      tickfont: tickFont,
      tickangle: 0,
      ticks: "outside",
      ticklen: 4,
      tickcolor: "#2a2e38",
      showline: true,
      linecolor: "#2a2e38",
      showgrid: false,
      fixedrange: true,
      // Draw the same month labels along the bottom edge.
      mirror: "allticks"
    },
    yaxis: {
      title: {
        text: "Year",
        font: { color: "#9a958c", size: 11 },
        standoff: 10
      },
      type: "category",
      categoryorder: "array",
      categoryarray: years,
      tickmode: "array",
      tickvals: years,
      ticktext: yearTickText,
      autorange: "reversed",
      tickfont: tickFont,
      ticks: "outside",
      ticklen: 4,
      tickcolor: "#2a2e38",
      showline: true,
      linecolor: "#2a2e38",
      showgrid: false,
      fixedrange: true,
      // Mirror year labels on the right for long calendars.
      mirror: "allticks"
    },
    font: { color: "#e8e6e1", family: "IBM Plex Sans, Segoe UI, sans-serif" }
  };
  Plotly.newPlot(plotEl, [trace], layout, {
    displayModeBar: false,
    responsive: true
  });
})();
</script>
"""
    lt_js = """
<script>
(function () {
  const bars = document.getElementById("lt-bars");
  const styles = document.getElementById("lt-styles");
  const lengths = document.getElementById("lt-lengths");
  const periods = document.getElementById("lt-periods");
  const plotEl = document.getElementById("lt-plot");
  const seedEl = document.getElementById("lt-seed");
  const statusEl = document.getElementById("lt-ind-status");
  const legendEl = document.getElementById("lt-cycle-legend");
  const smaCb = document.getElementById("lt-ind-sma");
  const piCb = document.getElementById("lt-ind-pi");
  const larssonCb = document.getElementById("lt-ind-larsson");
  const halvingCb = document.getElementById("lt-ind-halving");
  const presCb = document.getElementById("lt-ind-pres");
  const narrowMq = window.matchMedia
    ? window.matchMedia("(max-width: 720px)")
    : null;
  if (!bars || !styles || !lengths || !plotEl || !seedEl) return;
  if (typeof Plotly === "undefined") return;

  const seed = JSON.parse(seedEl.textContent);
  let barMode = "daily";
  let styleMode = "line";
  let lengthKey = seed.default_length || "2y";
  let xRange = null;
  let syncingY = false;
  let relayoutBound = false;

  function dateKey(v) {
    if (typeof v === "number" && Number.isFinite(v)) {
      return new Date(v).toISOString().slice(0, 10);
    }
    const m = String(v).match(/(\\d{4}-\\d{2}-\\d{2})/);
    return m ? m[1] : String(v);
  }

  function padEnd(iso) {
    const t = Date.parse(dateKey(iso) + "T00:00:00Z");
    if (!Number.isFinite(t)) return iso;
    return new Date(t + 86400000).toISOString().slice(0, 10);
  }

  function clipAsOf(v) {
    const k = dateKey(v);
    const cap = seed.through_date;
    if (cap && k > cap) return cap;
    return k;
  }

  function seriesEnd() {
    return seed.through_date || ((seed.dates || [])[(seed.dates || []).length - 1]);
  }

  function sliderRange() {
    const dates = seed.dates || [];
    if (!dates.length) return null;
    const through = seriesEnd();
    return through ? [dates[0], padEnd(through)] : [dates[0], dates[dates.length - 1]];
  }

  function activeSeries() {
    return barMode === "monthly" ? (seed.monthly || seed) : seed;
  }

  function windowForLength(key) {
    const s = activeSeries();
    const dates = s.dates || [];
    if (!dates.length) return null;
    const end = padEnd(dates[dates.length - 1]);
    if (key === "all") {
      return [dates[0], padEnd(seriesEnd() || dates[dates.length - 1])];
    }
    const startHint = (seed.length_starts || {})[key];
    if (!startHint) return [dates[0], end];
    const snapped = dates.find(function (d) { return d >= startHint; });
    return [snapped || startHint, end];
  }

  function ensureXRange() {
    if (!xRange) xRange = windowForLength(lengthKey);
    return xRange;
  }

  function isXRelayout(ed) {
    if (!ed) return true;
    return Object.keys(ed).some(function (k) {
      return k.indexOf("xaxis") === 0;
    });
  }

  function resolveXRange(plot, ed) {
    ed = ed || {};
    if (ed["xaxis.autorange"]) return windowForLength("all");
    const a0 = ed["xaxis.range[0]"];
    const a1 = ed["xaxis.range[1]"];
    if (a0 != null && a1 != null) return [a0, a1];
    if (ed["xaxis.range"]) return ed["xaxis.range"];
    if (ed["xaxis.rangeslider.range"]) return ed["xaxis.rangeslider.range"];
    const xa = (plot.layout && plot.layout.xaxis) || {};
    if (xa.range && xa.range.length === 2) return xa.range;
    return ensureXRange();
  }

  function padLogRange(lo, hi) {
    if (!(lo > 0) || !(hi > 0)) return null;
    if (hi < lo) { const tmp = lo; lo = hi; hi = tmp; }
    if (hi === lo) return [lo / 1.05, hi * 1.05];
    const pad = Math.pow(hi / lo, 0.08);
    return [lo / pad, hi * pad];
  }

  function logAxisRange(lo, hi) {
    const padded = padLogRange(lo, hi);
    if (!padded) return null;
    return [Math.log10(padded[0]), Math.log10(padded[1])];
  }

  function isNarrow() {
    return !!(narrowMq && narrowMq.matches);
  }

  function toTime(v) {
    const k = dateKey(v);
    const t = Date.parse(k + "T00:00:00Z");
    return Number.isFinite(t) ? t : NaN;
  }

  function overlaps(start, end, x0, x1) {
    if (x0 == null || x1 == null) return true;
    return toTime(start) <= toTime(x1) && toTime(end) >= toTime(x0);
  }

  function inRange(d, x0, x1) {
    if (x0 == null || x1 == null) return true;
    const t = toTime(d);
    return t >= toTime(x0) && t <= toTime(x1);
  }

  function covering(items, asOf) {
    if (!asOf || !items || !items.length) return null;
    const k = dateKey(asOf);
    for (let i = 0; i < items.length; i++) {
      const it = items[i];
      if (it.start <= k && k < it.end) return it;
    }
    return null;
  }

  function visibleMid(start, end, x0, x1) {
    const lo = (x0 == null) ? toTime(start) : Math.max(toTime(start), toTime(x0));
    const hi = (x1 == null) ? toTime(end) : Math.min(toTime(end), toTime(x1));
    if (!(lo < hi) || !Number.isFinite(lo) || !Number.isFinite(hi)) return null;
    return new Date((lo + hi) / 2).toISOString().slice(0, 10);
  }

  function visibleDays(start, end, x0, x1) {
    const lo = (x0 == null) ? toTime(start) : Math.max(toTime(start), toTime(x0));
    const hi = (x1 == null) ? toTime(end) : Math.min(toTime(end), toTime(x1));
    if (!(lo < hi) || !Number.isFinite(lo) || !Number.isFinite(hi)) return 0;
    return (hi - lo) / 86400000;
  }

  const EPOCH_FILL = [
    "rgba(255,255,255,0.04)",
    "rgba(0,188,212,0.12)",
    "rgba(38,166,154,0.12)",
    "rgba(94,114,228,0.13)",
    "rgba(247,147,26,0.13)"
  ];
  const PRES_FILL = {
    1: "rgba(176,191,198,0.18)",
    2: "rgba(255,202,40,0.14)",
    3: "rgba(102,187,106,0.16)",
    4: "rgba(239,83,80,0.14)"
  };
  const PRES_SHORT = {
    1: "Y1 post",
    2: "Y2 mid",
    3: "Y3 pre",
    4: "Y4 elect"
  };

  function fadeRgba(color, factor) {
    const m = String(color).match(
      /^rgba\\((\\d+),\\s*(\\d+),\\s*(\\d+),\\s*([\\d.]+)\\)$/
    );
    if (!m) return color;
    return "rgba(" + m[1] + "," + m[2] + "," + m[3] + ","
      + (Number(m[4]) * factor) + ")";
  }

  function vline(x, color, dash, width) {
    return {
      type: "line",
      xref: "x",
      yref: "paper",
      x0: x,
      x1: x,
      y0: 0,
      y1: 1,
      line: { color: color, width: width || 1.2, dash: dash || "dash" },
      layer: "above"
    };
  }

  function domainRect(x0, x1, y0, y1, fill) {
    return {
      type: "rect",
      xref: "x",
      yref: "y domain",
      x0: x0,
      x1: x1,
      y0: y0,
      y1: y1,
      fillcolor: fill,
      line: { width: 0 },
      layer: "below"
    };
  }

  function larssonShapes(bands) {
    if (!bands || !bands.length) return [];
    return bands.map(function (b) {
      const bull = b.state === "bull";
      return {
        type: "rect",
        xref: "x",
        yref: "y domain",
        x0: b.start,
        x1: b.end,
        y0: 0,
        y1: 1,
        fillcolor: bull ? "rgba(212,175,55,0.16)" : "rgba(93,173,226,0.16)",
        line: { width: 0 },
        layer: "below"
      };
    });
  }

  function halvingShapes(xr, faded) {
    const hv = seed.halvings || {};
    const shapes = [];
    const epochs = hv.epochs || [];
    const factor = faded ? 0.55 : 1;
    for (let i = 0; i < epochs.length; i++) {
      const e = epochs[i];
      if (xr && !overlaps(e.start, e.end, xr[0], xr[1])) continue;
      shapes.push(domainRect(
        e.start, e.end, 0, 1,
        fadeRgba(EPOCH_FILL[i % EPOCH_FILL.length], factor)
      ));
    }
    const events = hv.events || [];
    for (let i = 0; i < events.length; i++) {
      const ev = events[i];
      if (xr && !inRange(ev.date, xr[0], xr[1])) continue;
      shapes.push(vline(
        ev.date,
        ev.estimated ? "rgba(128,222,234,0.55)" : "#80deea",
        ev.estimated ? "dot" : "dash",
        ev.estimated ? 1.1 : 1.5
      ));
    }
    return shapes;
  }

  function presShapes(xr, asStrip) {
    const pc = seed.pres_cycle || {};
    const shapes = [];
    const y0 = asStrip ? 0.935 : 0;
    const years = pc.years || [];
    for (let i = 0; i < years.length; i++) {
      const y = years[i];
      if (xr && !overlaps(y.start, y.end, xr[0], xr[1])) continue;
      shapes.push(domainRect(
        y.start, y.end, y0, 1,
        PRES_FILL[y.cycle] || PRES_FILL[1]
      ));
    }
    if (asStrip && xr) {
      shapes.push({
        type: "rect",
        xref: "x",
        yref: "y domain",
        x0: xr[0],
        x1: xr[1],
        y0: y0,
        y1: 1,
        fillcolor: "rgba(0,0,0,0)",
        line: { color: "rgba(232,230,225,0.22)", width: 1 },
        layer: "above"
      });
    }
    const admins = pc.admins || [];
    for (let i = 0; i < admins.length; i++) {
      const a = admins[i];
      if (xr && !inRange(a.start, xr[0], xr[1])) continue;
      shapes.push(vline(a.start, "rgba(207,216,220,0.72)", "dash", 1.2));
    }
    const elections = pc.elections || [];
    for (let i = 0; i < elections.length; i++) {
      const e = elections[i];
      if (xr && !inRange(e.date, xr[0], xr[1])) continue;
      shapes.push(vline(e.date, "rgba(239,83,80,0.55)", "dot", 1));
    }
    return shapes;
  }

  function overlayShapes(series, xr) {
    const showLarsson = !!(larssonCb && larssonCb.checked);
    const showHalving = !!(halvingCb && halvingCb.checked);
    const showPres = !!(presCb && presCb.checked);
    const shapes = [];
    if (showHalving) {
      shapes.push.apply(shapes, halvingShapes(xr, showLarsson));
    }
    if (showPres) {
      shapes.push.apply(shapes, presShapes(xr, showHalving || showLarsson));
    }
    if (showLarsson) {
      shapes.push.apply(shapes, larssonShapes(series.larsson_bands));
    }
    shapes.push.apply(shapes, todayShapes());
    return shapes;
  }

  function todayShapes() {
    const from = seed.live_from;
    const through = seed.through_date;
    if (!from || !through) return [];
    return [
      {
        type: "rect",
        xref: "x",
        yref: "paper",
        x0: from,
        x1: padEnd(through),
        y0: 0,
        y1: 1,
        fillcolor: "rgba(247, 147, 26, 0.20)",
        line: { width: 0 },
        layer: "below"
      },
      {
        type: "line",
        xref: "x",
        yref: "paper",
        x0: through,
        x1: through,
        y0: 0,
        y1: 1,
        line: { color: "rgba(247, 147, 26, 0.85)", width: 1.4 },
        layer: "above"
      }
    ];
  }

  function todayAnnotations() {
    const through = seed.through_date;
    if (!through) return [];
    return [{
      x: through,
      y: 1,
      yref: "paper",
      text: seed.live_label || "Today",
      showarrow: false,
      xanchor: "right",
      yanchor: "bottom",
      xshift: -2,
      yshift: 2,
      font: { size: isNarrow() ? 9 : 10, color: "#f7931a" },
      bgcolor: "rgba(18,20,26,0.6)",
      borderpad: 2,
      captureevents: false
    }];
  }

  function overlayAnnotations(xr) {
    const anns = [];
    anns.push.apply(anns, todayAnnotations());
    if (isNarrow()) return anns;
    const showHalving = !!(halvingCb && halvingCb.checked);
    const showPres = !!(presCb && presCb.checked);
    const showLarsson = !!(larssonCb && larssonCb.checked);
    const asStrip = showHalving || showLarsson;
    if (showHalving) {
      const epochs = ((seed.halvings || {}).epochs) || [];
      for (let i = 0; i < epochs.length; i++) {
        const e = epochs[i];
        if (xr && !overlaps(e.start, e.end, xr[0], xr[1])) continue;
        if (visibleDays(e.start, e.end, xr && xr[0], xr && xr[1]) < 90) continue;
        const mid = visibleMid(e.start, e.end, xr && xr[0], xr && xr[1]);
        if (!mid) continue;
        anns.push({
          x: mid,
          y: 0.04,
          yref: "paper",
          text: e.short,
          showarrow: false,
          xanchor: "center",
          yanchor: "bottom",
          font: { size: 10, color: "#b2ebf2" },
          bgcolor: "rgba(18,20,26,0.55)",
          borderpad: 2,
          captureevents: false
        });
      }
      const events = ((seed.halvings || {}).events) || [];
      for (let i = 0; i < events.length; i++) {
        const ev = events[i];
        if (xr && !inRange(ev.date, xr[0], xr[1])) continue;
        anns.push({
          x: ev.date,
          y: 1,
          yref: "paper",
          text: ev.label,
          textangle: -90,
          showarrow: false,
          xanchor: "right",
          yanchor: "top",
          xshift: -4,
          yshift: -6,
          font: { size: 10, color: "#b2ebf2" },
          bgcolor: "rgba(18,20,26,0.62)",
          borderpad: 2,
          captureevents: false
        });
      }
    }
    if (showPres) {
      const years = ((seed.pres_cycle || {}).years) || [];
      for (let i = 0; i < years.length; i++) {
        const y = years[i];
        if (xr && !overlaps(y.start, y.end, xr[0], xr[1])) continue;
        if (visibleDays(y.start, y.end, xr && xr[0], xr && xr[1]) < 50) continue;
        const mid = visibleMid(y.start, y.end, xr && xr[0], xr && xr[1]);
        if (!mid) continue;
        anns.push({
          x: mid,
          y: asStrip ? 0.968 : 0.97,
          yref: "paper",
          text: y.short,
          showarrow: false,
          xanchor: "center",
          yanchor: "middle",
          font: { size: 9, color: asStrip ? "#e8e6e1" : "#cfd8dc" },
          bgcolor: asStrip ? "rgba(18,20,26,0.35)" : "rgba(18,20,26,0.45)",
          borderpad: 1,
          captureevents: false
        });
      }
      const admins = ((seed.pres_cycle || {}).admins) || [];
      for (let i = 0; i < admins.length; i++) {
        const a = admins[i];
        if (xr && !inRange(a.start, xr[0], xr[1])) continue;
        anns.push({
          x: a.start,
          y: 1,
          yref: "paper",
          text: a.label,
          showarrow: false,
          xanchor: "left",
          yanchor: "bottom",
          xshift: 5,
          yshift: 2,
          font: { size: 10, color: "#cfd8dc" },
          captureevents: false
        });
      }
    }
    return anns;
  }

  function swatch(color) {
    const s = document.createElement("span");
    s.className = "lt-swatch";
    s.style.background = color;
    return s;
  }

  function legendItem(color, text) {
    const item = document.createElement("span");
    item.className = "lt-legend-item";
    if (color) item.appendChild(swatch(color));
    item.appendChild(document.createTextNode(text));
    return item;
  }

  function updateCycleLegend(series, xr) {
    if (!legendEl) return;
    const showHalving = !!(halvingCb && halvingCb.checked);
    const showPres = !!(presCb && presCb.checked);
    while (legendEl.firstChild) legendEl.removeChild(legendEl.firstChild);
    const showToday = !!(seed.live_from && seed.through_date);
    if (!showHalving && !showPres && !showToday) {
      legendEl.hidden = true;
      return;
    }
    legendEl.hidden = false;
    const dates = series.dates || [];
    const asOf = clipAsOf((xr && xr[1]) || dates[dates.length - 1]);
    if (showHalving) {
      const epochs = ((seed.halvings || {}).epochs) || [];
      const cur = covering(epochs, asOf);
      if (cur) {
        const idx = epochs.indexOf(cur);
        const span = isNarrow()
          ? cur.label
          : (cur.label + " · " + cur.start.slice(0, 4) + "–" + cur.end.slice(0, 4));
        legendEl.appendChild(legendItem(
          EPOCH_FILL[Math.max(idx, 0) % EPOCH_FILL.length],
          span
        ));
      }
      const events = ((seed.halvings || {}).events) || [];
      const inWin = events.filter(function (ev) {
        return !xr || inRange(ev.date, xr[0], xr[1]);
      });
      if (inWin.length) {
        legendEl.appendChild(legendItem(
          null,
          inWin.map(function (ev) {
            return ev.estimated ? (ev.label + " (est.)") : ev.label;
          }).join(" · ")
        ));
      }
    }
    if (showPres) {
      const keyRow = document.createElement("span");
      keyRow.className = "lt-legend-key";
      [1, 2, 3, 4].forEach(function (c) {
        keyRow.appendChild(legendItem(PRES_FILL[c], PRES_SHORT[c]));
      });
      legendEl.appendChild(keyRow);
      const y = covering(((seed.pres_cycle || {}).years) || [], asOf);
      const admin = covering(((seed.pres_cycle || {}).admins) || [], asOf);
      const parts = [];
      if (y) parts.push(String(y.year) + " " + (isNarrow() ? y.short : y.label));
      if (admin) parts.push(admin.label);
      if (parts.length) {
        const now = legendItem(null, parts.join(" · "));
        now.className += " lt-legend-now";
        legendEl.appendChild(now);
      }
    }
    if (seed.live_from && seed.through_date) {
      legendEl.appendChild(legendItem(
        "rgba(247,147,26,0.9)",
        (seed.live_label || "Today") + " · " + seed.through_date
      ));
    }
  }

  function visiblePriceBounds(series, x0, x1) {
    const dates = series.dates || [];
    const d0 = x0 == null ? null : dateKey(x0);
    const d1 = x1 == null ? null : dateKey(x1);
    let lo = Infinity;
    let hi = -Infinity;
    const showSma = !!(smaCb && smaCb.checked);
    const showPi = !!(piCb && piCb.checked) && barMode === "daily";
    const showLarsson = !!(larssonCb && larssonCb.checked);
    const seriesList = [];
    if (styleMode === "candle") {
      seriesList.push(series.low, series.high, series.open, series.close);
    } else {
      seriesList.push(series.close);
    }
    if (showSma) seriesList.push(series.sma50, series.sma200);
    if (showPi) seriesList.push(series.sma111, series.pi350x2);
    if (showLarsson) {
      seriesList.push(
        series.ema60, series.larsson_bull, series.larsson_bear,
        series.larsson_neutral
      );
    }
    for (let i = 0; i < dates.length; i++) {
      const d = dateKey(dates[i]);
      if (d0 != null && d < d0) continue;
      if (d1 != null && d > d1) continue;
      for (let s = 0; s < seriesList.length; s++) {
        const arr = seriesList[s];
        if (!arr) continue;
        const v = arr[i];
        if (typeof v === "number" && Number.isFinite(v) && v > 0) {
          if (v < lo) lo = v;
          if (v > hi) hi = v;
        }
      }
    }
    if (!(lo < Infinity && hi > -Infinity)) return null;
    return logAxisRange(lo, hi);
  }

  function updateStatus(series) {
    if (!statusEl) return;
    const bits = [];
    if (smaCb && smaCb.checked) bits.push("50/200 SMA");
    if (piCb && piCb.checked && barMode === "daily") bits.push("Pi Cycle");
    if (larssonCb && larssonCb.checked) {
      const st = series.larsson_state || null;
      bits.push(st ? ("Larsson: " + st) : "Larsson Line");
    }
    if (halvingCb && halvingCb.checked) bits.push("Halvings");
    if (presCb && presCb.checked) {
      const dates = series.dates || [];
      const asOf = clipAsOf((xRange && xRange[1]) || dates[dates.length - 1]);
      const y = covering(((seed.pres_cycle || {}).years) || [], asOf);
      bits.push(y ? ("Pres " + y.short) : "Pres. cycle");
    }
    if (seed.through_date) {
      bits.push((seed.live_label || "to") + " " + seed.through_date);
    }
    bits.push(barMode === "monthly" ? "monthly" : "daily");
    bits.push(styleMode);
    bits.push(lengthKey.toUpperCase());
    statusEl.textContent = bits.join(" · ");
  }

  function setActiveGroup(group, attr, value) {
    if (!group) return;
    group.querySelectorAll(".live-btn").forEach(function (b) {
      b.classList.toggle("active", b.getAttribute(attr) === value);
    });
  }

  function syncIndicatorChrome() {
    // Pi Cycle is daily-only (350 DMA warm-up).
    if (piCb && piCb.parentElement) {
      const monthly = barMode === "monthly";
      piCb.parentElement.style.opacity = monthly ? "0.35" : "1";
      piCb.disabled = monthly;
      if (monthly) piCb.checked = false;
    }
  }

  function buildTraces(series) {
    const showSma = !!(smaCb && smaCb.checked);
    const showPi = !!(piCb && piCb.checked) && barMode === "daily";
    const showLarsson = !!(larssonCb && larssonCb.checked);
    const traces = [];
    if (styleMode === "candle") {
      traces.push({
        type: "candlestick",
        name: "BTC",
        x: series.dates,
        open: series.open,
        high: series.high,
        low: series.low,
        close: series.close,
        increasing: { line: { color: "#6fbf73" }, fillcolor: "#6fbf73" },
        decreasing: { line: { color: "#e57373" }, fillcolor: "#e57373" }
      });
    } else {
      traces.push({
        type: "scatter",
        mode: "lines",
        name: "BTC",
        x: series.dates,
        y: series.close,
        line: { color: "#F7931A", width: 2 }
      });
    }
    const lastD = (series.dates || [])[(series.dates || []).length - 1];
    const lastC = (series.close || [])[(series.close || []).length - 1];
    if (
      barMode === "daily"
      && seed.live_from
      && lastD
      && lastD >= seed.live_from
      && typeof lastC === "number"
    ) {
      traces.push({
        type: "scatter",
        mode: "markers",
        name: seed.live_label || "Today",
        x: [lastD],
        y: [lastC],
        marker: {
          color: "#f7931a",
          size: 9,
          symbol: "circle",
          line: { color: "#e8e6e1", width: 1 }
        },
        hovertemplate: (seed.live_label || "Today")
          + " %{x}<br>%{y:,.0f}<extra></extra>"
      });
    }
    if (showSma) {
      traces.push({
        type: "scatter", mode: "lines", name: "SMA 50",
        x: series.dates, y: series.sma50,
        line: { color: "#6fa8dc", width: 1.4 }
      });
      traces.push({
        type: "scatter", mode: "lines", name: "SMA 200",
        x: series.dates, y: series.sma200,
        line: { color: "#c27ba0", width: 1.4 }
      });
      if (series.golden_x && series.golden_x.length) {
        traces.push({
          type: "scatter", mode: "markers", name: "Golden cross",
          x: series.golden_x, y: series.golden_y,
          marker: { color: "#6fbf73", size: 9, symbol: "triangle-up" }
        });
      }
      if (series.death_x && series.death_x.length) {
        traces.push({
          type: "scatter", mode: "markers", name: "Death cross",
          x: series.death_x, y: series.death_y,
          marker: { color: "#e57373", size: 9, symbol: "triangle-down" }
        });
      }
    }
    if (showPi) {
      traces.push({
        type: "scatter", mode: "lines", name: "Pi 111 DMA",
        x: series.dates, y: series.sma111,
        line: { color: "#ffd666", width: 1.5 }
      });
      traces.push({
        type: "scatter", mode: "lines", name: "Pi 2×350 DMA",
        x: series.dates, y: series.pi350x2,
        line: { color: "#9b59b6", width: 1.5, dash: "dot" }
      });
      if (series.pi_top_x && series.pi_top_x.length) {
        traces.push({
          type: "scatter", mode: "markers", name: "Pi Cycle top",
          x: series.pi_top_x, y: series.pi_top_y,
          marker: {
            color: "#e74c3c", size: 11, symbol: "star",
            line: { color: "#fff", width: 0.5 }
          }
        });
      }
    }
    if (showLarsson) {
      traces.push({
        type: "scatter", mode: "lines", name: "EMA 60",
        x: series.dates, y: series.ema60,
        line: { color: "#7f8c8d", width: 1.2, dash: "dash" }
      });
      traces.push({
        type: "scatter", mode: "lines", name: "Larsson bull",
        x: series.dates, y: series.larsson_bull, connectgaps: false,
        line: { color: "#d4af37", width: 2.6 }
      });
      traces.push({
        type: "scatter", mode: "lines", name: "Larsson bear",
        x: series.dates, y: series.larsson_bear, connectgaps: false,
        line: { color: "#5dade2", width: 2.6 }
      });
      traces.push({
        type: "scatter", mode: "lines", name: "Larsson wait",
        x: series.dates, y: series.larsson_neutral, connectgaps: false,
        line: { color: "#95a5a6", width: 2.2 }
      });
    }
    updateStatus(series);
    return traces;
  }

  function layout(series) {
    const xr = ensureXRange();
    const yb = xr ? visiblePriceBounds(series, xr[0], xr[1]) : null;
    const showHalving = !!(halvingCb && halvingCb.checked);
    const showPres = !!(presCb && presCb.checked);
    const desktopAnns = !isNarrow() && (showHalving || showPres);
    const top = (desktopAnns && showPres) ? 52 : 36;
    updateCycleLegend(series, xr);
    return {
      template: "plotly_dark",
      height: 420,
      margin: { l: 48, r: 24, t: top, b: 24 },
      title: {
        text: "BTC " + barMode + " — " + styleMode + " (" + lengthKey + ")",
        font: { size: 14 }
      },
      xaxis: {
        title: "Date",
        type: "date",
        range: xr || undefined,
        rangeslider: {
          visible: true,
          thickness: 0.12,
          range: sliderRange() || undefined,
          bgcolor: "#0e1014",
          bordercolor: "#2a2e38"
        }
      },
      yaxis: {
        title: "USD",
        type: "log",
        fixedrange: false,
        autorange: !yb,
        range: yb || undefined
      },
      shapes: overlayShapes(series, xr),
      annotations: overlayAnnotations(xr),
      // Keep overlays from resetting zoom; length buttons own xRange.
      uirevision: barMode + ":" + styleMode + ":" + lengthKey,
      showlegend: true,
      legend: {
        orientation: "h",
        y: 1.12,
        x: (showPres && desktopAnns) ? 1 : 0,
        xanchor: (showPres && desktopAnns) ? "right" : "left",
        font: { size: 10, color: "#9a958c" }
      },
      paper_bgcolor: "#12141a",
      plot_bgcolor: "#12141a",
      font: { color: "#e8e6e1" }
    };
  }

  function syncY(ed) {
    if (syncingY || !isXRelayout(ed)) return;
    const series = activeSeries();
    const xr = resolveXRange(plotEl, ed);
    if (xr) xRange = [dateKey(xr[0]), dateKey(xr[1])];
    const bounds = visiblePriceBounds(
      series,
      xRange ? xRange[0] : null,
      xRange ? xRange[1] : null
    );
    updateCycleLegend(series, xRange);
    const payload = {
      shapes: overlayShapes(series, xRange),
      annotations: overlayAnnotations(xRange)
    };
    if (bounds) {
      payload["yaxis.type"] = "log";
      payload["yaxis.autorange"] = false;
      payload["yaxis.range"] = bounds;
    }
    syncingY = true;
    Plotly.relayout(plotEl, payload).then(
      function () { syncingY = false; },
      function () { syncingY = false; }
    );
  }

  function bindRelayout() {
    if (relayoutBound || typeof plotEl.on !== "function") return;
    plotEl.on("plotly_relayout", syncY);
    relayoutBound = true;
  }

  function renderChart() {
    syncIndicatorChrome();
    const series = activeSeries();
    ensureXRange();
    const p = Plotly.react(plotEl, buildTraces(series), layout(series), {
      displayModeBar: false, responsive: true
    });
    Promise.resolve(p).then(function () {
      bindRelayout();
      syncY({});
    });
  }

  bars.querySelectorAll(".live-btn").forEach(function (btn) {
    btn.addEventListener("click", function () {
      const next = btn.getAttribute("data-lt-bar");
      if (!next || next === barMode) return;
      barMode = next;
      setActiveGroup(bars, "data-lt-bar", barMode);
      // Re-apply the selected length on the new bar series.
      xRange = windowForLength(lengthKey);
      renderChart();
    });
  });

  styles.querySelectorAll(".live-btn").forEach(function (btn) {
    btn.addEventListener("click", function () {
      const next = btn.getAttribute("data-lt-style");
      if (!next || next === styleMode) return;
      styleMode = next;
      setActiveGroup(styles, "data-lt-style", styleMode);
      renderChart();
    });
  });

  lengths.querySelectorAll(".live-btn").forEach(function (btn) {
    btn.addEventListener("click", onLengthClick);
  });
  if (periods) {
    periods.querySelectorAll(".live-btn").forEach(function (btn) {
      btn.addEventListener("click", onLengthClick);
    });
  }

  function onLengthClick() {
    const next = this.getAttribute("data-lt-length");
    if (!next) return;
    lengthKey = next;
    setActiveGroup(lengths, "data-lt-length", lengthKey);
    setActiveGroup(periods, "data-lt-length", lengthKey);
    xRange = windowForLength(lengthKey);
    renderChart();
  }

  [smaCb, piCb, larssonCb, halvingCb, presCb].forEach(function (el) {
    if (el) el.addEventListener("change", renderChart);
  });

  const clearBtn = document.getElementById("lt-ind-clear");
  if (clearBtn) {
    clearBtn.addEventListener("click", function () {
      [smaCb, piCb, larssonCb, halvingCb, presCb].forEach(function (el) {
        if (el && !el.disabled) el.checked = false;
      });
      renderChart();
    });
  }

  if (narrowMq) {
    if (typeof narrowMq.addEventListener === "function") {
      narrowMq.addEventListener("change", renderChart);
    } else if (typeof narrowMq.addListener === "function") {
      narrowMq.addListener(renderChart);
    }
  }

  setActiveGroup(lengths, "data-lt-length", lengthKey);
  setActiveGroup(periods, "data-lt-length", lengthKey);
  xRange = windowForLength(lengthKey);
  renderChart();
})();
</script>
"""

    live_html = ""
    live_js = ""
    if live is not None and live.bar_closes:
        seed = {
            "t_ms": [_ms(t) for t in live.bar_times],
            "open": list(live.bar_opens),
            "high": list(live.bar_highs),
            "low": list(live.bar_lows),
            "close": list(live.bar_closes),
            "as_of_ms": _ms(live.as_of),
            "interval": live.interval,
            "range": live.range_key,
            "source": live.source,
        }
        seed_json = json.dumps(seed, separators=(",", ":"))
        chg = live.change_24h_pct
        chg_txt = _fmt_pct(chg)
        chg_tone = (
            "pos" if (chg or 0) > 0 else ("neg" if (chg or 0) < 0 else "neu")
        )
        as_of_txt = _fmt_tz(live.as_of, DASHBOARD_TZ)
        hi = f"${live.high_24h:,.0f}" if live.high_24h is not None else "—"
        lo = f"${live.low_24h:,.0f}" if live.low_24h is not None else "—"

        def _btn(kind: str, value: str, label: str, active: bool) -> str:
            cls = "live-btn active" if active else "live-btn"
            return (
                f'<button type="button" class="{cls}" '
                f'data-{kind}="{html.escape(value)}">{html.escape(label)}</button>'
            )

        range_btns = "".join(
            [
                _btn("range", "1h", "1H", live.range_key == "1h"),
                _btn("range", "1d", "1D", live.range_key == "1d"),
                _btn("range", "7d", "7D", live.range_key == "7d"),
            ]
        )
        # Seed buttons for the tape's range; JS rebuilds when range changes.
        seed_range: LiveRange = live.range_key
        seed_intervals = INTERVALS_FOR_RANGE[seed_range]
        seed_interval = (
            live.interval
            if live.interval in seed_intervals
            else DEFAULT_INTERVAL_FOR_RANGE[seed_range]
        )
        interval_btns = "".join(
            _btn("interval", iv, iv, iv == seed_interval) for iv in seed_intervals
        )
        tz_btns = "".join(
            _btn("tz", key, label, key == DEFAULT_LIVE_TZ)
            for key, _iana, label in LIVE_TZ_PRESETS
        )
        tz_map = {
            key: {"iana": iana, "label": label}
            for key, iana, label in LIVE_TZ_PRESETS
        }
        tz_map_js = json.dumps(tz_map, separators=(",", ":"))
        intervals_for_range_js = json.dumps(
            INTERVALS_FOR_RANGE, separators=(",", ":")
        )
        default_interval_js = json.dumps(
            DEFAULT_INTERVAL_FOR_RANGE, separators=(",", ":")
        )
        live_html = f"""
    <section class="live" aria-label="Live BTC tape">
      <div class="live-head">
        <div class="live-quote">
          <p class="live-kicker">Latest <span class="pulse">LIVE</span></p>
          <p class="live-price" id="live-price">${live.last:,.2f}</p>
          <p class="live-meta">
            <span id="live-chg" class="tone-{chg_tone}">{chg_txt}</span> 24h
            · H {hi} / L {lo}
            · <span id="live-asof">{as_of_txt}</span>
            · <span id="live-source">{html.escape(live.source)}</span>
          </p>
        </div>
        <div class="live-chart">
          <div class="live-toolbar">
            <div class="live-btn-group" id="live-ranges" aria-label="Chart range">
              {range_btns}
            </div>
            <div class="live-btn-group" id="live-intervals" aria-label="Candle size">
              {interval_btns}
            </div>
            <div class="live-btn-group" id="live-tzs" aria-label="Timezone">
              {tz_btns}
            </div>
            <span class="live-chart-label" id="live-chart-label"></span>
          </div>
          <div id="live-candle-plot" class="live-candle-plot"></div>
        </div>
      </div>
    </section>
"""
        # Candles + ticker refresh in-browser (Binance public REST; no server).
        live_js = f"""
<script type="application/json" id="live-seed">{seed_json}</script>
<script>
(function () {{
  const PRICE_EL = document.getElementById("live-price");
  const CHG_EL = document.getElementById("live-chg");
  const ASOF_EL = document.getElementById("live-asof");
  const SRC_EL = document.getElementById("live-source");
  const PLOT_EL = document.getElementById("live-candle-plot");
  const LABEL_EL = document.getElementById("live-chart-label");
  const SEED_EL = document.getElementById("live-seed");
  if (!PRICE_EL || !PLOT_EL || !SEED_EL || typeof Plotly === "undefined") return;

  const TZ_MAP = {tz_map_js};
  const TZ_STORAGE = "ccquant.liveTz";
  const RANGE_SEC = {{ "1h": 3600, "1d": 86400, "7d": 604800 }};
  const INTERVAL_SEC = {{
    "1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400
  }};
  // Coinbase has no 4h; 6h is the closest public granularity.
  const CB_GRAN = {{
    "1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 21600, "1d": 86400
  }};
  const INTERVALS_FOR_RANGE = {intervals_for_range_js};
  const DEFAULT_INTERVAL_FOR_RANGE = {default_interval_js};
  const BINANCE_PAGE = 1000;
  const BINANCE_HOSTS = [
    "https://data-api.binance.vision",
    "https://api.binance.com",
    "https://api.binance.us"
  ];
  let state = JSON.parse(SEED_EL.textContent);
  let rangeKey = state.range || "1h";
  let interval = state.interval || DEFAULT_INTERVAL_FOR_RANGE[rangeKey] || "5m";
  let tzKey = (function () {{
    try {{
      const saved = localStorage.getItem(TZ_STORAGE);
      if (saved && TZ_MAP[saved]) return saved;
    }} catch (err) {{}}
    return "{DEFAULT_LIVE_TZ}";
  }})();
  let lastAsOfMs = state.as_of_ms || null;
  let loadSeq = 0;

  function tzInfo() {{ return TZ_MAP[tzKey] || TZ_MAP.{DEFAULT_LIVE_TZ}; }}
  function allowedIntervals(r) {{
    return INTERVALS_FOR_RANGE[r] || INTERVALS_FOR_RANGE["1h"];
  }}
  function ensureIntervalForRange() {{
    const allowed = allowedIntervals(rangeKey);
    if (allowed.indexOf(interval) === -1) {{
      interval = DEFAULT_INTERVAL_FOR_RANGE[rangeKey] || allowed[0];
    }}
  }}
  function syncIntervalButtons() {{
    const group = document.getElementById("live-intervals");
    if (!group) return;
    const allowed = allowedIntervals(rangeKey);
    ensureIntervalForRange();
    group.innerHTML = allowed.map(function (iv) {{
      const cls = "live-btn" + (iv === interval ? " active" : "");
      return '<button type="button" class="' + cls + '" data-interval="'
        + iv + '">' + iv + "</button>";
    }}).join("");
    group.querySelectorAll(".live-btn").forEach(function (btn) {{
      btn.addEventListener("click", function () {{
        interval = btn.getAttribute("data-interval");
        syncIntervalButtons();
        loadCandles();
      }});
    }});
  }}
  function fmtUsd(v) {{
    return "$" + Number(v).toLocaleString(undefined, {{
      minimumFractionDigits: 2, maximumFractionDigits: 2
    }});
  }}
  function fmtPct(v) {{
    const x = 100 * v;
    return (x >= 0 ? "+" : "") + x.toFixed(1) + "%";
  }}
  function setTone(el, v) {{
    el.classList.remove("tone-pos", "tone-neg", "tone-neu");
    el.classList.add(v > 0 ? "tone-pos" : (v < 0 ? "tone-neg" : "tone-neu"));
  }}
  function tzParts(ms) {{
    const parts = new Intl.DateTimeFormat("en-US", {{
      timeZone: tzInfo().iana,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
      timeZoneName: "short"
    }}).formatToParts(new Date(ms));
    const m = {{}};
    parts.forEach(function (p) {{ if (p.type !== "literal") m[p.type] = p.value; }});
    return m;
  }}
  function fmtStamp(ms) {{
    const m = tzParts(ms);
    return m.year + "-" + m.month + "-" + m.day + " "
      + m.hour + ":" + m.minute + " " + m.timeZoneName;
  }}
  function fmtAxis(ms) {{
    const m = tzParts(ms);
    return m.year + "-" + m.month + "-" + m.day + " "
      + m.hour + ":" + m.minute + ":" + m.second;
  }}
  function barsWanted(r, iv) {{
    return Math.max(1, Math.floor(RANGE_SEC[r] / INTERVAL_SEC[iv]));
  }}
  function candleTrace(d) {{
    const xs = (d.t_ms || []).map(fmtAxis);
    return {{
      type: "candlestick",
      x: xs,
      open: d.open,
      high: d.high,
      low: d.low,
      close: d.close,
      increasing: {{ line: {{ color: "#6fbf73" }}, fillcolor: "#6fbf73" }},
      decreasing: {{ line: {{ color: "#e57373" }}, fillcolor: "#e57373" }},
      whiskerwidth: 0.4,
      name: "BTC"
    }};
  }}
  function layout() {{
    return {{
      margin: {{ l: 8, r: 48, t: 8, b: 28 }},
      height: 220,
      paper_bgcolor: "#12141a",
      plot_bgcolor: "#12141a",
      showlegend: false,
      uirevision: "live-candles",
      xaxis: {{
        autorange: true,
        gridcolor: "#2a2e38",
        tickfont: {{ size: 10, color: "#9a958c" }},
        rangeslider: {{ visible: false }},
        title: {{
          text: tzInfo().iana,
          font: {{ size: 10, color: "#9a958c" }}
        }}
      }},
      yaxis: {{
        autorange: true,
        side: "right",
        gridcolor: "#2a2e38",
        tickfont: {{ size: 10, color: "#9a958c" }},
        tickprefix: "$"
      }},
      font: {{ color: "#e8e6e1" }}
    }};
  }}
  function setLabel(n, note) {{
    if (!LABEL_EL) return;
    const want = barsWanted(rangeKey, interval);
    const short = n < want ? " · partial" : "";
    LABEL_EL.textContent = rangeKey.toUpperCase() + " · " + interval + " · "
      + n + "/" + want + " bars · " + tzInfo().label
      + short + (note ? " · " + note : "");
  }}
  function renderCandles(d, note) {{
    Plotly.react(PLOT_EL, [candleTrace(d)], layout(), {{
      displayModeBar: false, responsive: true
    }});
    setLabel((d.close || []).length, note || "");
  }}
  function setActive(groupId, attr, value) {{
    document.querySelectorAll("#" + groupId + " .live-btn").forEach(function (b) {{
      b.classList.toggle("active", b.getAttribute("data-" + attr) === value);
    }});
  }}
  function applyTz(next) {{
    if (!TZ_MAP[next]) return;
    tzKey = next;
    try {{ localStorage.setItem(TZ_STORAGE, tzKey); }} catch (err) {{}}
    setActive("live-tzs", "tz", tzKey);
    if (lastAsOfMs != null && ASOF_EL) ASOF_EL.textContent = fmtStamp(lastAsOfMs);
    renderCandles(state);
  }}

  async function fetchJson(url) {{
    const r = await fetch(url, {{ cache: "no-store" }});
    if (!r.ok) throw new Error(url + " → " + r.status);
    return r.json();
  }}

  async function fetchBinanceKlines(want) {{
    let lastErr = null;
    for (let h = 0; h < BINANCE_HOSTS.length; h++) {{
      const host = BINANCE_HOSTS[h];
      try {{
        const rows = [];
        let endTime = null;
        while (rows.length < want) {{
          const page = Math.min(BINANCE_PAGE, want - rows.length);
          let url = host + "/api/v3/klines?symbol=BTCUSDT"
            + "&interval=" + encodeURIComponent(interval)
            + "&limit=" + page;
          if (endTime != null) url += "&endTime=" + endTime;
          const batch = await fetchJson(url);
          if (!batch.length) break;
          rows.unshift.apply(rows, batch);
          endTime = Number(batch[0][0]) - 1;
          if (batch.length < page) break;
        }}
        if (!rows.length) throw new Error("empty klines");
        // Deduplicate by open time (pagination overlap).
        const seen = {{}};
        const dedup = [];
        for (let i = 0; i < rows.length; i++) {{
          const t = Number(rows[i][0]);
          if (seen[t]) continue;
          seen[t] = true;
          dedup.push(rows[i]);
        }}
        dedup.sort(function (a, b) {{ return Number(a[0]) - Number(b[0]); }});
        const cut = dedup.slice(Math.max(0, dedup.length - want));
        return {{ host: host, rows: cut }};
      }} catch (err) {{
        lastErr = err;
      }}
    }}
    throw lastErr || new Error("binance unavailable");
  }}

  async function fetchCoinbaseCandles(want) {{
    const gran = CB_GRAN[interval];
    if (!gran) throw new Error("coinbase gran");
    const rows = [];
    let endSec = Math.floor(Date.now() / 1000);
    // Coinbase returns max ~300 candles per request.
    while (rows.length < want) {{
      const page = Math.min(300, want - rows.length);
      const startSec = endSec - page * gran;
      const url = "https://api.exchange.coinbase.com/products/BTC-USD/candles"
        + "?granularity=" + gran
        + "&start=" + new Date(startSec * 1000).toISOString()
        + "&end=" + new Date(endSec * 1000).toISOString();
      const batch = await fetchJson(url);
      if (!batch.length) break;
      // [time, low, high, open, close, volume], newest first
      batch.sort(function (a, b) {{ return a[0] - b[0]; }});
      rows.unshift.apply(rows, batch);
      endSec = Number(batch[0][0]) - gran;
      if (batch.length < page) break;
    }}
    if (!rows.length) throw new Error("empty coinbase candles");
    const seen = {{}};
    const dedup = [];
    for (let i = 0; i < rows.length; i++) {{
      const t = Number(rows[i][0]);
      if (seen[t]) continue;
      seen[t] = true;
      dedup.push(rows[i]);
    }}
    dedup.sort(function (a, b) {{ return a[0] - b[0]; }});
    return dedup.slice(Math.max(0, dedup.length - want));
  }}

  function stateFromBinance(rows, source) {{
    return {{
      t_ms: rows.map(function (row) {{ return Number(row[0]); }}),
      open: rows.map(function (row) {{ return parseFloat(row[1]); }}),
      high: rows.map(function (row) {{ return parseFloat(row[2]); }}),
      low: rows.map(function (row) {{ return parseFloat(row[3]); }}),
      close: rows.map(function (row) {{ return parseFloat(row[4]); }}),
      as_of_ms: lastAsOfMs,
      interval: interval,
      range: rangeKey,
      source: source
    }};
  }}

  function stateFromCoinbase(rows) {{
    return {{
      t_ms: rows.map(function (row) {{ return Number(row[0]) * 1000; }}),
      open: rows.map(function (row) {{ return parseFloat(row[3]); }}),
      high: rows.map(function (row) {{ return parseFloat(row[2]); }}),
      low: rows.map(function (row) {{ return parseFloat(row[1]); }}),
      close: rows.map(function (row) {{ return parseFloat(row[4]); }}),
      as_of_ms: lastAsOfMs,
      interval: interval,
      range: rangeKey,
      source: "coinbase"
    }};
  }}

  async function loadCandles() {{
    const seq = ++loadSeq;
    const want = barsWanted(rangeKey, interval);
    if (LABEL_EL) LABEL_EL.textContent = "Loading " + rangeKey.toUpperCase()
      + " · " + interval + "…";
    try {{
      try {{
        const got = await fetchBinanceKlines(want);
        if (seq !== loadSeq) return;
        const host = got.host.replace("https://", "");
        state = stateFromBinance(got.rows, host);
        if (SRC_EL) SRC_EL.textContent = host;
        renderCandles(state);
        return;
      }} catch (binanceErr) {{
        const cb = await fetchCoinbaseCandles(want);
        if (seq !== loadSeq) return;
        state = stateFromCoinbase(cb);
        if (SRC_EL) SRC_EL.textContent = "coinbase";
        renderCandles(state);
      }}
    }} catch (err) {{
      if (seq !== loadSeq) return;
      renderCandles(state, "fetch blocked");
      if (SRC_EL) {{
        SRC_EL.textContent = (state.source || "seed") + " · live poll blocked";
      }}
    }}
  }}

  async function refreshTicker() {{
    let lastErr = null;
    for (let h = 0; h < BINANCE_HOSTS.length; h++) {{
      try {{
        const t = await fetchJson(
          BINANCE_HOSTS[h] + "/api/v3/ticker/24hr?symbol=BTCUSDT"
        );
        const last = parseFloat(t.lastPrice);
        const chg = parseFloat(t.priceChangePercent) / 100;
        PRICE_EL.textContent = fmtUsd(last);
        if (CHG_EL) {{
          CHG_EL.textContent = fmtPct(chg);
          setTone(CHG_EL, chg);
        }}
        lastAsOfMs = Number(t.closeTime);
        if (ASOF_EL) ASOF_EL.textContent = fmtStamp(lastAsOfMs);
        return;
      }} catch (err) {{
        lastErr = err;
      }}
    }}
    try {{
      const spot = await fetchJson(
        "https://api.coinbase.com/v2/prices/BTC-USD/spot"
      );
      PRICE_EL.textContent = fmtUsd(parseFloat(spot.data.amount));
      lastAsOfMs = Date.now();
      if (ASOF_EL) ASOF_EL.textContent = fmtStamp(lastAsOfMs);
    }} catch (err) {{
      if (SRC_EL && !SRC_EL.textContent.includes("poll blocked")) {{
        SRC_EL.textContent = (SRC_EL.textContent || "seed") + " · live poll blocked";
      }}
      void lastErr;
    }}
  }}

  document.querySelectorAll("#live-ranges .live-btn").forEach(function (btn) {{
    btn.addEventListener("click", function () {{
      rangeKey = btn.getAttribute("data-range");
      setActive("live-ranges", "range", rangeKey);
      syncIntervalButtons();
      loadCandles();
    }});
  }});
  document.querySelectorAll("#live-tzs .live-btn").forEach(function (btn) {{
    btn.addEventListener("click", function () {{
      applyTz(btn.getAttribute("data-tz"));
    }});
  }});

  setActive("live-tzs", "tz", tzKey);
  syncIntervalButtons();
  if (lastAsOfMs != null && ASOF_EL) ASOF_EL.textContent = fmtStamp(lastAsOfMs);
  renderCandles(state);
  refreshTicker();
  setInterval(refreshTicker, 15000);
  setInterval(loadCandles, 60000);
}})();
</script>
"""

    def chip(label: str, value: str, tone: str) -> str:
        return (
            f'<div class="chip tone-{html.escape(tone)}">'
            f"<span>{html.escape(label)}</span>"
            f"<strong>{html.escape(value)}</strong></div>"
        )

    def tone_for(sig: int) -> str:
        if sig > 0:
            return "pos"
        if sig < 0:
            return "neg"
        return "neu"

    stack_tone = (
        "pos"
        if snapshot.stack_score >= 2
        else ("neg" if snapshot.stack_score <= -2 else "neu")
    )

    btc_px = f"${snapshot.btc_close:,.0f}"
    live_metric = (
        f"${live.last:,.2f}" if live is not None else "—"
    )
    rel_vol_txt = (
        f"{snapshot.rel_vol_20:.1f}×"
        if snapshot.rel_vol_20 is not None
        else "—"
    )
    mtd_vol_txt = (
        f"{snapshot.mtd_vol_ratio:.1f}×"
        if snapshot.mtd_vol_ratio is not None
        else "—"
    )
    metrics = [
        ("Latest", live_metric),
        ("Daily close", btc_px),
        ("1d", _fmt_pct(snapshot.ret_1d)),
        ("7d", _fmt_pct(snapshot.ret_7d)),
        ("30d", _fmt_pct(snapshot.ret_30d)),
        ("YTD", _fmt_pct(snapshot.ret_ytd)),
        (
            "Universe up 7d",
            f"{_fmt_share(snapshot.pct_up_7d)} · {snapshot.n_universe}",
        ),
        ("Above 50d MA", _fmt_share(snapshot.pct_above_50)),
        ("Rel vol 20d", rel_vol_txt),
        ("MTD vol pace", mtd_vol_txt),
        ("Daily as of", snapshot.as_of.isoformat()),
    ]
    metrics_html = "".join(
        f'<div class="metric{" metric-latest" if k == "Latest" else ""}">'
        f"<span>{html.escape(k)}</span>"
        f"<strong>{html.escape(v)}</strong></div>"
        for k, v in metrics
    )
    chips_html = "".join(
        [
            chip("Liquidity", snapshot.liq_label, tone_for(snapshot.liq_signal)),
            chip("On-chain", snapshot.oc_label, tone_for(snapshot.oc_signal)),
            chip("Breadth", snapshot.breadth_label, tone_for(snapshot.breadth_signal)),
            chip(
                "ETF/MSTR",
                snapshot.demand_label,
                tone_for(snapshot.demand_signal),
            ),
            chip("Volume", snapshot.vol_label, tone_for(snapshot.vol_signal)),
            chip(
                "Stack",
                f"{snapshot.stack_label} ({snapshot.stack_score:+d})",
                stack_tone,
            ),
        ]
    )
    title = (
        f"{html.escape(snapshot.headline)} · "
        f"{html.escape(snapshot.stack_label)}"
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>ccquant — Market Tracker</title>
  <style>
    :root {{
      --bg: #0e1014;
      --fg: #e8e6e1;
      --muted: #9a958c;
      --line: #2a2e38;
      --accent: #f7931a;
      --pos: #6fbf73;
      --neg: #e57373;
      --neu: #b0a99a;
      --font: "IBM Plex Sans", "Segoe UI", sans-serif;
      --display: "IBM Plex Serif", Georgia, serif;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--fg);
      font-family: var(--font);
      line-height: 1.45;
    }}
    .page {{
      max-width: 920px;
      margin: 0 auto;
      padding: 2rem 1.25rem 3rem;
    }}
    .brand {{
      font-size: 0.8rem;
      letter-spacing: 0.14em;
      text-transform: uppercase;
      color: var(--accent);
      margin: 0 0 0.35rem;
    }}
    h1 {{
      font-family: var(--display);
      font-weight: 500;
      font-size: clamp(1.8rem, 4vw, 2.4rem);
      margin: 0 0 0.4rem;
      line-height: 1.15;
    }}
    .support {{
      color: var(--muted);
      margin: 0 0 1.4rem;
      max-width: 36rem;
    }}
    .live {{
      border: 1px solid var(--line);
      padding: 0.9rem 1rem 0.4rem;
      margin: 0 0 1.25rem;
    }}
    .live-head {{
      display: grid;
      grid-template-columns: minmax(150px, 200px) 1fr;
      gap: 0.75rem 1rem;
      align-items: stretch;
    }}
    @media (max-width: 720px) {{
      .live-head {{ grid-template-columns: 1fr; }}
    }}
    .live-quote {{
      display: flex;
      flex-direction: column;
      justify-content: center;
      min-height: 220px;
    }}
    .live-kicker {{
      margin: 0;
      font-size: 0.72rem;
      letter-spacing: 0.1em;
      text-transform: uppercase;
      color: var(--muted);
    }}
    .pulse {{
      color: var(--accent);
      letter-spacing: 0.14em;
    }}
    .live-price {{
      margin: 0.15rem 0 0.25rem;
      font-family: var(--display);
      font-size: clamp(1.8rem, 4vw, 2.5rem);
      font-weight: 500;
      line-height: 1.1;
      color: var(--accent);
    }}
    .live-meta {{
      margin: 0;
      font-size: 0.85rem;
      color: var(--muted);
    }}
    .live-meta .tone-pos {{ color: var(--pos); }}
    .live-meta .tone-neg {{ color: var(--neg); }}
    .live-meta .tone-neu {{ color: var(--neu); }}
    .live-chart {{
      min-width: 0;
      display: flex;
      flex-direction: column;
      gap: 0.35rem;
    }}
    .live-toolbar {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 0.45rem 0.75rem;
    }}
    .live-btn-group {{
      display: inline-flex;
      border: 1px solid var(--line);
    }}
    .live-btn {{
      appearance: none;
      background: transparent;
      border: 0;
      border-right: 1px solid var(--line);
      color: var(--muted);
      font: inherit;
      font-size: 0.72rem;
      letter-spacing: 0.04em;
      padding: 0.28rem 0.55rem;
      cursor: pointer;
    }}
    .live-btn:last-child {{ border-right: 0; }}
    .live-btn:hover {{ color: var(--fg); }}
    .live-btn.active {{
      background: #1c2029;
      color: var(--accent);
    }}
    .live-chart-label {{
      font-size: 0.7rem;
      color: var(--muted);
      margin-left: auto;
    }}
    .live-candle-plot {{
      width: 100%;
      min-height: 220px;
      background: #12141a;
    }}
    .metrics {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
      gap: 0.75rem 1rem;
      padding: 1rem 0 1.25rem;
      border-top: 1px solid var(--line);
      border-bottom: 1px solid var(--line);
      margin-bottom: 1.25rem;
    }}
    .metric span {{
      display: block;
      font-size: 0.72rem;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      color: var(--muted);
    }}
    .metric strong {{
      font-size: 1.15rem;
      font-weight: 560;
    }}
    .metric-latest strong {{
      color: var(--accent);
      font-size: 1.35rem;
    }}
    .chips {{
      display: flex;
      flex-wrap: wrap;
      gap: 0.5rem;
      margin: 0 0 1.25rem;
    }}
    .chip {{
      border: 1px solid var(--line);
      padding: 0.45rem 0.7rem;
      min-width: 7.5rem;
    }}
    .chip span {{
      display: block;
      font-size: 0.68rem;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: var(--muted);
    }}
    .chip strong {{ font-size: 0.95rem; }}
    .tone-pos strong {{ color: var(--pos); }}
    .tone-neg strong {{ color: var(--neg); }}
    .tone-neu strong {{ color: var(--neu); }}
    .chart {{
      margin: 0 0 1.25rem;
      border-top: 1px solid var(--line);
      padding-top: 0.5rem;
      min-width: 0;
      overflow-x: hidden;
    }}
    .lt-toolbar {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 0.45rem 0.75rem;
      margin: 0 0 0.35rem;
    }}
    .lt-range-groups {{
      display: inline-flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 0.45rem;
    }}
    .lt-ind-group {{
      display: inline-flex;
      flex-wrap: wrap;
      gap: 0.55rem 0.85rem;
      margin-left: 0.25rem;
      align-items: center;
    }}
    .lt-ind-group + .lt-ind-group {{
      padding-left: 0.65rem;
      border-left: 1px solid var(--line);
    }}
    .lt-ind {{
      display: inline-flex;
      align-items: center;
      gap: 0.3rem;
      font-size: 0.78rem;
      color: var(--muted);
      cursor: pointer;
      user-select: none;
      touch-action: manipulation;
      -webkit-tap-highlight-color: transparent;
    }}
    .lt-ind:has(input:checked) {{
      color: var(--fg);
    }}
    .lt-ind input {{
      accent-color: var(--accent);
      margin: 0;
    }}
    .lt-cycle-legend {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 0.4rem 0.85rem;
      min-height: 1.15rem;
      margin: 0 0 0.45rem;
      font-size: 0.72rem;
      color: var(--muted);
      line-height: 1.35;
    }}
    .lt-cycle-legend[hidden] {{
      display: none !important;
    }}
    .lt-legend-item {{
      display: inline-flex;
      align-items: center;
      gap: 0.28rem;
      white-space: nowrap;
    }}
    .lt-legend-key {{
      display: inline-flex;
      flex-wrap: wrap;
      gap: 0.35rem 0.65rem;
    }}
    .lt-legend-now {{
      color: var(--fg);
    }}
    .lt-swatch {{
      display: inline-block;
      width: 0.7rem;
      height: 0.7rem;
      border: 1px solid var(--line);
      flex: 0 0 auto;
    }}
    .lt-daily-plot {{
      width: 100%;
      max-width: 100%;
      min-height: 420px;
      overflow: hidden;
      background: #12141a;
    }}
    .lt-pane[hidden] {{ display: none !important; }}
    @media (max-width: 720px) {{
      .live-chart-label {{
        margin-left: 0;
        width: 100%;
      }}
      .lt-ind-group {{
        margin-left: 0;
      }}
      .lt-ind-group + .lt-ind-group {{
        padding-left: 0;
        border-left: 0;
        width: 100%;
      }}
      .lt-ind {{
        min-height: 2.5rem;
        padding: 0.3rem 0.5rem;
        border: 1px solid var(--line);
      }}
      .lt-ind:has(input:checked) {{
        color: var(--accent);
        border-color: var(--accent);
      }}
      .lt-ind input {{
        width: 1.05rem;
        height: 1.05rem;
      }}
      .lt-legend-item {{
        white-space: normal;
      }}
    }}
    @media (max-width: 420px) {{
      .lt-toolbar {{
        gap: 0.35rem 0.4rem;
      }}
      .live-btn {{
        padding: 0.32rem 0.42rem;
        font-size: 0.68rem;
      }}
    }}
    .outlook {{
      border-top: 1px solid var(--line);
      padding-top: 1rem;
      max-width: 40rem;
    }}
    .outlook h2 {{
      font-size: 0.75rem;
      letter-spacing: 0.1em;
      text-transform: uppercase;
      color: var(--muted);
      margin: 0 0 0.4rem;
      font-weight: 500;
    }}
    .outlook p {{ margin: 0; color: var(--fg); }}
    .heatmap {{
      border-top: 1px solid var(--line);
      margin-top: 1.5rem;
      padding-top: 1rem;
    }}
    .heatmap h2 {{
      font-size: 0.75rem;
      letter-spacing: 0.1em;
      text-transform: uppercase;
      color: var(--muted);
      margin: 0 0 0.35rem;
      font-weight: 500;
    }}
    .heatmap-note {{
      margin: 0 0 0.65rem;
      font-size: 0.82rem;
      color: var(--muted);
      max-width: 40rem;
    }}
    .month-heatmap-plot {{
      width: 100%;
      background: #12141a;
    }}
    footer {{
      margin-top: 1.75rem;
      font-size: 0.78rem;
      color: var(--muted);
      border-top: 1px solid var(--line);
      padding-top: 0.85rem;
    }}
    footer a {{ color: var(--accent); }}
  </style>
</head>
<body>
  <main class="page">
    <p class="brand">ccquant · Market Tracker</p>
    <h1>{title}</h1>
    <p class="support">{html.escape(snapshot.supporting)}</p>
    {live_html}
    <section class="metrics" aria-label="Key metrics">{metrics_html}</section>
    <section class="chips" aria-label="Regime stack">{chips_html}</section>
    <section class="chart" aria-label="BTC long-term market view">{chart_html}</section>
    <section class="outlook">
      <h2>Outlook</h2>
      <p>{html.escape(snapshot.outlook)}</p>
    </section>
    {heatmap_html}
    <footer>
      {html.escape(snapshot.freshness_note)} · Regime-conditional research only —
      not a prediction.
      Deep dive: <a href="../../notebooks/Market_Tracker.ipynb">Market_Tracker.ipynb</a>
      · Refresh: <code>uv run ccquant sync all</code>
      · Live tape polls Binance every 15s in-browser when allowed.
    </footer>
  </main>
  {lt_js}
  {live_js}
  {heatmap_js}
</body>
</html>
"""


def write_dashboard(
    database: str | Path,
    out: str | Path,
    *,
    live_interval: LiveInterval = "5m",
    live_range: LiveRange = "1h",
    include_live: bool = True,
) -> Path:
    """Build snapshot (+ optional live tape), write HTML, return output path."""
    snap = build_market_snapshot(database)
    live: LiveTape | None = None
    daily_tail: tuple[DailyFill, ...] | None = None
    if include_live:
        allowed = INTERVALS_FOR_RANGE[live_range]
        if live_interval not in allowed:
            live_interval = DEFAULT_INTERVAL_FOR_RANGE[live_range]
        try:
            live = fetch_live_tape(interval=live_interval, range_key=live_range)
        except Exception as exc:
            # Dashboard still useful offline / when exchanges are blocked.
            import logging

            logging.getLogger(__name__).warning("live tape unavailable: %s", exc)
        try:
            daily_tail = fetch_recent_daily_btc(days=45)
        except Exception as exc:
            import logging

            logging.getLogger(__name__).warning("daily tail fill unavailable: %s", exc)
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        render_dashboard_html(snap, live=live, daily_tail=daily_tail),
        encoding="utf-8",
    )
    return path.resolve()
