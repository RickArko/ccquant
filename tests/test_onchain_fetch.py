"""Tests for on-chain fetch helpers."""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from ccquant.onchain_fetch import (
    fetch_blockchain_chart,
    fetch_blockchain_info_points,
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
        if isinstance(self._payload, BaseException):
            raise self._payload
        return self._payload


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
