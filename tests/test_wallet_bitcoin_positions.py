from __future__ import annotations

from datetime import UTC, date, datetime

from ccquant.models import WalletTransfer
from ccquant.storage import MarketStore
from ccquant.wallet.positions import snapshot_bitcoin_positions


def _btc_transfer(
    *,
    block_time: datetime,
    direction: str,
    amount: float,
    address: str,
    counterparty: str,
    transfer_index: int,
) -> WalletTransfer:
    return WalletTransfer(
        chain="bitcoin",
        tx_hash=f"tx-{transfer_index}",
        transfer_index=transfer_index,
        block_time=block_time,
        from_address=address if direction == "outflow" else counterparty,
        to_address=address if direction == "inflow" else counterparty,
        asset_mint_or_contract="btc",
        asset_symbol="BTC",
        amount=amount,
        amount_usd=None,
        direction=direction,
        program_or_method="p2wpkh",
        source="test",
    )


def test_snapshot_bitcoin_positions_net_balance(tmp_path) -> None:
    store = MarketStore(tmp_path / "ccquant.duckdb")
    watched = "1NDyJtNTjmwk5xPNe21PaRLLJ46W4hKEMj"
    other = "bc1qjasf9z3h7l3jkaware86a4s4ut9t928cerovd"
    try:
        transfers = [
            _btc_transfer(
                block_time=datetime(2024, 1, 1, 12, tzinfo=UTC),
                direction="inflow",
                amount=2.0,
                address=watched,
                counterparty=other,
                transfer_index=0,
            ),
            _btc_transfer(
                block_time=datetime(2024, 1, 2, 12, tzinfo=UTC),
                direction="outflow",
                amount=0.5,
                address=watched,
                counterparty=other,
                transfer_index=1,
            ),
        ]
        store.upsert_wallet_transfers(transfers)
        count = snapshot_bitcoin_positions(store, as_of=date(2024, 1, 2))
        assert count == 1
        row = store.connection.execute(
            """
            select balance
            from wallet_positions_daily
            where address = ? and chain = 'bitcoin' and date = ?
            """,
            [watched, date(2024, 1, 2)],
        ).fetchone()
        assert row is not None
        assert float(row[0]) == 1.5
    finally:
        store.close()
