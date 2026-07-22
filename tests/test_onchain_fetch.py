"""Tests for on-chain fetch helpers."""

from __future__ import annotations

import json
from datetime import date
from unittest.mock import MagicMock

import httpx
import pytest

from ccquant.onchain_fetch import (
    fetch_bid_valuation_points,
    fetch_blockchain_chart,
    fetch_blockchain_info_points,
)


class _Resp:
    def __init__(
        self,
        payload: object,
        status: int = 200,
        *,
        text: str | None = None,
    ) -> None:
        self._payload = payload
        self.status_code = status
        self._text = text

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "err", request=MagicMock(), response=MagicMock()
            )

    def json(self) -> object:
        if isinstance(self._payload, BaseException):
            raise self._payload
        return self._payload

    @property
    def text(self) -> str:
        if self._text is not None:
            return self._text
        if isinstance(self._payload, (dict, list)):
            return json.dumps(self._payload)
        return str(self._payload)


def test_fetch_blockchain_chart_bad_schema_returns_empty() -> None:
    client = MagicMock()
    client.get.return_value = _Resp({"not_values": []})
    assert fetch_blockchain_chart(client, "hash-rate") == []


def test_fetch_blockchain_chart_json_decode_returns_empty() -> None:
    client = MagicMock()
    client.get.return_value = _Resp(ValueError("bad json"))
    assert fetch_blockchain_chart(client, "hash-rate") == []


def test_fetch_blockchain_info_points_skips_http_errors() -> None:
    calls = {"n": 0}

    def side_effect(
        url: str, params: dict[str, object] | None = None, **_kw: object
    ) -> _Resp:
        calls["n"] += 1
        if calls["n"] == 1:
            return _Resp({"values": "bad"})  # not a list → empty series
        return _Resp({}, status=503)

    client = MagicMock()
    client.get.side_effect = side_effect
    points = fetch_blockchain_info_points(client, delay_seconds=0.0)
    assert points == []
    assert calls["n"] >= 2


def test_fetch_blockchain_chart_parses_rows() -> None:
    client = MagicMock()
    client.get.return_value = _Resp(
        {"values": [{"x": 1_704_067_200, "y": "100.5"}, {"x": "bad", "y": 1}]}
    )
    rows = fetch_blockchain_chart(client, "hash-rate")
    assert len(rows) == 1
    assert rows[0][1] == pytest.approx(100.5)


def test_fetch_blockchain_chart_double_429_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exhausted 429 retries must return [] (not None) so callers can iterate."""
    monkeypatch.setattr("ccquant.onchain_fetch.time.sleep", lambda _s: None)
    client = MagicMock()
    client.get.return_value = _Resp({}, status=429)
    assert fetch_blockchain_chart(client, "hash-rate") == []
    assert client.get.call_count == 2


def test_fetch_bid_valuation_points_missing_key() -> None:
    points, status = fetch_bid_valuation_points(MagicMock(), api_key="")
    assert points == []
    assert status == "missing_key"


def test_fetch_bid_valuation_points_expired() -> None:
    client = MagicMock()
    client.get.return_value = _Resp({}, text='"Hello, your API key is EXPIRED"')
    points, status = fetch_bid_valuation_points(client, api_key="k")
    assert points == []
    assert status == "expired"


def test_fetch_bid_valuation_points_unexpected_payload() -> None:
    client = MagicMock()
    client.get.return_value = _Resp({"meta": "no data key"})
    points, status = fetch_bid_valuation_points(client, api_key="k")
    assert points == []
    assert status == "error:unexpected_payload"


def test_fetch_bid_valuation_points_polars_error_returns_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Schema/runtime errors during Polars parse must not crash sync."""

    def _boom(*_a: object, **_k: object) -> object:
        raise TypeError("bad schema")

    monkeypatch.setattr("ccquant.onchain_fetch.pl.DataFrame", _boom)
    client = MagicMock()
    client.get.return_value = _Resp(
        [{"date": "2024-01-01", "total_mvrv": 1.5}]
    )
    points, status = fetch_bid_valuation_points(client, api_key="k")
    assert points == []
    assert status.startswith("error:")
    assert "bad schema" in status


def test_fetch_bid_valuation_points_happy_path() -> None:
    client = MagicMock()
    client.get.return_value = _Resp(
        {
            "data": [
                {
                    "date": "2024-01-01",
                    "total_mvrv": 2.5,
                    "total_realized_price": 40_000.0,
                    "total_nupl": 0.4,
                },
                {
                    "date": "2024-01-01",  # same day → group_by last
                    "total_mvrv": 2.6,
                    "total_realized_price": 41_000.0,
                    "total_nupl": 0.41,
                },
            ]
        }
    )
    points, status = fetch_bid_valuation_points(client, api_key="k")
    assert status == "ok"
    by_metric = {p.metric: p for p in points}
    assert set(by_metric) == {"mvrv", "realized_price", "nupl"}
    assert by_metric["mvrv"].date == date(2024, 1, 1)
    assert by_metric["mvrv"].value == pytest.approx(2.6)
    assert by_metric["mvrv"].source == "bitcoinisdata"
