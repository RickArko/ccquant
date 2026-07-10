"""Render key notebook charts to documentation/images/notebooks/ as PNGs.

Runs the relevant pipeline cells from each notebook (without plotly .show())
and exports 4 representative charts that illustrate core principles.

Usage: uv run python scripts/render_chart_images.py
"""
from __future__ import annotations

import json
import os
import warnings
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import plotly.graph_objects as go
import polars as pl
import statsmodels.api as sm
from dotenv import load_dotenv
from statsmodels.tsa.arima.model import ARIMA

from ccquant.forecasting import load_daily_panel

warnings.filterwarnings("ignore")

# --- Setup ------------------------------------------------------------------
_root = Path("/home/ricka/Git/GitHub/ccquant")
load_dotenv(_root / ".env")

DB_PATH = os.environ.get("CCQUANT_DB", str(_root / "data" / "ccquant.duckdb"))
OUT_DIR = _root / "documentation" / "images" / "notebooks"
OUT_DIR.mkdir(parents=True, exist_ok=True)

WEEKLY_FREQ = "1w"


def save_fig(fig: go.Figure, name: str, width: int = 1200, height: int = 600) -> None:
    path = OUT_DIR / f"{name}.png"
    fig.write_image(str(path), width=width, height=height, scale=2)
    print(f"  wrote {path}  ({path.stat().st_size // 1024} KB)")


# --- Common: load BTC data --------------------------------------------------
panel = load_daily_panel(DB_PATH)
btc = panel.filter(pl.col("symbol") == "BTC").sort("date").unique(subset=["date"])
btc = btc.with_columns(np.log(pl.col("close")).alias("log_close"))

btc_weekly = (
    btc.with_columns(pl.col("date").dt.truncate(WEEKLY_FREQ).alias("week"))
    .group_by("week")
    .agg(
        pl.col("close").last().alias("close"),
        pl.col("volume").sum().alias("volume"),
    )
    .sort("week")
    .with_columns(
        np.log(pl.col("close")).alias("log_close"),
        (pl.col("close") / pl.col("close").shift(1) - 1.0).alias("wk_return"),
    )
)
btc_weekly = btc_weekly.with_columns(
    (pl.col("log_close") - pl.col("log_close").cum_max()).alias("log_drawdown")
)

print(f"BTC daily: {btc.height} rows  {btc['date'].min()} -> {btc['date'].max()}")
print(f"BTC weekly: {btc_weekly.height} rows")

HALVING_DATES = [
    date(2012, 11, 28), date(2016, 7, 9), date(2020, 5, 11), date(2024, 4, 20),
]
NEXT_HALVING_EST = date(2028, 4, 1)


# ===========================================================================
# Chart 1: BTC price (log) with halvings — from BTC.ipynb
# ===========================================================================
print("\n[1/4] BTC price with halvings ...")

def halving_cycle_progress(d: date) -> float:
    prev = max(h for h in HALVING_DATES if h <= d) if any(h <= d for h in HALVING_DATES) else HALVING_DATES[0]
    nxt = NEXT_HALVING_EST
    for i, h in enumerate(HALVING_DATES):
        if h == prev and i + 1 < len(HALVING_DATES):
            nxt = HALVING_DATES[i + 1]
    total = (nxt - prev).days
    elapsed = (d - prev).days
    return min(max(elapsed / total, 0.0), 1.0) if total > 0 else 0.0

btc_h = btc.with_columns(
    pl.col("date").map_elements(halving_cycle_progress, return_dtype=pl.Float64).alias("halving_progress")
)

fig1 = go.Figure()
fig1.add_trace(go.Scatter(
    x=btc_h["date"], y=btc_h["close"], mode="lines",
    name="BTC close", line=dict(color="#f7931a", width=1.5),
))
for h in HALVING_DATES:
    fig1.add_vline(
        x=h.isoformat(), line_dash="dash", line_color="cyan",
        annotation_text=f"halving {h.year}", annotation_textangle=-90,
    )
