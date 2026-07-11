from __future__ import annotations

import json
import os
from typing import Any

import httpx

from ccquant.models import TweetAlert


async def send_telegram_alerts(
    alerts: list[TweetAlert],
    *,
    client: httpx.AsyncClient | None = None,
) -> int:
    """Optional Telegram notifier for TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id or not alerts:
        return 0

    owns_client = client is None
    http = client or httpx.AsyncClient()
    sent = 0
    try:
        for alert in alerts:
            text = (
                f"[{alert.severity}] {alert.alert_type}\n"
                f"@{alert.handle} {alert.symbols}\n"
                f"{alert.posted_at.isoformat()}"
            )
            response = await http.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text},
                timeout=10.0,
            )
            response.raise_for_status()
            sent += 1
    finally:
        if owns_client:
            await http.aclose()
    return sent


def alert_to_dict(alert: TweetAlert) -> dict[str, Any]:
    return {
        "tweet_id": alert.tweet_id,
        "handle": alert.handle,
        "alert_type": alert.alert_type,
        "severity": alert.severity,
        "symbols": alert.symbols,
        "posted_at": alert.posted_at.isoformat(),
        "metadata": json.loads(alert.metadata_json or "{}"),
    }
