"""Lightweight single-page Market Tracker dashboard (HTML + Plotly).

Condenses the notebook surface into one viewport: brand, headline, near-live
BTC tape, key metrics, daily market chart, regime strip, and outlook. No
HTTP server — write a self-contained HTML file via ``ccquant dashboard``.
The live tape seeds from Binance/Coinbase at render time; the browser can
poll Binance every 15s to keep the headline last price fresh.
"""

from __future__ import annotations

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
    LiveInterval,
    LiveRange,
    LiveTape,
    fetch_live_tape,
)

MOM_LOOKBACK = 12
LIQ_LOOKBACK = 52
# Default daily viewport (~2y). Full BTC history is still embedded so the
# rangeslider can zoom out to the entire series (esp. useful on monthly).
CHART_DEFAULT_VIEW_DAYS = 730
STALE_WARN_DAYS = 3
DASHBOARD_TZ = ZoneInfo("America/Chicago")
# Trading-desk presets exposed in the live tape toolbar (default: Chicago).
LIVE_TZ_PRESETS: tuple[tuple[str, str, str], ...] = (
    ("ny", "America/New_York", "NY"),
    ("utc", "UTC", "UTC"),
    ("ct", "America/Chicago", "CT"),
)
DEFAULT_LIVE_TZ = "ct"


def _to_tz(dt: datetime, tz: ZoneInfo) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=tz)
    return dt.astimezone(tz)


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

    drivers: list[str] = []
    if liq_sig != 0:
        drivers.append(f"{liq_label} liquidity")
    if oc_sig != 0:
        drivers.append(f"on-chain {oc_label}")
    drivers.append(f"{br_label} breadth")
    if demand_sig != 0 or (etf_7d is not None):
        drivers.append(f"demand {demand_label}")
    if ret_7d is not None:
        drivers.append(f"BTC 7d {ret_7d * 100:+.1f}%")

    if br_sig == 1:
        supporting = "Tape is broad across the research universe."
    elif br_sig == -1:
        supporting = "Tape is narrow — leadership is concentrated."
    else:
        supporting = "Breadth is balanced; wait for confirmation."

    chart_cols = ["date", "open", "high", "low", "close"]
    if "volume" in btc.columns:
        chart_cols.append("volume")
    chart = btc.select(chart_cols)
    if "volume" not in chart.columns:
        chart = chart.with_columns(pl.lit(0.0).alias("volume"))
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
        etf_flow_7d_m=etf_7d,
        mstr_rel_20d=mstr_rel,
        outlook=_outlook(stack_label, drivers),
        supporting=supporting,
        freshness_note=note,
        btc_dates=tuple(d for d in chart["date"].to_list() if isinstance(d, date)),
        btc_opens=tuple(_as_float(x) for x in chart["open"].to_list()),
        btc_highs=tuple(_as_float(x) for x in chart["high"].to_list()),
        btc_lows=tuple(_as_float(x) for x in chart["low"].to_list()),
        btc_closes=tuple(_as_float(x) for x in chart["close"].to_list()),
        btc_volumes=tuple(_as_float(x) for x in chart["volume"].to_list()),
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


