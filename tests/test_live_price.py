"""Tests for near-live BTC tape helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import httpx
import pytest

from ccquant.live_price import (
    DEFAULT_INTERVAL_FOR_RANGE,
    INTERVALS_FOR_RANGE,
    LiveTape,
    fetch_live_tape,
    kline_limit,
)


class _Resp:
    def __init__(self, payload: object, status: int = 200) -> None:
        self._payload = payload
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "err", request=MagicMock(), response=MagicMock()
            )

    def json(self) -> object:
        return self._payload


def test_kline_limit_defaults() -> None:
    assert kline_limit("1h", "5m") == 12
    assert kline_limit("1d", "4h") == 6
    assert kline_limit("7d", "4h") == 42
    assert kline_limit("7d", "1d") == 7


def test_intervals_for_range() -> None:
    assert INTERVALS_FOR_RANGE["1h"] == ("1m", "5m", "15m", "1h")
    assert INTERVALS_FOR_RANGE["1d"] == ("1h", "4h")
    assert INTERVALS_FOR_RANGE["7d"] == ("4h", "1d")
    assert DEFAULT_INTERVAL_FOR_RANGE["1d"] == "4h"
    assert DEFAULT_INTERVAL_FOR_RANGE["7d"] == "4h"


def test_fetch_live_tape_binance(monkeypatch: pytest.MonkeyPatch) -> None:
    now_ms = int(datetime(2026, 7, 19, 12, 0, tzinfo=UTC).timestamp() * 1000)
    ticker = {
        "lastPrice": "65000.12",
        "priceChangePercent": "1.5",
        "highPrice": "66000",
        "lowPrice": "64000",
        "closeTime": now_ms,
    }
    klines = [
        [
            now_ms - 300_000,
            "64700",
            "64900",
            "64600",
            "64800",
            "0",
            0,
            "0",
            0,
            "0",
            "0",
            "0",
        ],
        [
            now_ms,
            "64800",
            "65100",
            "64750",
            "65000.12",
            "0",
            0,
            "0",
            0,
            "0",
            "0",
            "0",
        ],
    ]

    def fake_get(
        url: str,
        params: dict[str, object] | None = None,
        **_kw: object,
    ) -> _Resp:
        if "ticker/24hr" in url:
            return _Resp(ticker)
        if "klines" in url:
            assert params is not None
            assert params["interval"] == "5m"
            assert params["limit"] == 12
            return _Resp(klines)
        raise AssertionError(url)

    client = MagicMock()
    client.get.side_effect = fake_get
    tape = fetch_live_tape(interval="5m", range_key="1h", client=client)
    assert isinstance(tape, LiveTape)
    assert tape.last == pytest.approx(65000.12)
    assert tape.change_24h_pct == pytest.approx(0.015)
    assert "binance" in tape.source
    assert tape.interval == "5m"
    assert tape.range_key == "1h"
    assert len(tape.bar_closes) == 2
    assert tape.bar_opens[0] == pytest.approx(64700)
    assert tape.bar_highs[-1] == pytest.approx(65100)
    assert tape.bar_lows[0] == pytest.approx(64600)
    assert tape.bar_closes[-1] == pytest.approx(65000.12)


def test_fetch_live_tape_coinbase_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(
        url: str,
        params: dict[str, object] | None = None,
        **_kw: object,
    ) -> _Resp:
        if "binance" in url or "binance.vision" in url:
            return _Resp({}, status=451)
        if "prices/BTC-USD/spot" in url:
            return _Resp({"data": {"amount": "65123.45"}})
        if "candles" in url:
            ts = int(datetime(2026, 7, 19, 12, 0, tzinfo=UTC).timestamp())
            # newest first: [time, low, high, open, close, volume]
            return _Resp(
                [
                    [ts, 64000, 66000, 65000, 65123.45, 1],
                    [ts - 300, 64000, 66000, 64900, 65000, 1],
                ]
            )
        raise AssertionError(url)

    client = MagicMock()
    client.get.side_effect = fake_get
    tape = fetch_live_tape(interval="5m", range_key="1h", client=client)
    assert tape.source == "coinbase"
    assert tape.last == pytest.approx(65123.45)
    assert tape.bar_opens[-1] == pytest.approx(65000)
    assert tape.bar_closes[-1] == pytest.approx(65123.45)
