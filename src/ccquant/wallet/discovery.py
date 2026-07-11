from __future__ import annotations

import json
import os
from datetime import UTC, datetime

import httpx

from ccquant.models import WalletRegistryEntry

FLIPSIDE_API_URL = "https://api-v2.flipsidecrypto.xyz/json-rpc"


def _flipside_headers() -> dict[str, str]:
    key = os.environ.get("FLIPSIDE_API_KEY", "")
    headers = {"Content-Type": "application/json"}
    if key:
        headers["x-api-key"] = key
    return headers


async def fetch_flipside_labels(
    client: httpx.AsyncClient,
    *,
    chain: str,
    limit: int = 20,
) -> list[WalletRegistryEntry]:
    """Fetch labeled addresses from Flipside (free tier when API key set)."""
    table = {
        "solana": "solana.core.dim_labels",
        "arbitrum": "arbitrum.core.dim_labels",
        "ethereum": "ethereum.core.dim_labels",
        "bitcoin": "bitcoin.core.dim_labels",
    }.get(chain)
    if table is None:
        return []

    sql = f"""
        select address, label_type, label_subtype, project_name, address_name
        from {table}
        where label_type in ('cex', 'dex', 'defi', 'bridge', 'miner', 'institution')
        limit {limit}
    """
    payload = {
        "jsonrpc": "2.0",
        "method": "createQueryRun",
        "params": [
            {
                "resultTTLHours": 1,
                "maxAgeMinutes": 60,
                "sql": sql,
                "tags": {"source": "ccquant", "chain": chain},
            }
        ],
        "id": 1,
    }
    try:
        create_resp = await client.post(
            FLIPSIDE_API_URL,
            headers=_flipside_headers(),
            json=payload,
            timeout=30.0,
        )
        create_resp.raise_for_status()
        create_data = create_resp.json()
        query_id = _extract_query_id(create_data)
        if query_id is None:
            return _fallback_labels(chain, limit)
        rows = await _poll_flipside_results(client, query_id)
        return _rows_to_registry(rows, chain)
    except (httpx.HTTPError, json.JSONDecodeError, KeyError):
        return _fallback_labels(chain, limit)


def _extract_query_id(data: dict[str, object]) -> str | None:
    result = data.get("result")
    if not isinstance(result, dict):
        return None
    query_id = result.get("queryRun", {})
    if isinstance(query_id, dict):
        raw = query_id.get("id")
        return str(raw) if raw is not None else None
    return None


async def _poll_flipside_results(
    client: httpx.AsyncClient,
    query_id: str,
    *,
    attempts: int = 5,
) -> list[dict[str, str]]:
    for _ in range(attempts):
        status_payload = {
            "jsonrpc": "2.0",
            "method": "getQueryRun",
            "params": [{"queryRunId": query_id}],
            "id": 1,
        }
        status_resp = await client.post(
            FLIPSIDE_API_URL,
            headers=_flipside_headers(),
            json=status_payload,
            timeout=30.0,
        )
        status_resp.raise_for_status()
        status_data = status_resp.json()
        state = _query_state(status_data)
        if state == "finished":
            return await _fetch_flipside_rows(client, query_id)
        if state in {"failed", "cancelled"}:
            break
    return []


def _query_state(data: dict[str, object]) -> str | None:
    result = data.get("result")
    if not isinstance(result, dict):
        return None
    query_run = result.get("queryRun", {})
    if isinstance(query_run, dict):
        state = query_run.get("state")
        return str(state) if state is not None else None
    return None


async def _fetch_flipside_rows(
    client: httpx.AsyncClient,
    query_id: str,
) -> list[dict[str, str]]:
    payload = {
        "jsonrpc": "2.0",
        "method": "getQueryRunResults",
        "params": [{"queryRunId": query_id}],
        "id": 1,
    }
    resp = await client.post(
        FLIPSIDE_API_URL,
        headers=_flipside_headers(),
        json=payload,
        timeout=30.0,
    )
    resp.raise_for_status()
    data = resp.json()
    result = data.get("result")
    if not isinstance(result, dict):
        return []
    rows = result.get("rows", [])
    if not isinstance(rows, list):
        return []
    parsed: list[dict[str, str]] = []
    for row in rows:
        if isinstance(row, dict):
            parsed.append({str(k): str(v) for k, v in row.items()})
    return parsed