fig1.update_layout(
    yaxis_type="log", title="BTC price (log) with halving cycles",
    template="plotly_dark", height=450,
    yaxis_title="BTC price (USD, log)", xaxis_title="date",
    showlegend=False, margin=dict(t=50, b=40),
)
save_fig(fig1, "btc_price_halvings", width=1200, height=450)


# ===========================================================================
# Chart 2: BTC Long-Term Forecast fan — from BTC.ipynb
# ===========================================================================
print("\n[2/4] BTC long-term forecast fan ...")

def fetch_hashrate() -> pl.DataFrame:
    with httpx.Client(timeout=30.0) as client:
        resp = client.get(
            "https://api.blockchain.info/charts/hash-rate",
            params={"timespan": "all", "format": "json"},
        )
        resp.raise_for_status()
        data = resp.json()
    vals = data["values"]
    return pl.DataFrame({
        "date": [datetime.fromtimestamp(v["x"], tz=UTC).date() for v in vals],
        "hashrate": [float(v["y"]) for v in vals],
    }).sort("date")

def fetch_fred_series(series_id: str, api_key: str, start: str) -> pl.DataFrame:
    url = "https://api.stlouisfed.org/fred/series/observations"
    params = {"series_id": series_id, "api_key": api_key, "file_type": "json",
              "observation_start": start}
    with httpx.Client(timeout=30.0) as client:
        resp = client.get(url, params=params)
        resp.raise_for_status()
        obs = resp.json()["observations"]
    rows = []
    for o in obs:
        v = o["value"]
        if v != "." and v:
            rows.append({"date": date.fromisoformat(o["date"]), "value": float(v)})
    return pl.DataFrame(rows).sort("date").rename({"value": series_id})

def resample_to_daily(df: pl.DataFrame, col: str, btc_ref: pl.DataFrame) -> pl.DataFrame:
    dates = btc_ref.select("date").unique().sort("date")
    return dates.join_asof(df.sort("date"), on="date", strategy="backward").with_columns(
        pl.col(col).interpolate()
    )

FRED_API_KEY = os.environ.get("FRED_API_KEY", "")
HORIZONS = [365, 730, 1461]
RNG_SEED = 42

hashrate = fetch_hashrate()
hashrate = hashrate.with_columns(np.log(pl.col("hashrate") + 1e-12).alias("log_hashrate"))

fred_daily = btc.select("date").sort("date")
start_date_str = (btc["date"].min() - timedelta(days=30)).isoformat()

fred_frames: dict[str, pl.DataFrame | None] = {}
if FRED_API_KEY:
    for sid in ["M2SL", "WALCL", "DGS10"]:
        try:
            fred_frames[sid] = fetch_fred_series(sid, FRED_API_KEY, start_date_str)
        except Exception:
            fred_frames[sid] = None
else:
    fred_frames = {sid: None for sid in ["M2SL", "WALCL", "DGS10"]}

if fred_frames.get("M2SL") is not None:
    m2_daily = resample_to_daily(fred_frames["M2SL"], "M2SL", btc)
    fred_daily = fred_daily.join(m2_daily.select("date", "M2SL"), on="date", how="left")
    fred_daily = fred_daily.with_columns(np.log(pl.col("M2SL")).alias("log_m2"))
else:
    start_val = 13.5e12
    days = (fred_daily["date"] - fred_daily["date"].min()).dt.total_days()
    fred_daily = fred_daily.with_columns((np.log(start_val) + (days / 365.25) * 0.06).alias("log_m2"))

if fred_frames.get("WALCL") is not None:
    walcl_daily = resample_to_daily(fred_frames["WALCL"], "WALCL", btc)
    fred_daily = fred_daily.join(walcl_daily.select("date", "WALCL"), on="date", how="left")
    fred_daily = fred_daily.with_columns(np.log(pl.col("WALCL")).alias("log_fed_bs"))
else:
    fed_bs_start = 4.5e12
    days = (fred_daily["date"] - fred_daily["date"].min()).dt.total_days()
    fred_daily = fred_daily.with_columns((np.log(fed_bs_start) + (days / 365.25) * 0.04).alias("log_fed_bs"))

