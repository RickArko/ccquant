"""Lightweight single-page Market Tracker dashboard (HTML + Plotly).

Condenses the notebook surface into one viewport: brand, headline, key
metrics, one chart, regime strip, and outlook. No HTTP server — write a
self-contained HTML file via ``ccquant dashboard``.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Literal

import duckdb
import numpy as np
import polars as pl

from ccquant.forecasting import load_daily_panel, load_signals_panel

MOM_LOOKBACK = 12
LIQ_LOOKBACK = 52
CHART_LOOKBACK_DAYS = 730
STALE_WARN_DAYS = 3

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
    outlook: str
    supporting: str
    freshness_note: str
    btc_dates: tuple[date, ...]
    btc_closes: tuple[float, ...]


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
    if onchain.is_empty():
        return 0, "MISSING"
    oc = onchain.with_columns(pl.col("date").cast(pl.Date)).sort("date")
    candidates = [
        c
        for c in ("mvrv", "nupl", "active_addresses", "hashrate", "fees_usd")
        if c in oc.columns and oc[c].drop_nulls().len() >= 5
    ]
    if not candidates:
        return 0, "MISSING"
    oc = oc.drop_nulls(subset=candidates)
    varying: list[str] = []
    for c in candidates:
        std_v = oc[c].std()
        if std_v is not None and _as_float(std_v) > 1e-12:
            varying.append(c)
    if not varying:
        return 0, "MISSING"
    mom_lb = min(MOM_LOOKBACK, max(3, oc.height // 3))
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
        (pl.col("cycle_index") - pl.col("cycle_index").shift(mom_lb)).alias(
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
    return (1 if bullish else -1), ("bullish mom" if bullish else "bearish mom")


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

    drivers: list[str] = []
    if liq_sig != 0:
        drivers.append(f"{liq_label} liquidity")
    if oc_sig != 0:
        drivers.append(f"on-chain {oc_label}")
    drivers.append(f"{br_label} breadth")
    if ret_7d is not None:
        drivers.append(f"BTC 7d {ret_7d * 100:+.1f}%")

    if br_sig == 1:
        supporting = "Tape is broad across the research universe."
    elif br_sig == -1:
        supporting = "Tape is narrow — leadership is concentrated."
    else:
        supporting = "Breadth is balanced; wait for confirmation."

    chart_from = as_of - timedelta(days=CHART_LOOKBACK_DAYS)
    chart = btc.filter(pl.col("date") >= chart_from)
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
        outlook=_outlook(stack_label, drivers),
        supporting=supporting,
        freshness_note=note,
        btc_dates=tuple(d for d in chart["date"].to_list() if isinstance(d, date)),
        btc_closes=tuple(_as_float(x) for x in chart["close"].to_list()),
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
        daily, macro=macro, onchain=onchain, freshness_note=freshness
    )


def _fmt_pct(x: float | None) -> str:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "—"
    return f"{100 * x:+.1f}%"


def _fmt_share(x: float | None) -> str:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "—"
    return f"{100 * x:.0f}%"


def render_dashboard_html(snapshot: MarketSnapshot) -> str:
    """Return a self-contained single-page HTML dashboard."""
    try:
        import plotly.graph_objects as go
    except ImportError as exc:
        raise ImportError(
            "plotly is required for the dashboard. Install with: uv sync"
        ) from exc

    fig = go.Figure(
        data=[
            go.Scatter(
                x=list(snapshot.btc_dates),
                y=list(snapshot.btc_closes),
                name="BTC",
                line=dict(color="#F7931A", width=2),
            )
        ]
    )
    fig.update_layout(
        template="plotly_dark",
        height=320,
        margin=dict(l=48, r=24, t=36, b=36),
        title=dict(text="BTC close (log)", font=dict(size=14)),
        yaxis=dict(title="USD", type="log"),
        xaxis=dict(title="Date"),
        showlegend=False,
        paper_bgcolor="#12141a",
        plot_bgcolor="#12141a",
    )
    chart_html = fig.to_html(
        full_html=False,
        include_plotlyjs="cdn",
        config={"displayModeBar": False, "responsive": True},
    )

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
    metrics = [
        ("BTC", btc_px),
        ("1d", _fmt_pct(snapshot.ret_1d)),
        ("7d", _fmt_pct(snapshot.ret_7d)),
        ("30d", _fmt_pct(snapshot.ret_30d)),
        ("YTD", _fmt_pct(snapshot.ret_ytd)),
        (
            "Universe up 7d",
            f"{_fmt_share(snapshot.pct_up_7d)} · {snapshot.n_universe}",
        ),
        ("Above 50d MA", _fmt_share(snapshot.pct_above_50)),
        ("As of", snapshot.as_of.isoformat()),
    ]
    metrics_html = "".join(
        f'<div class="metric"><span>{html.escape(k)}</span>'
        f"<strong>{html.escape(v)}</strong></div>"
        for k, v in metrics
    )
    chips_html = "".join(
        [
            chip("Liquidity", snapshot.liq_label, tone_for(snapshot.liq_signal)),
            chip("On-chain", snapshot.oc_label, tone_for(snapshot.oc_signal)),
            chip("Breadth", snapshot.breadth_label, tone_for(snapshot.breadth_signal)),
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
    <section class="metrics" aria-label="Key metrics">{metrics_html}</section>
    <section class="chips" aria-label="Regime stack">{chips_html}</section>
    <section class="chart" aria-label="BTC price">{chart_html}</section>
    <section class="outlook">
      <h2>Outlook</h2>
      <p>{html.escape(snapshot.outlook)}</p>
    </section>
    <footer>
      {html.escape(snapshot.freshness_note)} · Regime-conditional research only —
      not a prediction.
      Deep dive: <a href="../notebooks/Market_Tracker.ipynb">Market_Tracker.ipynb</a>
      · Refresh: <code>uv run ccquant sync all</code>
    </footer>
  </main>
</body>
</html>
"""


def write_dashboard(
    database: str | Path,
    out: str | Path,
) -> Path:
    """Build snapshot, write HTML, return output path."""
    snap = build_market_snapshot(database)
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_dashboard_html(snap), encoding="utf-8")
    return path.resolve()
