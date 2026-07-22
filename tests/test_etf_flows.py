"""Unit tests for Farside ETF flow parsing and MSTR/ETF health."""

from __future__ import annotations

from ccquant.etf_flows import mstr_etf_health, parse_farside_btc_html, parse_flow_cell


def test_parse_flow_cell_parentheses_and_blank() -> None:
    assert parse_flow_cell("(219.4)") == -219.4
    assert parse_flow_cell("223.5") == 223.5
    assert parse_flow_cell("0.0") == 0.0
    assert parse_flow_cell("-") is None
    assert parse_flow_cell("") is None


def test_parse_farside_btc_html_extracts_totals() -> None:
    html = """
    <html><body>
    <table class="etf">
      <tr><th></th><th>IBIT</th><th>FBTC</th><th>Total</th></tr>
      <tr><td></td><td>IBIT</td><td>FBTC</td><td></td></tr>
      <tr><td>01 Jul 2026</td><td>(10.0)</td><td>5.0</td><td>(5.0)</td></tr>
      <tr><td>02 Jul 2026</td><td>20.0</td><td>0.0</td><td>20.0</td></tr>
      <tr><td>Total</td><td>10</td><td>5</td><td>15</td></tr>
    </table>
    </body></html>
    """
    rows = parse_farside_btc_html(html)
    assert len(rows) == 2
    assert rows[0]["date"] == "01 Jul 2026"
    assert rows[0]["IBIT"] == -10.0
    assert rows[0]["Total"] == -5.0
    assert rows[1]["Total"] == 20.0


def test_mstr_etf_health_strong_and_weak() -> None:
    assert mstr_etf_health(etf_flow_7d_m=200.0, mstr_rel_20d=0.05) == (
        1,
        "strong demand",
    )
    assert mstr_etf_health(etf_flow_7d_m=-200.0, mstr_rel_20d=-0.05) == (
        -1,
        "weak demand",
    )
    assert mstr_etf_health(etf_flow_7d_m=None, mstr_rel_20d=None)[0] == 0