if fred_frames.get("DGS10") is not None:
    dgs_daily = resample_to_daily(fred_frames["DGS10"], "DGS10", btc)
    fred_daily = fred_daily.join(dgs_daily.select("date", "DGS10"), on="date", how="left")
    fred_daily = fred_daily.with_columns(pl.col("DGS10").alias("yield_10y"))
else:
    fred_daily = fred_daily.with_columns(pl.lit(2.5).cast(pl.Float64).alias("yield_10y"))

fred_daily = fred_daily.with_columns(
    pl.col("log_m2").forward_fill(),
    pl.col("log_fed_bs").forward_fill(),
    pl.col("yield_10y").forward_fill(),
)

model_df = (
    btc_h.select("date", "close", "log_close", "halving_progress")
    .sort("date")
    .join_asof(hashrate.select("date", "hashrate", "log_hashrate").sort("date"),
               on="date", strategy="backward")
    .join(fred_daily.select("date", "log_m2", "log_fed_bs", "yield_10y"), on="date", how="left")
    .sort("date")
)
model_df = model_df.with_columns(
    pl.col("log_hashrate").forward_fill(),
    pl.col("hashrate").forward_fill(),
    pl.col("log_m2").forward_fill(),
    pl.col("log_fed_bs").forward_fill(),
    pl.col("yield_10y").forward_fill(),
).drop_nulls(subset=["log_close", "log_hashrate", "log_m2"])
model_df = model_df.with_columns(
    ((pl.col("date") - pl.col("date").min()).dt.total_days()).alias("t")
)

FEATURES = ["t", "log_m2", "log_hashrate", "halving_progress", "log_fed_bs", "yield_10y"]
TARGET = "log_close"
y = model_df[TARGET].to_numpy()
X = model_df.select(FEATURES).to_numpy()
n_obs = X.shape[0]
X_with_const = np.column_stack([np.ones(n_obs), X])
ols_model = sm.OLS(y, X_with_const).fit(cov_type="HAC", cov_kwds={"maxlags": 30})

best_aic = np.inf
best_order = None
best_arima = None
for p in range(4):
    for q in range(4):
        try:
            m = ARIMA(y, order=(p, 1, q)).fit()
            if m.aic < best_aic:
                best_aic, best_order, best_arima = m.aic, (p, 1, q), m
        except Exception:
            pass

def nearest_psd(cov: np.ndarray) -> np.ndarray:
    cov = np.asarray(cov, dtype=float)
    cov = (cov + cov.T) / 2
    vals, vecs = np.linalg.eigh(cov)
    vals = np.clip(vals, 1e-12, None)
    return (vecs * vals) @ vecs.T

def recent_slope(series: np.ndarray, lookback: int = 730) -> float:
    k = min(lookback, len(series))
    x = np.arange(k)
    return float(np.polyfit(x, series[-k:], 1)[0])

n = model_df.height
Hmax = max(HORIZONS)
future_t = np.arange(n, n + Hmax)
drifts = {f: recent_slope(model_df[f].to_numpy()) for f in
          ["log_m2", "log_hashrate", "log_fed_bs", "yield_10y", "halving_progress"]}

fut_halving = []
last_progress = float(model_df["halving_progress"][-1])
for i in range(1, Hmax + 1):
    p = (last_progress + i / (4 * 365.25)) % 1.0
    fut_halving.append(p)

fut_logm2  = float(model_df["log_m2"][-1])    + drifts["log_m2"]    * np.arange(1, Hmax + 1)
fut_loghr  = float(model_df["log_hashrate"][-1]) + drifts["log_hashrate"] * np.arange(1, Hmax + 1)
fut_fedbs  = float(model_df["log_fed_bs"][-1]) + drifts["log_fed_bs"]  * np.arange(1, Hmax + 1)
fut_yield  = float(model_df["yield_10y"][-1])   + drifts["yield_10y"]   * np.arange(1, Hmax + 1)

X_future = np.column_stack([
    np.ones(Hmax), future_t, fut_logm2, fut_loghr, fut_halving, fut_fedbs, fut_yield,
])

