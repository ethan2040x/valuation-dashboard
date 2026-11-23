"""SPY (S&P 500) daily holdings adapter (Slickcharts primary, iShares IVV CSV & local CSV fallback)."""

from __future__ import annotations

import csv
import io
import logging
from datetime import date
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

from app.config import HTTP_TIMEOUT_SECONDS
from app.sources.base import HoldingItem, HoldingsSource, parse_float
from app.sources.qqq_holdings import (
    DEFAULT_HEADERS,
    _VALID_TICKER_RE,
    _normalize_weights,
    _parse_as_of_date,
    parse_invesco_csv,
)

logger = logging.getLogger(__name__)

SLICKCHARTS_SP500_URL = "https://www.slickcharts.com/sp500"
ISHARES_IVV_CSV_URL = (
    "https://www.ishares.com/us/products/239726/ishares-core-sp-500-etf/"
    "1467271812596.ajax?fileType=csv&fileName=IVV_holdings&dataType=fund"
)

_ISHARES_TICKER_MAP: dict[str, str] = {
    "BRKB": "BRK.B",
    "BFB": "BF.B",
}
_EXCLUDED_TICKERS = {"CASH", "USD", "XTSLA"}


def parse_slickcharts_sp500_html(html_text: str) -> tuple[str, list[HoldingItem]]:
    """Parse Slickcharts S&P 500 constituents table into (as_of_date, holdings)."""
    soup = BeautifulSoup(html_text, "lxml")
    table = soup.find("table")
    if table is None:
        raise ValueError("No holdings table found in Slickcharts S&P 500 HTML")

    holdings: list[HoldingItem] = []
    for tr in table.find_all("tr")[1:]:
        tds = [td.get_text(strip=True) for td in tr.find_all("td")]
        if len(tds) < 4:
            continue
        # Standard Slickcharts columns: #, Company, Symbol, Weight, Price, Chg, % Chg
        name = tds[1]
        ticker = tds[2].strip().upper()
        weight_val = parse_float(tds[3])
        if not ticker or weight_val is None or weight_val <= 0:
            continue
        if not _VALID_TICKER_RE.match(ticker):
            continue
        holdings.append(HoldingItem(ticker=ticker, weight=weight_val, name=name))

    if len(holdings) < 200:
        raise ValueError(f"Parsed too few S&P 500 holdings from Slickcharts: {len(holdings)}")

    return date.today().isoformat(), _normalize_weights(holdings)


def parse_ishares_csv(csv_text: str) -> tuple[str, list[HoldingItem]]:
    """Parse iShares Core S&P 500 ETF (IVV) holdings CSV into (as_of_date, holdings)."""
    cleaned_text = csv_text.lstrip("\ufeff")
    lines = [line for line in cleaned_text.splitlines() if line.strip()]
    if not lines:
        raise ValueError("Empty iShares CSV response")

    as_of_date: str | None = None
    header_idx: int | None = None

    for idx, line in enumerate(lines[:20]):
        lower = line.lower()
        if as_of_date is None and "fund holdings as of" in lower:
            cells = next(csv.reader([line]), [])
            for cell in cells[1:]:
                if cell and cell.strip() and cell.strip() != "-":
                    as_of_date = _parse_as_of_date(cell)
                    break
        if "ticker" in lower and "weight" in lower:
            header_idx = idx
            break

    if header_idx is None:
        raise ValueError("Could not locate header row in iShares IVV CSV")

    reader = csv.DictReader(io.StringIO("\n".join(lines[header_idx:])))
    holdings: list[HoldingItem] = []

    for row in reader:
        normalized_row = {
            (k.strip().lower() if k else ""): (v.strip() if isinstance(v, str) else v)
            for k, v in row.items()
        }
        asset_class = (normalized_row.get("asset class") or "").strip()
        if asset_class and asset_class.lower() != "equity":
            continue

        raw_ticker = (
            normalized_row.get("ticker")
            or normalized_row.get("holding ticker")
            or normalized_row.get("symbol")
            or ""
        ).strip().upper()
        ticker = _ISHARES_TICKER_MAP.get(raw_ticker, raw_ticker)

        weight_raw = (
            normalized_row.get("weight (%)")
            or normalized_row.get("weight")
            or normalized_row.get("portfolio weight")
        )
        weight_val = parse_float(weight_raw)

        if not ticker or weight_val is None or weight_val <= 0:
            continue
        if ticker in _EXCLUDED_TICKERS or not _VALID_TICKER_RE.match(ticker):
            continue

        name = normalized_row.get("name") or normalized_row.get("holding name")
        holdings.append(HoldingItem(ticker=ticker, weight=weight_val, name=name))

    if len(holdings) < 200:
        raise ValueError(f"Parsed too few S&P 500 holdings from iShares CSV: {len(holdings)}")

    return (as_of_date or date.today().isoformat()), _normalize_weights(holdings)


class SPYHoldingsSource(HoldingsSource):
    """Fetches daily SPY / S&P 500 constituent weights with Slickcharts -> iShares IVV -> local CSV fallback."""

    name = "spy_holdings"

    def __init__(
        self,
        timeout: float = HTTP_TIMEOUT_SECONDS,
        local_csv_path: Path | None = None,
    ) -> None:
        self.timeout = timeout
        self.local_csv_path = local_csv_path

    def fetch_holdings(self) -> tuple[str, list[HoldingItem]]:
        errors: list[str] = []

        # 1. Try Slickcharts S&P 500 constituents table
        try:
            with httpx.Client(
                timeout=self.timeout,
                headers=DEFAULT_HEADERS,
                follow_redirects=True,
            ) as client:
                resp = client.get(SLICKCHARTS_SP500_URL)
                resp.raise_for_status()
                as_of, holdings = parse_slickcharts_sp500_html(resp.text)
                logger.info(
                    "Fetched %d S&P 500 holdings from Slickcharts (%s)", len(holdings), as_of
                )
                return as_of, holdings
        except Exception as exc:
            msg = f"Slickcharts S&P 500 failed: {exc}"
            logger.warning(msg)
            errors.append(msg)

        # 2. Fallback to iShares Core S&P 500 (IVV) official CSV
        try:
            with httpx.Client(
                timeout=self.timeout,
                headers=DEFAULT_HEADERS,
                follow_redirects=True,
            ) as client:
                resp = client.get(ISHARES_IVV_CSV_URL)
                resp.raise_for_status()
                as_of, holdings = parse_ishares_csv(resp.text)
                logger.info(
                    "Fetched %d S&P 500 holdings from iShares IVV CSV (%s)", len(holdings), as_of
                )
                return as_of, holdings
        except Exception as exc:
            msg = f"iShares IVV CSV fallback failed: {exc}"
            logger.warning(msg)
            errors.append(msg)

        # 3. Optional local CSV file if configured
        if self.local_csv_path and self.local_csv_path.exists():
            text = self.local_csv_path.read_text(encoding="utf-8")
            if "fund holdings as of" in text.lower():
                as_of, holdings = parse_ishares_csv(text)
            else:
                as_of, holdings = parse_invesco_csv(text)
            logger.info(
                "Loaded %d SPY holdings from local CSV %s", len(holdings), self.local_csv_path
            )
            return as_of, holdings

        raise RuntimeError("All SPY holdings sources failed: " + " | ".join(errors))
