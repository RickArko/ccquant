"""Fetch BTC on-chain fundamentals (blockchain.info) and valuation (BID)."""

from __future__ import annotations

import logging
import os
import time
from datetime import UTC, date, datetime

import httpx
import numpy as np
import polars as pl

from ccquant.models import OnchainPoint

LOGGER = logging.getLogger(__name__)

BC_API = "https://api.blockchain.info/charts"
BID_API = "https://bitcoinisdata.com/api/get_data"
BID_START_BLOCK = 400_000

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

BID_COLUMN_MAP: dict[str, str] = {
    "total_mvrv": "mvrv",
    "total_realized_price": "realized_price",
    "total_nupl": "nupl",
}


def fetch_blockchain_chart(
    client: httpx.Client,
    chart: str,
    *,
    timespan: str = "all",
) -> list[tuple[date, float]]:
    """Fetch one blockchain.info chart as ``[(date, value), ...]``."""
    url = f"{BC_API}/{chart}"
    for _attempt in range(2):
        resp = client.get(url, params={"timespan": timespan, "format": "json"})
        if resp.status_code == 429:
            time.sleep(60)
            continue
        resp.raise_for_status()
        vals = resp.json()["values"]
        return [
            (datetime.fromtimestamp(int(v["x"]), tz=UTC).date(), float(v["y"]))
            for v in vals
            if v.get("y") is not None
        ]
    return []


def fetch_blockchain_info_points(
    client: httpx.Client,
    *,
    delay_seconds: float = 1.0,
) -> list[OnchainPoint]:
    """Pull all configured blockchain.info fundamentals."""
    points: list[OnchainPoint] = []
    for chart, metric in BLOCKCHAIN_METRICS.items():
        try:
            rows = fetch_blockchain_chart(client, chart)
        except httpx.HTTPError as exc:
            LOGGER.warning("blockchain.info %s failed: %s", chart, exc)
            time.sleep(delay_seconds)
            continue
        for d, value in rows:
            points.append(
                OnchainPoint(
                    metric=metric, date=d, value=value, source="blockchain.info"
                )
            )
        time.sleep(delay_seconds)
    return points


def fetch_bid_valuation_points(
    client: httpx.Client,
    *,
    api_key: str | None = None,
    start_block: int = BID_START_BLOCK,
) -> tuple[list[OnchainPoint], str]:
    """Fetch MVRV / NUPL / realized_price from bitcoinisdata.com.

    Returns ``(points, status)`` where status is ``ok``, ``missing_key``,
    ``expired``, or ``error:<msg>``.
    """
    if api_key is None:
        api_key = os.environ.get("BITCOIN_IS_DATA_KEY", "")
    key = api_key.strip()
    if not key:
        return [], "missing_key"

    columns = ",".join(["date", *BID_COLUMN_MAP.keys()])
    params = {
        "api_key": key,
        "start_block": str(start_block),
        "columns": columns,
        "format": "json",
    }
    try:
        resp = client.get(BID_API, params=params, timeout=120.0)
        resp.raise_for_status()
        text = resp.text.strip()
        if "EXPIRED" in text.upper() or text.startswith('"Hello'):
            return [], "expired"
        payload = resp.json()
    except Exception as exc:
        LOGGER.warning("bitcoinisdata fetch failed: %s", exc)
        return [], f"error:{exc}"

    if isinstance(payload, dict) and "data" in payload:
        rows = payload["data"]
    elif isinstance(payload, list):
        rows = payload
    else:
        return [], "error:unexpected_payload"

    if not rows:
        return [], "error:empty"

    df = pl.DataFrame(rows)
    if "date" not in df.columns:
        return [], "error:no_date"

    daily = df.group_by("date").last().sort("date")
    points: list[OnchainPoint] = []
    for bid_col, metric in BID_COLUMN_MAP.items():
        if bid_col not in daily.columns:
            continue
        for d, v in zip(daily["date"].to_list(), daily[bid_col].to_list(), strict=True):
            if v is None or (isinstance(v, float) and np.isnan(v)):
                continue
            try:
                day = date.fromisoformat(str(d)[:10])
            except ValueError:
                continue
            points.append(
                OnchainPoint(
                    metric=metric,
                    date=day,
                    value=float(v),
                    source="bitcoinisdata",
                )
            )
    return points, ("ok" if points else "error:empty_metrics")