cov_psd = nearest_psd(ols_model.cov_params())
coef = ols_model.params
resid = ols_model.resid
BOOTSTRAP_DRAWS = 500

rng = np.random.default_rng(RNG_SEED)
paths = np.zeros((BOOTSTRAP_DRAWS, Hmax))
for b in range(BOOTSTRAP_DRAWS):
    beta_draw = rng.multivariate_normal(coef, cov_psd)
    eps = rng.choice(resid, size=Hmax, replace=True)
    paths[b] = np.clip(X_future @ beta_draw + eps, -25, 30)

price_now = float(model_df["close"][-1])
date_now = model_df["date"][-1]
future_dates = [date_now + timedelta(days=i + 1) for i in range(Hmax)]
arima_mean = best_arima.get_forecast(steps=Hmax).predicted_mean

n_plot = min(200, BOOTSTRAP_DRAWS)
plot_paths = paths[:n_plot]

# Subsample future dates for rendering (every 7th day) to keep the SVG small
# enough for kaleido — full resolution is in the interactive notebook.
_step = 7
_plot_future = future_dates[::_step]
_plot_paths = plot_paths[:, ::_step]
arima_plot = arima_mean[::_step]

fig2 = go.Figure()
# Subsample history to monthly for the static image
_hist_step = 7
hist_dates = model_df["date"].to_list()[::_hist_step]
hist_close = model_df["close"].to_list()[::_hist_step]
fig2.add_trace(go.Scatter(
    x=hist_dates, y=hist_close, mode="lines", name="historical",
    line=dict(color="#f7931a", width=2),
))
band_edges = [(2.5, 97.5), (10, 90), (25, 75)]
colors = ["rgba(100,100,120,0.12)", "rgba(100,100,120,0.25)", "rgba(100,100,120,0.5)"]
for (lo, hi), color in zip(band_edges, colors, strict=True):
    lo_vals = np.exp(np.percentile(_plot_paths, lo, axis=0))
    hi_vals = np.exp(np.percentile(_plot_paths, hi, axis=0))
    fig2.add_trace(go.Scatter(
        x=_plot_future + _plot_future[::-1],
        y=list(hi_vals) + list(lo_vals[::-1]),
        fill="toself", fillcolor=color, line=dict(color="rgba(0,0,0,0)"),
        name=f"{hi-lo:.0f}% band", hoverinfo="skip",
    ))
fig2.add_trace(go.Scatter(
    x=_plot_future, y=np.exp(arima_plot), mode="lines", name="ARIMA median",
    line=dict(color="cyan", width=1.5, dash="dash"),
))
fig2.update_layout(
    yaxis_type="log",
    title="BTC Long-Term Forecast - OLS Bootstrap + ARIMA",
    xaxis_title="date", yaxis_title="BTC price (USD, log scale)",
    height=600, template="plotly_dark",
    legend=dict(orientation="h", y=1.08),
    margin=dict(t=50, b=40),
)
save_fig(fig2, "btc_forecast_fan", width=1200, height=600)


# ===========================================================================
# Chart 3: Global Liquidity Composite vs BTC — from Macro.ipynb
# ===========================================================================
print("\n[3/4] Global liquidity composite ...")

LIQ_LOOKBACK = 52
MOM_LOOKBACK = 12
LEAD_MIN, LEAD_MAX = -26, 26

def z_expr(col: str) -> pl.Expr:
    return (pl.col(col) - pl.col(col).mean()) / pl.col(col).std()

def fetch_fred(sid: str, start: str) -> pl.DataFrame:
    try:
        return fetch_fred_series(sid, FRED_API_KEY, start)
    except Exception:
        return pl.DataFrame()

FRED_SERIES: dict[str, dict[str, Any]] = {
    "M2SL":     {"label": "M2 money stock", "freq": "monthly"},
    "WALCL":    {"label": "Fed total assets", "freq": "weekly"},
    "DGS10":    {"label": "10Y Treasury yield", "freq": "daily"},
    "DGS2":     {"label": "2Y Treasury yield", "freq": "daily"},
    "T10YIE":   {"label": "10Y breakeven inflation", "freq": "daily"},
    "FEDFUNDS": {"label": "Effective Fed funds rate", "freq": "monthly"},
    "DTWEXBGS": {"label": "Trade-weighted USD (broad)", "freq": "daily"},
    "VIXCLS":   {"label": "VIX", "freq": "daily"},
}

