"""QQQ daily holdings adapter (Invesco CSV primary, Slickcharts fallback)."""

from __future__ import annotations

import csv
import io
import logging
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

import httpx
from bs4 import BeautifulSoup

from app.config import HTTP_TIMEOUT_SECONDS
from app.sources.base import HoldingItem, HoldingsSource, parse_float

logger = logging.getLogger(__name__)

INVESCO_QQQ_CSV_URL = (
    "https://www.invesco.com/us/financial-products/etfs/holdings/main/holdings/0"
    "?audienceType=Investor&action=download&ticker=QQQ"
)
SLICKCHARTS_NDX_URL = "https://www.slickcharts.com/nasdaq100"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "text/csv,text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

_VALID_TICKER_RE = re.compile(r"^[A-Z]{1,5}([.\-][A-Z])?$")


def _parse_as_of_date(raw: str | None) -> str:
    if not raw:
        return date.today().isoformat()
    text = raw.strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%Y/%m/%d", "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return date.today().isoformat()


def parse_invesco_csv(csv_text: str) -> tuple[str, list[HoldingItem]]:
    """Parse Invesco QQQ holdings CSV into (as_of_date, holdings)."""
    lines = [line for line in csv_text.splitlines() if line.strip()]
    if not lines:
        raise ValueError("Empty Invesco CSV response")

    # Find header row containing Ticker and Weight
    header_idx = None
    for idx, line in enumerate(lines[:15]):
        lower = line.lower()
        if ("holding ticker" in lower or "ticker" in lower) and "weight" in lower:
            header_idx = idx
            break
    if header_idx is None:
        raise ValueError("Could not locate header row in Invesco QQQ CSV")

    reader = csv.DictReader(io.StringIO("\n".join(lines[header_idx:])))
    holdings: list[HoldingItem] = []
    as_of_date: str | None = None

    for row in reader:
        normalized_row = {
            (k.strip().lower() if k else ""): (v.strip() if isinstance(v, str) else v)
            for k, v in row.items()
        }
        ticker = (
            normalized_row.get("holding ticker")
            or normalized_row.get("ticker")
            or normalized_row.get("symbol")
            or ""
        ).strip().upper()
        weight_raw = (
            normalized_row.get("weight")
            or normalized_row.get("weight (%)")
            or normalized_row.get("portfolio weight")
        )
        weight_val = parse_float(weight_raw)

        if not ticker or weight_val is None or weight_val <= 0:
            continue
        # Exclude cash / currency / non-equity rows
        if not _VALID_TICKER_RE.match(ticker) or ticker in {"CASH", "USD"}:
            continue

        if as_of_date is None:
            raw_date = normalized_row.get("date") or normalized_row.get("as of date")
            if raw_date:
                as_of_date = _parse_as_of_date(raw_date)

        name = normalized_row.get("name") or normalized_row.get("holding name")
        holdings.append(HoldingItem(ticker=ticker, weight=weight_val, name=name))

    if len(holdings) < 50:
        raise ValueError(f"Parsed too few QQQ holdings from CSV: {len(holdings)}")

    return (as_of_date or date.today().isoformat()), _normalize_weights(holdings)


def parse_slickcharts_html(html_text: str) -> tuple[str, list[HoldingItem]]:
    """Parse Slickcharts Nasdaq-100 constituents table into (as_of_date, holdings)."""
    soup = BeautifulSoup(html_text, "lxml")
    table = soup.find("table")
    if table is None:
        raise ValueError("No holdings table found in Slickcharts HTML")

    holdings: list[HoldingItem] = []
    for tr in table.find_all("tr")[1:]:
        tds = [td.get_text(strip=True) for td in tr.find_all("td")]
        if len(tds) < 4:
            continue
        # Standard Slickcharts columns: #, Company, Symbol, Portfolio%, Price, Chg, %Chg
        name = tds[1]
        ticker = tds[2].strip().upper()
        weight_val = parse_float(tds[3])
        if not ticker or weight_val is None or weight_val <= 0:
            continue
        if not _VALID_TICKER_RE.match(ticker):
            continue
        holdings.append(HoldingItem(ticker=ticker, weight=weight_val, name=name))

    if len(holdings) < 50:
        raise ValueError(f"Parsed too few NDX holdings from Slickcharts: {len(holdings)}")

    return date.today().isoformat(), _normalize_weights(holdings)


def _normalize_weights(holdings: list[HoldingItem]) -> list[HoldingItem]:
    """Convert percentage weights (e.g. summing to ~100) to fractions summing to 1.0."""
    # Merge duplicate tickers if any (rare)
    merged: dict[str, tuple[float, str | None]] = {}
    for h in holdings:
        prev_w, prev_name = merged.get(h.ticker, (0.0, h.name))
        merged[h.ticker] = (prev_w + h.weight, prev_name or h.name)

    total = sum(w for w, _ in merged.values())
    if total <= 0:
        raise ValueError("Total constituent weight is zero")

    # If weights look like percentages (sum > 2), convert to fraction and normalize to 1.0
    result = [
        HoldingItem(ticker=ticker, weight=w / total, name=name)
        for ticker, (w, name) in merged.items()
    ]
    result.sort(key=lambda item: item.weight, reverse=True)
    return result


class QQQHoldingsSource(HoldingsSource):
    """Fetches daily QQQ constituent weights with Invesco -> Slickcharts fallback."""

    name = "qqq_holdings"

    def __init__(
        self,
        timeout: float = HTTP_TIMEOUT_SECONDS,
        local_csv_path: Path | None = None,
    ) -> None:
        self.timeout = timeout
        self.local_csv_path = local_csv_path

    def fetch_holdings(self) -> tuple[str, list[HoldingItem]]:
        errors: list[str] = []

        # 1. Try Invesco official CSV endpoint
        try:
            with httpx.Client(
                timeout=self.timeout,
                headers=DEFAULT_HEADERS,
                follow_redirects=True,
            ) as client:
                resp = client.get(INVESCO_QQQ_CSV_URL)
                resp.raise_for_status()
                as_of, holdings = parse_invesco_csv(resp.text)
                logger.info("Fetched %d QQQ holdings from Invesco CSV (%s)", len(holdings), as_of)
                return as_of, holdings
        except Exception as exc:
            msg = f"Invesco CSV failed: {exc}"
            logger.warning(msg)
            errors.append(msg)

        # 2. Fallback to Slickcharts Nasdaq-100 table
        try:
            with httpx.Client(
                timeout=self.timeout,
                headers=DEFAULT_HEADERS,
                follow_redirects=True,
            ) as client:
                resp = client.get(SLICKCHARTS_NDX_URL)
                resp.raise_for_status()
                as_of, holdings = parse_slickcharts_html(resp.text)
                logger.info(
                    "Fetched %d NDX holdings from Slickcharts (%s)", len(holdings), as_of
                )
                return as_of, holdings
        except Exception as exc:
            msg = f"Slickcharts fallback failed: {exc}"
            logger.warning(msg)
            errors.append(msg)

        # 3. Optional local CSV file if configured
        if self.local_csv_path and self.local_csv_path.exists():
            as_of, holdings = parse_invesco_csv(self.local_csv_path.read_text(encoding="utf-8"))
            logger.info(
                "Loaded %d QQQ holdings from local CSV %s", len(holdings), self.local_csv_path
            )
            return as_of, holdings

        raise RuntimeError("All QQQ holdings sources failed: " + " | ".join(errors))
