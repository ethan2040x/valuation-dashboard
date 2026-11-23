"""yfinance adapter for constituent price, trailing 12M EPS, and forward 12M consensus EPS."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import yfinance as yf

from app.config import YFINANCE_MAX_WORKERS
from app.sources.base import (
    FundamentalItem,
    FundamentalsSource,
    normalize_ticker_for_yahoo,
    parse_float,
)

logger = logging.getLogger(__name__)


MAX_SANE_POSITIVE_EY = 1.0  # Reject corrupted positive EPS implying P/E < 1.0x (e.g. unadjusted splits)


def _sanitize_eps(ticker: str, field: str, eps: float | None, price: float | None) -> float | None:
    if eps is None or price is None or price <= 0:
        return eps
    # Always preserve loss-making companies (eps <= 0); only guard extreme positive data glitches
    if eps > 0 and (eps / price) > MAX_SANE_POSITIVE_EY:
        logger.warning(
            "Dropping anomalous %s=%.4f for %s (price=%.2f, implied PE=%.3fx)",
            field,
            eps,
            ticker,
            price,
            price / eps,
        )
        return None
    return eps


def _extract_fundamental(ticker: str, info: dict[str, Any]) -> FundamentalItem:
    price = (
        parse_float(info.get("currentPrice"))
        or parse_float(info.get("regularMarketPrice"))
        or parse_float(info.get("previousClose"))
        or parse_float(info.get("regularMarketPreviousClose"))
    )
    eps_ttm = parse_float(info.get("trailingEps"))
    eps_fwd = parse_float(info.get("forwardEps"))

    # Fallback: if forwardEps missing but forwardPE and price exist, derive eps_fwd = price / forwardPE
    if eps_fwd is None and price is not None and price > 0:
        fwd_pe = parse_float(info.get("forwardPE"))
        if fwd_pe is not None and fwd_pe != 0:
            eps_fwd = price / fwd_pe

    if eps_ttm is None and price is not None and price > 0:
        ttm_pe = parse_float(info.get("trailingPE"))
        if ttm_pe is not None and ttm_pe != 0:
            eps_ttm = price / ttm_pe

    eps_ttm = _sanitize_eps(ticker, "eps_ttm", eps_ttm, price)
    eps_fwd = _sanitize_eps(ticker, "eps_fwd", eps_fwd, price)

    return FundamentalItem(
        ticker=ticker,
        price=price,
        eps_ttm=eps_ttm,
        eps_fwd=eps_fwd,
    )


def _fetch_single_ticker(ticker: str) -> FundamentalItem:
    yf_symbol = normalize_ticker_for_yahoo(ticker)
    try:
        t = yf.Ticker(yf_symbol)
        info = t.info or {}
        if not info:
            return FundamentalItem(
                ticker=ticker,
                price=None,
                eps_ttm=None,
                eps_fwd=None,
                error="Empty info from yfinance",
            )
        return _extract_fundamental(ticker, info)
    except Exception as exc:
        logger.debug("yfinance failed for %s (%s): %s", ticker, yf_symbol, exc)
        return FundamentalItem(
            ticker=ticker,
            price=None,
            eps_ttm=None,
            eps_fwd=None,
            error=str(exc),
        )


class YFinanceFundamentalsSource(FundamentalsSource):
    """Fetches price, trailingEps, and forwardEps via yfinance."""

    name = "yfinance"

    def __init__(self, max_workers: int = YFINANCE_MAX_WORKERS) -> None:
        self.max_workers = max_workers

    def fetch_fundamentals(self, tickers: list[str]) -> dict[str, FundamentalItem]:
        unique_tickers = list(dict.fromkeys(t.strip().upper() for t in tickers if t.strip()))
        results: dict[str, FundamentalItem] = {}
        if not unique_tickers:
            return results

        workers = max(1, min(self.max_workers, len(unique_tickers)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            future_map = {pool.submit(_fetch_single_ticker, tk): tk for tk in unique_tickers}
            for fut in as_completed(future_map):
                tk = future_map[fut]
                try:
                    results[tk] = fut.result()
                except Exception as exc:
                    results[tk] = FundamentalItem(
                        ticker=tk,
                        price=None,
                        eps_ttm=None,
                        eps_fwd=None,
                        error=str(exc),
                    )
        return results