def synthetic_macro(dates: pl.Series, sid: str) -> pl.DataFrame:
    rng = np.random.default_rng(abs(hash(sid)) % (2**32))
    n = dates.len()
    t = np.arange(n)
    base = {"M2SL": 13.0e12, "WALCL": 7.5e12, "DGS10": 2.5, "DGS2": 2.5,
            "T10YIE": 2.2, "FEDFUNDS": 2.0, "DTWEXBGS": 110.0, "VIXCLS": 18.0}[sid]
    drift = {"M2SL": 0.06 / 52, "WALCL": 0.04 / 52}.get(sid, 0.0)
    cyc = 0.10 * np.sin(2 * np.pi * t / 260.0)
    noise = rng.normal(0, 0.02, n)
    if sid in {"M2SL", "WALCL"}:
        val = base * np.exp((drift + cyc * 0.05 + noise * 0.01) * t)
    elif sid in {"DGS10", "DGS2", "T10YIE", "FEDFUNDS"}:
        val = base + 1.5 * cyc + np.cumsum(noise) * 0.4
        val = np.clip(val, 0.05, None)
    elif sid == "VIXCLS":
        val = np.clip(base * np.exp(0.3 * cyc + np.cumsum(noise) * 0.5), 9.0, None)
    else:
        val = base + 5.0 * cyc + np.cumsum(noise) * 0.5
    return pl.DataFrame({"date": dates.to_list(), sid: val}).sort("date")

week_spine = btc_weekly.select(pl.col("week").alias("date")).sort("date")
macro_weekly = week_spine
fred_start = (btc["date"].min() - timedelta(days=60)).isoformat()
for sid in FRED_SERIES:
    df = None
    if FRED_API_KEY:
        try:
            df = fetch_fred_series(sid, FRED_API_KEY, fred_start)
        except Exception:
            df = None
    if df is None or df.is_empty():
        df = synthetic_macro(week_spine["date"], sid)
    macro_weekly = macro_weekly.join_asof(df.sort("date"), on="date", strategy="backward")
macro_weekly = macro_weekly.with_columns(*[pl.col(sid).forward_fill() for sid in FRED_SERIES])

macro = macro_weekly.with_columns(
    (pl.col("DGS10") - pl.col("T10YIE")).alias("real_10y"),
    (pl.col("DGS10") - pl.col("DGS2")).alias("curve_10y_2y"),
).with_columns(
    (np.log(pl.col("M2SL")) - np.log(pl.col("M2SL")).shift(LIQ_LOOKBACK)).alias("m2_grow_yoy"),
    (np.log(pl.col("WALCL")) - np.log(pl.col("WALCL")).shift(LIQ_LOOKBACK)).alias("fedbs_grow_yoy"),
    (pl.col("real_10y") - pl.col("real_10y").shift(LIQ_LOOKBACK)).alias("real_rate_delta"),
)
macro = macro.join(
    btc_weekly.select("week", "close", "log_close", "wk_return", "log_drawdown"),
    left_on="date", right_on="week", how="left",
)
macro = macro.drop_nulls(subset=["close", "real_10y", "m2_grow_yoy"])

macro = macro.with_columns(
    (z_expr("m2_grow_yoy") + z_expr("fedbs_grow_yoy") - z_expr("real_rate_delta")).alias("liq_raw")
)
mu, sd = float(macro["liq_raw"].mean()), float(macro["liq_raw"].std())
macro = macro.with_columns(
    ((pl.col("liq_raw") - mu) / (sd if sd > 1e-12 else 1.0)).alias("liq_index"),
)