def _long_term_indicator_seed(snapshot: MarketSnapshot) -> dict[str, object]:
    """Build JSON-serializable series for the daily long-term chart toggles."""
    dates = [d.isoformat() for d in snapshot.btc_dates]
    closes = list(snapshot.btc_closes)
    highs = list(snapshot.btc_highs)
    lows = list(snapshot.btc_lows)

    sma50 = _sma(closes, 50)
    sma200 = _sma(closes, 200)
    sma111 = _sma(closes, 111)
    sma350 = _sma(closes, 350)
    pi_double = [None if v is None else 2.0 * v for v in sma350]
    golden_x, golden_y, death_x, death_y = _cross_events(dates, sma50, sma200)
    pi_up_x, pi_up_y, _, _ = _cross_events(dates, sma111, pi_double)

    ema30 = _ema(closes, 30)
    ema60 = _ema(closes, 60)
    atr60 = _atr(highs, lows, closes, 60)
    larsson = _larsson_states(ema30, ema60, atr60)
    latest_state = next((s for s in reversed(larsson) if s is not None), None)

    view_start: str | None = None
    if snapshot.btc_dates:
        end = snapshot.btc_dates[-1]
        target = end - timedelta(days=CHART_DEFAULT_VIEW_DAYS)
        view_start = next(
            (d.isoformat() for d in snapshot.btc_dates if d >= target),
            dates[0],
        )

    return {
        "dates": dates,
        "close": closes,
        "view_start": view_start,
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
        "ema30": ema30,
        "ema60": ema60,
        "larsson_bull": _mask_by_state(ema30, larsson, "bull"),
        "larsson_bear": _mask_by_state(ema30, larsson, "bear"),
        "larsson_neutral": _mask_by_state(ema30, larsson, "neutral"),
        "larsson_state": latest_state,
    }


