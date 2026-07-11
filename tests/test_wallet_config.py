from __future__ import annotations

from ccquant.config import load_config


def test_wallet_tracking_config_defaults(monkeypatch) -> None:
    monkeypatch.delenv("CCQUANT_DB", raising=False)
    cfg = load_config()
    assert cfg.wallet_tracking.enabled is True
    assert "solana" in cfg.wallet_tracking.chains
    assert "arbitrum" in cfg.wallet_tracking.chains
    assert "bitcoin" not in cfg.wallet_tracking.chains
    assert cfg.wallet_tracking.history.extract_days == 7
    assert cfg.wallet_tracking.tail.max_wallets == 50