fig3 = go.Figure()
fig3.add_trace(go.Scatter(
    x=macro["date"], y=macro["liq_index"], name="Liquidity index",
    line=dict(color="#6ea8ff", width=1.8), yaxis="y",
))
fig3.add_trace(go.Scatter(
    x=macro["date"], y=np.exp(macro["log_close"]), name="BTC",
    line=dict(color="#f7931a", width=1.5), yaxis="y2",
))
fig3.update_layout(
    title="Global Liquidity Composite vs BTC (weekly)",
    template="plotly_dark", height=450,
    yaxis=dict(title="liquidity z"),
    yaxis2=dict(title="BTC $", overlaying="y", side="right", type="log"),
    legend=dict(orientation="h", y=1.08),
    margin=dict(t=50, b=40),
)
save_fig(fig3, "macro_liquidity_vs_btc", width=1200, height=450)


# ===========================================================================
# Chart 4: On-chain cycle-valuation composite vs BTC — from OnChain_BTC.ipynb
# ===========================================================================
print("\n[4/4] On-chain cycle-valuation composite ...")

# blockchain.info keyless series
BLOCKCHAIN_METRICS: dict[str, str] = {
    "hash-rate": "hashrate",
    "difficulty": "difficulty",
    "miners-revenue": "miner_revenue_usd",
    "transaction-fees-usd": "fees_usd",
    "n-unique-addresses": "active_addresses",
    "n-transactions": "tx_count",
    "estimated-transaction-volume-usd": "transfer_volume_usd",
    "market-cap": "market_cap",
    "total-bitcoins": "supply",
    "cost-per-transaction-percent": "cost_per_tx_pct",
}
BC_API = "https://api.blockchain.info/charts"
import time as _time

def fetch_bc(chart: str) -> list[tuple[date, float]]:
    with httpx.Client(timeout=30.0) as client:
        resp = client.get(f"{BC_API}/{chart}", params={"timespan": "all", "format": "json"})
        resp.raise_for_status()
        vals = resp.json()["values"]
    return [(datetime.fromtimestamp(int(v["x"]), tz=UTC).date(), float(v["y"]))
            for v in vals if v.get("y") is not None]

bc_data: dict[str, pl.DataFrame] = {}
for chart_id, metric in BLOCKCHAIN_METRICS.items():
    try:
        rows = fetch_bc(chart_id)
        bc_data[metric] = pl.DataFrame({"date": [r[0] for r in rows], "value": [r[1] for r in rows]}).sort("date")
        _time.sleep(1.0)
    except Exception as exc:
        print(f"  {metric}: FAILED ({exc})")
        bc_data[metric] = pl.DataFrame()

bc_weekly = week_spine
for metric in BLOCKCHAIN_METRICS.values():
    df = bc_data.get(metric, pl.DataFrame())
    if df.is_empty():
        df = pl.DataFrame({"date": week_spine["date"].to_list(), "value": [np.nan] * week_spine.height})
    df = df.rename({"value": metric})
    bc_weekly = bc_weekly.join_asof(df, on="date", strategy="backward")
bc_weekly = bc_weekly.with_columns(*[pl.col(m).forward_fill() for m in BLOCKCHAIN_METRICS.values()])

# Synthetic Glassnode valuation (no key)
GLASSNODE_SYNTH: dict[str, dict[str, float]] = {
    "mvrv":             {"base": 1.6,  "scale": 1.1},
    "sopr":             {"base": 1.0,  "scale": 0.12},
    "nupl":             {"base": 0.0,  "scale": 0.45},
    "rhodl":            {"base": 1500, "scale": 4000},
    "exchange_balance": {"base": 2.5e6,"scale": 0.4e6},
}

