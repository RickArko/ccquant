"""US spot Bitcoin ETF flows (Farside) + MSTR equity health helpers."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from html.parser import HTMLParser

import httpx

from ccquant.models import EquityDaily, EtfFlowPoint

LOGGER = logging.getLogger(__name__)

FARSIDE_BTC_URL = "https://farside.co.uk/btc/"
YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
DATE_RE = re.compile(r"^\d{1,2}\s+[A-Za-z]{3}\s+\d{4}$")
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Curated US spot BTC ETF tickers tracked on the Farside table.
ETF_TICKERS = (
    "IBIT",
    "FBTC",
    "BITB",
    "ARKB",
    "BTCO",
    "EZBC",
    "BRRR",
    "HODL",
    "BTCW",
    "MSBT",
    "GBTC",
    "BTC",
)


def parse_flow_cell(raw: str) -> float | None:
    """Parse a Farside cell (US$m). Parentheses = negative; blank/- = missing."""
    s = raw.strip().replace(",", "").replace("\u2013", "-").replace("&nbsp;", "")
    if s in ("", "-"):
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    try:
        value = float(s)
    except ValueError:
        return None
    return -value if neg else value


class _TableParser(HTMLParser):
    """Minimal HTML table extractor (no BeautifulSoup dependency)."""

    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._in_cell = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []
            self._in_cell = True

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._cell is not None and self._row is not None:
            text = re.sub(r"\s+", " ", "".join(self._cell)).strip()
            self._row.append(text)
            self._cell = None
            self._in_cell = False
        elif tag == "tr" and self._row is not None and self._table is not None:
            if self._row:
                self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            if self._table:
                self.tables.append(self._table)
            self._table = None

    def handle_data(self, data: str) -> None:
        if self._in_cell and self._cell is not None:
            self._cell.append(data)


def parse_farside_btc_html(html: str) -> list[dict[str, float | str | None]]:
    """Parse Farside BTC ETF flow table into per-day records."""
    parser = _TableParser()
    parser.feed(html)
    want = (*ETF_TICKERS, "Total")
    for table in parser.tables:
        first_data = next(
            (i for i, row in enumerate(table) if row and DATE_RE.match(row[0])),
            None,
        )
        if first_data is None:
            continue
        colmap: dict[str, int] = {}
        for row in table[:first_data]:
            for idx, text in enumerate(row):
                if text in want and text not in colmap:
                    colmap[text] = idx
        if "IBIT" not in colmap or "Total" not in colmap:
            continue
        out: list[dict[str, float | str | None]] = []
        for row in table[first_data:]:
            if not row or not DATE_RE.match(row[0]):
                continue
            rec: dict[str, float | str | None] = {"date": row[0]}
            for name in want:
                idx_opt = colmap.get(name)
                if idx_opt is None or idx_opt >= len(row):
                    rec[name] = None
                else:
                    rec[name] = parse_flow_cell(row[idx_opt])
            out.append(rec)
        if out:
            return out
    raise ValueError("Farside BTC ETF flow table not found or schema changed")


def fetch_farside_btc_flows(client: httpx.Client) -> list[EtfFlowPoint]:
    """Fetch and flatten Farside US spot BTC ETF daily flows (USD millions)."""
    # Warm root for cookies, then fetch the BTC table page.
    client.get("https://farside.co.uk/", headers=HEADERS, timeout=30.0)
    resp = client.get(FARSIDE_BTC_URL, headers=HEADERS, timeout=30.0)
    resp.raise_for_status()
    rows = parse_farside_btc_html(resp.text)
    points: list[EtfFlowPoint] = []
    for row in rows:
        raw_date = str(row["date"])
        day = datetime.strptime(raw_date, "%d %b %Y").date()
        for ticker in (*ETF_TICKERS, "TOTAL"):
            key = "Total" if ticker == "TOTAL" else ticker
            value = row.get(key)
            if value is None:
                continue
            points.append(
                EtfFlowPoint(
                    date=day,
                    ticker=ticker,
                    net_flow_usd_m=float(value),
                    source="farside",
                )
            )
    return points


def fetch_yahoo_daily(
    client: httpx.Client,
    symbol: str,
    *,
    range_: str = "5y",
) -> list[EquityDaily]:
    """Fetch daily adjusted closes from Yahoo Finance chart API."""
    import time

    # Warm the quote page first — bare chart calls often return HTTP 429.
    client.get(
        f"https://finance.yahoo.com/quote/{symbol.upper()}",
        headers=HEADERS,
        timeout=30.0,
    )
    urls = [
        YAHOO_CHART.format(symbol=symbol.upper()),
        f"https://query2.finance.yahoo.com/v8/finance/chart/{symbol.upper()}",
    ]
    resp: httpx.Response | None = None
    for attempt in range(3):
        for url in urls:
            resp = client.get(
                url,
                params={"interval": "1d", "range": range_},
                headers=HEADERS,
                timeout=30.0,
            )
            if resp.status_code != 429:
                break
        if resp is not None and resp.status_code != 429:
            break
        time.sleep(2.0 * (attempt + 1))
    if resp is None:
        raise RuntimeError(f"Yahoo chart failed for {symbol}")
    resp.raise_for_status()
    payload = resp.json()
    result = (payload.get("chart") or {}).get("result") or []
    if not result:
        raise ValueError(f"Yahoo chart empty for {symbol}")
    block = result[0]
    timestamps = block.get("timestamp") or []
    quote = ((block.get("indicators") or {}).get("quote") or [{}])[0]
    closes = quote.get("close") or []
    points: list[EquityDaily] = []
    for ts, close in zip(timestamps, closes, strict=False):
        if close is None:
            continue
        day = datetime.fromtimestamp(int(ts), tz=UTC).date()
        points.append(
            EquityDaily(
                symbol=symbol.upper(),
                date=day,
                close=float(close),
                source="yahoo",
            )
        )
    return points


def mstr_etf_health(
    *,
    etf_flow_7d_m: float | None,
    mstr_rel_20d: float | None,
) -> tuple[int, str]:
    """Combine ETF 7d net flow + MSTR vs BTC relative strength into a signal.

    Returns ``(signal, label)`` where signal is -1 / 0 / +1.
    """
    etf_sig = 0
    if etf_flow_7d_m is not None:
        if etf_flow_7d_m > 50:
            etf_sig = 1
        elif etf_flow_7d_m < -50:
            etf_sig = -1

    mstr_sig = 0
    if mstr_rel_20d is not None:
        if mstr_rel_20d > 0.02:
            mstr_sig = 1
        elif mstr_rel_20d < -0.02:
            mstr_sig = -1

    score = etf_sig + mstr_sig
    if score >= 2:
        return 1, "strong demand"
    if score <= -2:
        return -1, "weak demand"
    if score > 0:
        return 1, "constructive"
    if score < 0:
        return -1, "cautious"
    return 0, "mixed"