def render_dashboard_html(
    snapshot: MarketSnapshot,
    *,
    live: LiveTape | None = None,
) -> str:
    """Return a self-contained single-page HTML dashboard."""
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots
    except ImportError as exc:
        raise ImportError(
            "plotly is required for the dashboard. Install with: uv sync"
        ) from exc

    lt_seed = _long_term_indicator_seed(snapshot)
    lt_seed_json = json.dumps(lt_seed, separators=(",", ":"))

    m_dates, m_o, m_h, m_l, m_c, m_v = _monthly_ohlcv(
        snapshot.btc_dates,
        snapshot.btc_opens,
        snapshot.btc_highs,
        snapshot.btc_lows,
        snapshot.btc_closes,
        snapshot.btc_volumes,
    )
    vol_colors = [
        "#6fbf73" if c >= o else "#e57373" for o, c in zip(m_o, m_c, strict=True)
    ]
    monthly_fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.04,
        row_heights=[0.72, 0.28],
    )
    monthly_fig.add_trace(
        go.Candlestick(
            x=[d.isoformat() for d in m_dates],
            open=list(m_o),
            high=list(m_h),
            low=list(m_l),
            close=list(m_c),
            name="BTC",
            increasing_line_color="#6fbf73",
            increasing_fillcolor="#6fbf73",
            decreasing_line_color="#e57373",
            decreasing_fillcolor="#e57373",
        ),
        row=1,
        col=1,
    )
    monthly_fig.add_trace(
        go.Bar(
            x=[d.isoformat() for d in m_dates],
            y=list(m_v),
            name="Volume",
            marker_color=vol_colors,
            opacity=0.75,
        ),
        row=2,
        col=1,
    )
    monthly_fig.update_layout(
        template="plotly_dark",
        height=460,
        margin=dict(l=48, r=24, t=36, b=24),
        title=dict(text="BTC monthly — candles + volume", font=dict(size=14)),
        showlegend=False,
        paper_bgcolor="#12141a",
        plot_bgcolor="#12141a",
        # Candlestick defaults a top-axis slider; put it on the shared bottom
        # axis so volume stays aligned and the full history is scrubbable.
        xaxis_rangeslider_visible=False,
        xaxis2_rangeslider_visible=True,
        xaxis2_rangeslider_thickness=0.08,
    )
    monthly_fig.update_yaxes(title_text="USD", type="log", row=1, col=1)
    monthly_fig.update_yaxes(title_text="Vol", row=2, col=1)
    monthly_fig.update_xaxes(title_text="Month", row=2, col=1)
    # CDN here so the JS-built daily plot (and live tape) can use Plotly.
    monthly_chart = monthly_fig.to_html(
        full_html=False,
        include_plotlyjs="cdn",
        config={"displayModeBar": False, "responsive": True},
    )
    chart_html = f"""
      <div class="lt-toolbar">
        <div class="live-btn-group" id="lt-modes"
             aria-label="Long-term chart mode">
          <button type="button" class="live-btn active"
                  data-lt-mode="daily">Daily</button>
          <button type="button" class="live-btn"
                  data-lt-mode="monthly">Monthly</button>
        </div>
        <div class="lt-ind-group" id="lt-indicators"
             aria-label="Long-term indicators">
          <label class="lt-ind" title="50/200 SMA trend filter (golden/death cross)">
            <input type="checkbox" id="lt-ind-sma" /> 50/200 SMA
          </label>
          <label class="lt-ind" title="Pi Cycle Top: 111 DMA vs 2×350 DMA">
            <input type="checkbox" id="lt-ind-pi" /> Pi Cycle
          </label>
          <label class="lt-ind" title="Larsson-style EMA30/60 + 0.3×ATR(60)">
            <input type="checkbox" id="lt-ind-larsson" /> Larsson Line
          </label>
          <button type="button" class="live-btn lt-ind-clear" id="lt-ind-clear"
                  title="Turn off all indicator overlays">Clear</button>
        </div>
        <span class="live-chart-label" id="lt-ind-status"></span>
      </div>
      <div id="lt-daily" class="lt-pane">
        <div id="lt-daily-plot" class="lt-daily-plot"></div>
      </div>
      <div id="lt-monthly" class="lt-pane" hidden>{monthly_chart}</div>
      <script type="application/json" id="lt-seed">{lt_seed_json}</script>
"""
    lt_js = """
<script>
(function () {
  const modes = document.getElementById("lt-modes");
  const daily = document.getElementById("lt-daily");
  const monthly = document.getElementById("lt-monthly");
  const plotEl = document.getElementById("lt-daily-plot");
  const seedEl = document.getElementById("lt-seed");
  const statusEl = document.getElementById("lt-ind-status");
  const smaCb = document.getElementById("lt-ind-sma");
  const piCb = document.getElementById("lt-ind-pi");
  const larssonCb = document.getElementById("lt-ind-larsson");
  if (!modes || !daily || !monthly || !plotEl || !seedEl) return;
  if (typeof Plotly === "undefined") return;

  const seed = JSON.parse(seedEl.textContent);
  let dailyReady = false;
  let syncingDailyY = false;
  let syncingMonthlyY = false;
  let dailyRelayoutBound = false;

  function dateKey(v) {
    if (typeof v === "number" && Number.isFinite(v)) {
      return new Date(v).toISOString().slice(0, 10);
    }
    const m = String(v).match(/(\\d{4}-\\d{2}-\\d{2})/);
    return m ? m[1] : String(v);
  }

  function isXRelayout(ed) {
    if (!ed) return true;
    return Object.keys(ed).some(function (k) {
      return k.indexOf("xaxis") === 0;
    });
  }

  function resolveXRange(plot, ed) {
    ed = ed || {};
    if (ed["xaxis.autorange"] || ed["xaxis2.autorange"]) return null;
    const a0 = ed["xaxis.range[0]"] != null
      ? ed["xaxis.range[0]"]
      : ed["xaxis2.range[0]"];
    const a1 = ed["xaxis.range[1]"] != null
      ? ed["xaxis.range[1]"]
      : ed["xaxis2.range[1]"];
    if (a0 != null && a1 != null) return [a0, a1];
    if (ed["xaxis.range"]) return ed["xaxis.range"];
    if (ed["xaxis2.range"]) return ed["xaxis2.range"];
    if (ed["xaxis.rangeslider.range"]) return ed["xaxis.rangeslider.range"];
    if (ed["xaxis2.rangeslider.range"]) return ed["xaxis2.rangeslider.range"];
    const lay = plot.layout || {};
    const xa = lay.xaxis || {};
    const xa2 = lay.xaxis2 || {};
    if (xa.range && xa.range.length === 2) return xa.range;
    if (xa2.range && xa2.range.length === 2) return xa2.range;
    return null;
  }

  function padLogRange(lo, hi) {
    if (!(lo > 0) || !(hi > 0)) return null;
    if (hi < lo) { const tmp = lo; lo = hi; hi = tmp; }
    if (hi === lo) return [lo / 1.05, hi * 1.05];
    const pad = Math.pow(hi / lo, 0.08);
    return [lo / pad, hi * pad];
  }

  // Plotly log-axis range is in log10 units.
  function logAxisRange(lo, hi) {
    const padded = padLogRange(lo, hi);
    if (!padded) return null;
    return [Math.log10(padded[0]), Math.log10(padded[1])];
  }

  function visibleSeries() {
    const series = [seed.close];
    if (smaCb && smaCb.checked) {
      series.push(seed.sma50, seed.sma200);
    }
    if (piCb && piCb.checked) {
      series.push(seed.sma111, seed.pi350x2);
    }
    if (larssonCb && larssonCb.checked) {
      series.push(
        seed.ema60, seed.larsson_bull, seed.larsson_bear, seed.larsson_neutral
      );
    }
    return series;
  }

  function dailyYBounds(x0, x1) {
    const dates = seed.dates || [];
    if (!dates.length) return null;
    const d0 = x0 == null ? null : dateKey(x0);
    const d1 = x1 == null ? null : dateKey(x1);
    let lo = Infinity;
    let hi = -Infinity;
    const series = visibleSeries();
    for (let i = 0; i < dates.length; i++) {
      const d = dateKey(dates[i]);
      if (d0 != null && d < d0) continue;
      if (d1 != null && d > d1) continue;
      for (let s = 0; s < series.length; s++) {
        const v = series[s][i];
        if (typeof v === "number" && Number.isFinite(v) && v > 0) {
          if (v < lo) lo = v;
          if (v > hi) hi = v;
        }
      }
    }
    if (!(lo < Infinity && hi > -Infinity)) return null;
    return logAxisRange(lo, hi);
  }

  function defaultXWindow() {
    if (seed.view_start && seed.dates && seed.dates.length) {
      return [seed.view_start, seed.dates[seed.dates.length - 1]];
    }
    return null;
  }

  function layout() {
    const xaxis = {
      title: "Date",
      type: "date",
      rangeslider: { visible: true, thickness: 0.08 }
    };
    const yaxis = {
      title: "USD",
      type: "log",
      fixedrange: false,
      autorange: true
    };
    // Initial ~2y window + matching y-range so first paint is never blank.
    const xw = !dailyReady ? defaultXWindow() : null;
    if (xw) {
      xaxis.range = xw;
      const yb = dailyYBounds(xw[0], xw[1]);
      if (yb) {
        yaxis.autorange = false;
        yaxis.range = yb;
      }
    }
    return {
      template: "plotly_dark",
      height: 420,
      margin: { l: 48, r: 24, t: 36, b: 24 },
      title: { text: "BTC close (log) — market view", font: { size: 14 } },
      yaxis: yaxis,
      xaxis: xaxis,
      uirevision: "lt-daily",
      showlegend: true,
      legend: {
        orientation: "h", y: 1.12, x: 0, font: { size: 10, color: "#9a958c" }
      },
      paper_bgcolor: "#12141a",
      plot_bgcolor: "#12141a",
      font: { color: "#e8e6e1" }
    };
  }

  function syncDailyY(ed) {
    if (syncingDailyY || !isXRelayout(ed)) return;
    const xr = resolveXRange(plotEl, ed) || defaultXWindow();
    const bounds = xr ? dailyYBounds(xr[0], xr[1]) : dailyYBounds(null, null);
    if (!bounds) return;
    syncingDailyY = true;
    Plotly.relayout(plotEl, {
      "yaxis.type": "log",
      "yaxis.autorange": false,
      "yaxis.range": bounds
    }).then(
      function () { syncingDailyY = false; },
      function () { syncingDailyY = false; }
    );
  }

  function bindDailyRelayout() {
    if (dailyRelayoutBound || typeof plotEl.on !== "function") return;
    plotEl.on("plotly_relayout", syncDailyY);
    dailyRelayoutBound = true;
  }

  function buildTraces() {
    const showSma = !!(smaCb && smaCb.checked);
    const showPi = !!(piCb && piCb.checked);
    const showLarsson = !!(larssonCb && larssonCb.checked);
    const traces = [{
      type: "scatter",
      mode: "lines",
      name: "BTC",
      x: seed.dates,
      y: seed.close,
      line: { color: "#F7931A", width: 2 }
    }];
    if (showSma) {
      traces.push({
        type: "scatter", mode: "lines", name: "SMA 50",
        x: seed.dates, y: seed.sma50,
        line: { color: "#6fa8dc", width: 1.4 }
      });
      traces.push({
        type: "scatter", mode: "lines", name: "SMA 200",
        x: seed.dates, y: seed.sma200,
        line: { color: "#c27ba0", width: 1.4 }
      });
      if (seed.golden_x && seed.golden_x.length) {
        traces.push({
          type: "scatter", mode: "markers", name: "Golden cross",
          x: seed.golden_x, y: seed.golden_y,
          marker: { color: "#6fbf73", size: 9, symbol: "triangle-up" }
        });
      }
      if (seed.death_x && seed.death_x.length) {
        traces.push({
          type: "scatter", mode: "markers", name: "Death cross",
          x: seed.death_x, y: seed.death_y,
          marker: { color: "#e57373", size: 9, symbol: "triangle-down" }
        });
      }
    }
    if (showPi) {
      traces.push({
        type: "scatter", mode: "lines", name: "Pi 111 DMA",
        x: seed.dates, y: seed.sma111,
        line: { color: "#ffd666", width: 1.5 }
      });
      traces.push({
        type: "scatter", mode: "lines", name: "Pi 2×350 DMA",
        x: seed.dates, y: seed.pi350x2,
        line: { color: "#9b59b6", width: 1.5, dash: "dot" }
      });
      if (seed.pi_top_x && seed.pi_top_x.length) {
        traces.push({
          type: "scatter", mode: "markers", name: "Pi Cycle top",
          x: seed.pi_top_x, y: seed.pi_top_y,
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
        x: seed.dates, y: seed.ema60,
        line: { color: "#7f8c8d", width: 1.2, dash: "dash" }
      });
      traces.push({
        type: "scatter", mode: "lines", name: "Larsson bull",
        x: seed.dates, y: seed.larsson_bull, connectgaps: false,
        line: { color: "#d4af37", width: 2.6 }
      });
      traces.push({
        type: "scatter", mode: "lines", name: "Larsson bear",
        x: seed.dates, y: seed.larsson_bear, connectgaps: false,
        line: { color: "#5dade2", width: 2.6 }
      });
      traces.push({
        type: "scatter", mode: "lines", name: "Larsson wait",
        x: seed.dates, y: seed.larsson_neutral, connectgaps: false,
        line: { color: "#95a5a6", width: 2.2 }
      });
    }
    if (statusEl) {
      const bits = [];
      if (showSma) bits.push("50/200 SMA");
      if (showPi) bits.push("Pi Cycle");
      if (showLarsson && seed.larsson_state) {
        bits.push("Larsson: " + seed.larsson_state);
      } else if (showLarsson) {
        bits.push("Larsson Line");
      }
      statusEl.textContent = bits.length
        ? bits.join(" · ")
        : "long-term market view";
    }
    return traces;
  }

  function renderDaily() {
    const p = Plotly.react(plotEl, buildTraces(), layout(), {
      displayModeBar: false, responsive: true
    });
    Promise.resolve(p).then(function () {
      dailyReady = true;
      bindDailyRelayout();
      syncDailyY({});
    });
  }

  function syncMonthlyY(ed) {
    if (syncingMonthlyY || !isXRelayout(ed)) return;
    const mPlot = monthly.querySelector(".js-plotly-plot");
    if (!mPlot || !mPlot.data) return;
    const candle = mPlot.data.find(function (t) {
      return t.type === "candlestick";
    });
    const volume = mPlot.data.find(function (t) {
      return t.type === "bar";
    });
    if (!candle || !candle.x) return;
    const xr = resolveXRange(mPlot, ed);
    const d0 = xr ? dateKey(xr[0]) : null;
    const d1 = xr ? dateKey(xr[1]) : null;
    let lo = Infinity;
    let hi = -Infinity;
    let volHi = 0;
    for (let i = 0; i < candle.x.length; i++) {
      const d = dateKey(candle.x[i]);
      if (d0 != null && d < d0) continue;
      if (d1 != null && d > d1) continue;
      const vals = [candle.low[i], candle.high[i], candle.open[i], candle.close[i]];
      for (let j = 0; j < vals.length; j++) {
        const v = vals[j];
        if (typeof v === "number" && Number.isFinite(v) && v > 0) {
          if (v < lo) lo = v;
          if (v > hi) hi = v;
        }
      }
      if (volume && typeof volume.y[i] === "number" && volume.y[i] > volHi) {
        volHi = volume.y[i];
      }
    }
    const priceRange = logAxisRange(lo, hi);
    if (!priceRange) return;
    const update = {
      "yaxis.type": "log",
      "yaxis.autorange": false,
      "yaxis.range": priceRange
    };
    if (volHi > 0) {
      update["yaxis2.autorange"] = false;
      update["yaxis2.range"] = [0, volHi * 1.12];
    }
    syncingMonthlyY = true;
    Plotly.relayout(mPlot, update).then(
      function () { syncingMonthlyY = false; },
      function () { syncingMonthlyY = false; }
    );
  }

  [smaCb, piCb, larssonCb].forEach(function (el) {
    if (el) el.addEventListener("change", renderDaily);
  });

  const clearBtn = document.getElementById("lt-ind-clear");
  if (clearBtn) {
    clearBtn.addEventListener("click", function () {
      [smaCb, piCb, larssonCb].forEach(function (el) {
        if (el) el.checked = false;
      });
      renderDaily();
    });
  }

  // Monthly plot is already initialized by Plotly.newPlot in the embed.
  const monthlyPlot = monthly.querySelector(".js-plotly-plot");
  if (monthlyPlot && typeof monthlyPlot.on === "function") {
    monthlyPlot.on("plotly_relayout", syncMonthlyY);
  }

  modes.querySelectorAll(".live-btn").forEach(function (btn) {
    btn.addEventListener("click", function () {
      const mode = btn.getAttribute("data-lt-mode");
      modes.querySelectorAll(".live-btn").forEach(function (b) {
        b.classList.toggle("active", b === btn);
      });
      const showMonthly = mode === "monthly";
      daily.hidden = showMonthly;
      monthly.hidden = !showMonthly;
      const ind = document.getElementById("lt-indicators");
      if (ind) ind.style.opacity = showMonthly ? "0.35" : "1";
      if (typeof Plotly !== "undefined") {
        const pane = showMonthly ? monthly : daily;
        pane.querySelectorAll(".js-plotly-plot").forEach(function (el) {
          try { Plotly.Plots.resize(el); } catch (err) {}
        });
        if (showMonthly) syncMonthlyY({});
        else renderDaily();
      }
    });
  });

  // Bind daily relayout only after Plotly.react — .on does not exist on a bare div.
  renderDaily();
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
    }}
    .lt-toolbar {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 0.45rem 0.75rem;
      margin: 0 0 0.35rem;
    }}
    .lt-ind-group {{
      display: inline-flex;
      flex-wrap: wrap;
      gap: 0.55rem 0.85rem;
      margin-left: 0.25rem;
    }}
    .lt-ind {{
      display: inline-flex;
      align-items: center;
      gap: 0.3rem;
      font-size: 0.78rem;
      color: var(--muted);
      cursor: pointer;
      user-select: none;
    }}
    .lt-ind input {{
      accent-color: var(--accent);
      margin: 0;
    }}
    .lt-daily-plot {{
      width: 100%;
      min-height: 420px;
      background: #12141a;
    }}
    .lt-pane[hidden] {{ display: none !important; }}
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
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_dashboard_html(snap, live=live), encoding="utf-8")
    return path.resolve()