def synth_val(dates: pl.Series, metric: str, btc_lc: pl.Series) -> pl.DataFrame:
    meta = GLASSNODE_SYNTH[metric]
    base, scale = meta["base"], meta["scale"]
    rng = np.random.default_rng(abs(hash(metric)) % (2**32))
    n = dates.len()
    t = np.arange(n)
    lc = btc_lc.to_numpy()
    lc_norm = (lc - np.nanmean(lc)) / (np.nanstd(lc) + 1e-12)
    cyc = 0.85 * np.sin(2 * np.pi * t / 208.0)
    noise = rng.normal(0, 0.04, n)
    if metric == "mvrv":
        val = np.clip(base + scale * (0.6 * lc_norm + 0.4 * cyc) + noise * scale * 0.15, 0.6, 7.0)
    elif metric == "sopr":
        val = np.clip(base + scale * (0.7 * lc_norm + 0.3 * cyc) + noise * scale * 0.3, 0.7, 1.25)
    elif metric == "nupl":
        val = np.clip(base + scale * (0.65 * lc_norm + 0.35 * cyc) + noise * scale * 0.2, -0.2, 0.8)
    elif metric == "rhodl":
        val = np.clip(base + scale * (0.5 * lc_norm + 0.5 * cyc) + noise * scale * 0.1, 50.0, None)
    elif metric == "exchange_balance":
        val = np.clip(base + scale * (0.3 - 0.5 * lc_norm + 0.2 * cyc) + np.cumsum(noise) * scale * 0.05, 1.0e6, None)
    else:
        val = base + scale * lc_norm
    return pl.DataFrame({"date": dates.to_list(), metric: val}).sort("date")

gn_weekly = week_spine
for metric in GLASSNODE_SYNTH:
    df = synth_val(week_spine["date"], metric, btc_weekly["log_close"])
    gn_weekly = gn_weekly.join_asof(df, on="date", strategy="backward")
gn_weekly = gn_weekly.with_columns(*[pl.col(m).forward_fill() for m in GLASSNODE_SYNTH])

oc = week_spine
for frame in (bc_weekly, gn_weekly):
    oc = oc.join(frame, on="date", how="left", suffix="_dup")
oc = oc.drop([c for c in oc.columns if c.endswith("_dup")])
oc = oc.with_columns(
    (pl.col("miner_revenue_usd") / (pl.col("hashrate") + 1e-9)).alias("hashprice"),
    (pl.col("fees_usd") / (pl.col("miner_revenue_usd") + 1e-9)).alias("fee_rev_share"),
    (pl.col("transfer_volume_usd") / (pl.col("market_cap") + 1e-9)).alias("transfer_velocity"),
).with_columns(
    (pl.col("miner_revenue_usd") / pl.col("miner_revenue_usd").rolling_mean(365 // 7)).alias("puell_multiple"),
    (np.log(pl.col("active_addresses")) - np.log(pl.col("active_addresses")).shift(LIQ_LOOKBACK)).alias("active_addr_yoy"),
)
oc = oc.join(
    btc_weekly.select("week", "close", "log_close", "wk_return", "log_drawdown"),
    left_on="date", right_on="week", how="left",
)
oc = oc.drop_nulls(subset=["close", "hashprice", "puell_multiple", "mvrv"])

oc = oc.with_columns(
    (z_expr("mvrv") + z_expr("sopr") + z_expr("nupl") + z_expr("rhodl") - z_expr("puell_multiple")).alias("cycle_raw")
)
mu, sd = float(oc["cycle_raw"].mean()), float(oc["cycle_raw"].std())
oc = oc.with_columns(
    ((pl.col("cycle_raw") - mu) / (sd if sd > 1e-12 else 1.0)).alias("cycle_index"),
)

fig4 = go.Figure()
fig4.add_trace(go.Scatter(
    x=oc["date"], y=oc["cycle_index"], name="cycle composite",
    line=dict(color="#39ff14", width=1.8), yaxis="y",
))
fig4.add_trace(go.Scatter(
    x=oc["date"], y=np.exp(oc["log_close"]), name="BTC",
    line=dict(color="#f7931a", width=1.5), yaxis="y2",
))
fig4.update_layout(
    title="On-chain Cycle-Valuation Composite vs BTC (weekly)",
    template="plotly_dark", height=450,
    yaxis=dict(title="cycle z"),
    yaxis2=dict(title="BTC $", overlaying="y", side="right", type="log"),
    legend=dict(orientation="h", y=1.08),
    margin=dict(t=50, b=40),
)
save_fig(fig4, "onchain_cycle_vs_btc", width=1200, height=450)

print(f"\nAll charts saved to {OUT_DIR}/")
