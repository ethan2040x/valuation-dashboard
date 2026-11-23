"""Base interfaces for data source adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class HoldingItem:
    """Single index constituent weight."""

    ticker: str
    weight: float  # Fraction, e.g. 0.085 for 8.5%
    name: str | None = None


@dataclass(frozen=True)
class FundamentalItem:
    """Single stock price and EPS snapshot."""

    ticker: str
    price: float | None
    eps_ttm: float | None
    eps_fwd: float | None
    error: str | None = None
    market_cap: float | None = None
    name: str | None = None
    sector: str | None = None
    currency: str | None = None


class HoldingsSource(ABC):
    """Adapter that returns constituent weights for an instrument."""

    name: str = "unknown"

    @abstractmethod
    def fetch_holdings(self) -> tuple[str, list[HoldingItem]]:
        """Return (as_of_date YYYY-MM-DD, list of HoldingItem)."""


class FundamentalsSource(ABC):
    """Adapter that fetches price, trailing EPS, and forward EPS for tickers."""

    name: str = "unknown"

    @abstractmethod
    def fetch_fundamentals(self, tickers: list[str]) -> dict[str, FundamentalItem]:
        """Return mapping of ticker -> FundamentalItem."""


class HistoricalSeriesSource(ABC):
    """Adapter fetching historical index or equity P/E series into `raw_series`."""

    source_id: str = "unknown"
    tier: str = "free"
    supported_instruments: tuple[str, ...] = ()
    supported_series: tuple[str, ...] = ()

    def is_configured(self) -> bool:
        return True

    def supports(self, instrument_symbol: str) -> bool:
        return instrument_symbol.upper() in self.supported_instruments

    @abstractmethod
    def fetch_history(
        self,
        instrument_symbol: str,
        start_date: str,
        end_date: str | None = None,
    ) -> dict[str, list[tuple[str, float]]]:
        """Return `{series_id: [(date_iso, value), ...]}`."""


def normalize_ticker_for_yahoo(ticker: str) -> str:
    """Normalize ticker symbol for Yahoo Finance (e.g. BRK.B -> BRK-B)."""
    cleaned = ticker.strip().upper()
    if "." in cleaned and not cleaned.endswith("."):
        cleaned = cleaned.replace(".", "-")
    return cleaned


def parse_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if value != value:  # NaN check
            return None
        return float(value)
    text = str(value).strip().replace(",", "").replace("%", "").replace("$", "")
    if not text or text.lower() in {"nan", "none", "null", "n/a", "-", "--"}:
        return None
    try:
        num = float(text)
        return None if num != num else num
    except ValueError:
        return None
