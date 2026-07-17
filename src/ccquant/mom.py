"""Month-over-month (MoM) helpers for daily time series and OHLCV panels.

Use these for DeFi aggregates (TVL / volume / fees), single-asset prices, and
cross-sectional market breadth without reimplementing calendar truncation in
every notebook.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Literal

import polars as pl

Agg = Literal["last", "sum", "mean"]
WindowHow = Literal["last", "sum", "mean"]


def monthly_mom(
    df: pl.DataFrame,
    *,
    value_col: str,
    date_col: str = "date",
    agg: Agg = "last",
    as_of: date | None = None,
    month_col: str = "month",
) -> pl.DataFrame:
    """Collapse a daily series to monthly values with MoM percent change.

    Parameters
    ----------
    df:
        Daily frame containing ``date_col`` and ``value_col``.
    value_col:
        Numeric column to aggregate.
    date_col:
        Date column (``pl.Date`` or datetime).
    agg:
        ``last`` = end-of-month level (prices, TVL), ``sum`` = month total
        (volume, fees), ``mean`` = month average.
    as_of:
        Calendar date used to flag the incomplete current month. Defaults to
        ``date.today()``.
    month_col:
        Name of the truncated month column in the result.

    Returns
    -------
    DataFrame with ``month_col``, ``value_col``, ``{value_col}_mom``, ``partial``.
    """
    if value_col not in df.columns:
        raise ValueError(f"missing value_col={value_col!r}")
    if date_col not in df.columns:
        raise ValueError(f"missing date_col={date_col!r}")

    as_of = as_of or date.today()
    agg_expr = {
        "last": pl.col(value_col).last().alias(value_col),
        "sum": pl.col(value_col).sum().alias(value_col),
        "mean": pl.col(value_col).mean().alias(value_col),
    }[agg]

    out = (
        df.sort(date_col)
        .with_columns(pl.col(date_col).cast(pl.Date).dt.truncate("1mo").alias(month_col))
        .group_by(month_col)
        .agg(agg_expr)
        .sort(month_col)
        .with_columns(pl.col(value_col).pct_change().alias(f"{value_col}_mom"))
    )
    return mark_partial_months(out, month_col=month_col, as_of=as_of)


def mark_partial_months(
    monthly: pl.DataFrame,
    *,
    month_col: str = "month",
    as_of: date | None = None,
) -> pl.DataFrame:
    """Add ``partial`` True when ``month`` is the still-open calendar month."""
    as_of = as_of or date.today()
    return monthly.with_columns(
        (pl.col(month_col).dt.month_end() > as_of).alias("partial")
    )


def full_months(
    monthly: pl.DataFrame,
    *,
    partial_col: str = "partial",
) -> pl.DataFrame:
    """Drop incomplete months when a ``partial`` flag is present."""
    if partial_col not in monthly.columns:
        return monthly
    return monthly.filter(~pl.col(partial_col))


def compare_trailing_windows(
    monthly: pl.DataFrame,
    *,
    value_col: str,
    window: int = 3,
    how: WindowHow = "last",
    partial_col: str = "partial",
) -> dict[str, float | None]:
    """Compare the last ``window`` full months to the prior ``window``.

    For ``how="last"`` (levels), compares end-of-window values.
    For ``how="sum"`` / ``mean``, aggregates each window then compares.
    """
    if window < 1:
        raise ValueError("window must be >= 1")
    frame = full_months(monthly, partial_col=partial_col).sort(
        "month" if "month" in monthly.columns else monthly.columns[0]
    )
    need = window * 2
    if frame.height < need:
        return {
            "prior": None,
            "recent": None,
            "change": None,
            "n_months": float(frame.height),
        }

    win = frame.tail(need)
    prior = win.head(window)
    recent = win.tail(window)

    def _as_float(value: object) -> float:
        if value is None:
            raise ValueError(f"empty reduction for {value_col!r}")
        return float(pl.Series([value]).cast(pl.Float64)[0])

    def _reduce(part: pl.DataFrame) -> float:
        series = part[value_col]
        if how == "last":
            return _as_float(series[-1])
        if how == "sum":
            return _as_float(series.sum())
        return _as_float(series.mean())

    prior_v = _reduce(prior)
    recent_v = _reduce(recent)
    change = None if prior_v == 0 else recent_v / prior_v - 1.0
    return {
        "prior": prior_v,
        "recent": recent_v,
        "change": change,
        "n_months": float(need),
    }


def ohlcv_price_mom(
    panel: pl.DataFrame,
    *,
    symbols: Sequence[str] | None = None,
    date_col: str = "date",
    symbol_col: str = "symbol",
    price_col: str = "close",
    as_of: date | None = None,
) -> pl.DataFrame:
    """End-of-month close and MoM return per symbol from a daily OHLCV panel."""
    required = {date_col, symbol_col, price_col}
    missing = required - set(panel.columns)
    if missing:
        raise ValueError(f"panel missing columns: {sorted(missing)}")

    frame = panel
    if symbols is not None:
        wanted = [s.upper() for s in symbols]
        frame = frame.filter(pl.col(symbol_col).str.to_uppercase().is_in(wanted))

    # Deduplicate date×symbol (prefer last source row).
    frame = (
        frame.sort(date_col)
        .unique(subset=[symbol_col, date_col], keep="last")
        .select([symbol_col, date_col, price_col])
    )

    as_of = as_of or date.today()
    out = (
        frame.sort([symbol_col, date_col])
        .with_columns(pl.col(date_col).cast(pl.Date).dt.truncate("1mo").alias("month"))
        .group_by(["month", symbol_col])
        .agg(pl.col(price_col).last().alias(price_col))
        .sort([symbol_col, "month"])
        .with_columns(pl.col(price_col).pct_change().over(symbol_col).alias(f"{price_col}_mom"))
    )
    return mark_partial_months(out, as_of=as_of)


def market_mom(
    panel: pl.DataFrame,
    *,
    date_col: str = "date",
    symbol_col: str = "symbol",
    price_col: str = "close",
    volume_col: str = "volume",
    as_of: date | None = None,
) -> pl.DataFrame:
    """Cross-sectional market MoM from a multi-symbol daily OHLCV panel.

    Returns one row per month with:

    - ``eq_weight_mom`` — mean of per-symbol EOM close MoM
    - ``vol_weight_mom`` — volume-weighted mean of per-symbol MoM (prior-month
      volume as weights when available)
    - ``volume`` / ``volume_mom`` — sum of monthly traded volume
    - ``n_symbols``, ``n_up``, ``n_down`` — breadth counts
    """
    price = ohlcv_price_mom(
        panel,
        date_col=date_col,
        symbol_col=symbol_col,
        price_col=price_col,
        as_of=as_of,
    )
    mom_col = f"{price_col}_mom"

    vol_monthly: pl.DataFrame | None = None
    if volume_col in panel.columns:
        vol_monthly = (
            panel.sort(date_col)
            .unique(subset=[symbol_col, date_col], keep="last")
            .with_columns(pl.col(date_col).cast(pl.Date).dt.truncate("1mo").alias("month"))
            .group_by(["month", symbol_col])
            .agg(pl.col(volume_col).sum().alias(volume_col))
            .sort([symbol_col, "month"])
            .with_columns(pl.col(volume_col).shift(1).over(symbol_col).alias("_w"))
        )
        price = price.join(vol_monthly, on=["month", symbol_col], how="left")

    breadth = price.group_by("month").agg(
        [
            pl.col(mom_col).mean().alias("eq_weight_mom"),
            pl.col(mom_col).count().alias("n_symbols"),
            (pl.col(mom_col) > 0).sum().alias("n_up"),
            (pl.col(mom_col) < 0).sum().alias("n_down"),
        ]
    )

    if vol_monthly is not None and "_w" in price.columns:
        weighted = (
            price.filter(pl.col("_w").is_not_null() & (pl.col("_w") > 0))
            .group_by("month")
            .agg(
                (
                    (pl.col(mom_col) * pl.col("_w")).sum() / pl.col("_w").sum()
                ).alias("vol_weight_mom"),
                pl.col(volume_col).sum().alias("volume"),
            )
            .with_columns(pl.col("volume").pct_change().alias("volume_mom"))
        )
        breadth = breadth.join(weighted, on="month", how="left")
    else:
        breadth = breadth.with_columns(
            [
                pl.lit(None).cast(pl.Float64).alias("vol_weight_mom"),
                pl.lit(None).cast(pl.Float64).alias("volume"),
                pl.lit(None).cast(pl.Float64).alias("volume_mom"),
            ]
        )

    return mark_partial_months(breadth.sort("month"), as_of=as_of or date.today())


def top_mom_months(
    monthly: pl.DataFrame,
    *,
    mom_col: str,
    n: int = 5,
    partial_col: str = "partial",
) -> pl.DataFrame:
    """Return the ``n`` strongest MoM months among full months."""
    frame = full_months(monthly, partial_col=partial_col)
    if mom_col not in frame.columns:
        raise ValueError(f"missing mom_col={mom_col!r}")
    return frame.sort(mom_col, descending=True).head(n)
