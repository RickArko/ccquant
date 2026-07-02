from __future__ import annotations

from ccquant.config import load_config


def test_load_default_config(monkeypatch) -> None:
    monkeypatch.delenv("CCQUANT_DB", raising=False)
    cfg = load_config()
    assert str(cfg.database) == "data/ccquant.duckdb"
    assert cfg.universe.size == 100
    assert cfg.hourly.enabled is True