def _rows_to_registry(
    rows: list[dict[str, str]],
    chain: str,
) -> list[WalletRegistryEntry]:
    now = datetime.now(tz=UTC)
    entries: list[WalletRegistryEntry] = []
    for row in rows:
        address = row.get("address") or row.get("ADDRESS") or ""
        if not address:
            continue
        label_type = row.get("label_type") or row.get("LABEL_TYPE") or "unknown"
        entity_type = _map_entity_type(label_type)
        project = row.get("project_name") or row.get("PROJECT_NAME") or ""
        address_name = row.get("address_name") or row.get("ADDRESS_NAME") or ""
        label = address_name or project or address[:12]
        entries.append(
            WalletRegistryEntry(
                address=address,
                chain=chain,
                label=label,
                entity_type=entity_type,
                confidence=0.85,
                source="flipside",
                discovered_at=now,
                active=True,
                metadata_json=json.dumps(
                    {
                        "label_type": label_type,
                        "label_subtype": row.get("label_subtype")
                        or row.get("LABEL_SUBTYPE"),
                        "project_name": project,
                    }
                ),
            )
        )
    return entries


def _map_entity_type(label_type: str) -> str:
    mapping = {
        "cex": "exchange",
        "dex": "dex",
        "defi": "smart_money",
        "bridge": "bridge",
        "nft": "whale",
        "dapp": "deployer",
        "miner": "insider",
        "institution": "insider",
    }
    return mapping.get(label_type.lower(), "smart_money")


def _fallback_labels(chain: str, limit: int) -> list[WalletRegistryEntry]:
    """Deterministic fallback when Flipside API is unavailable."""
    now = datetime.now(tz=UTC)
    templates = [
        ("exchange", "CEX Hot Wallet"),
        ("dex", "DEX Router"),
        ("deployer", "Contract Deployer"),
        ("smart_money", "Smart Money Candidate"),
    ]
    entries: list[WalletRegistryEntry] = []
    for idx in range(limit):
        entity_type, label_prefix = templates[idx % len(templates)]
        suffix = f"{idx:03d}"
        if chain == "solana":
            address = f"Seed{suffix}Wallet{chain[:3]}SeedWalletSeedWalletSeed"
        elif chain == "bitcoin":
            address = f"1Seed{suffix}BitcoinSeedWalletSeedWalletSeed"[:34]
        else:
            address = f"0xSeed{suffix}{chain[:3]}000000000000000000000"
        entries.append(
            WalletRegistryEntry(
                address=address[:44],
                chain=chain,
                label=f"{label_prefix} {suffix}",
                entity_type=entity_type,
                confidence=0.5,
                source="heuristic",
                discovered_at=now,
                active=True,
                metadata_json="{}",
            )
        )
    return entries


async def resolve_sns_domain(
    client: httpx.AsyncClient,
    domain: str,
) -> str | None:
    """Resolve a .sol SNS domain to a wallet address via Bonfida API."""
    name = domain if domain.endswith(".sol") else f"{domain}.sol"
    url = f"https://sns-api.bonfida.com/v2/domain/{name}"
    try:
        resp = await client.get(url, timeout=15.0)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict):
            owner = data.get("owner") or data.get("result", {}).get("owner")
            return str(owner) if owner else None
    except (httpx.HTTPError, json.JSONDecodeError, AttributeError):
        return None
    return None


def match_holder_amount(
    holders: list[tuple[str, float]],
    *,
    target_amount: float,
    tolerance: float = 0.001,
) -> str | None:
    """Match a holder list to an exact balance (screenshot trick)."""
    for address, amount in holders:
        if amount == 0:
            continue
        rel_diff = abs(amount - target_amount) / max(abs(target_amount), 1.0)
        if rel_diff <= tolerance:
            return address
    return None


def score_wallet_performance(
    *,
    win_rate: float,
    trade_count: int,
    median_hold_hours: float,
    min_win_rate: float,
) -> tuple[float, str]:
    """Heuristic smart-money scorer."""
    if trade_count < 10:
        return 0.3, "whale"
    if win_rate >= min_win_rate and median_hold_hours >= 2.0:
        confidence = min(0.95, 0.5 + win_rate * 0.5)
        return confidence, "smart_money"
    if win_rate >= 0.25:
        return 0.45, "kol"
    return 0.35, "whale"
