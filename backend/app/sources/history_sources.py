"""Historical valuation data source adapters (free public sources + optional paid API).

Supports:
- Multpl S&P 500 trailing P/E monthly table (`multpl`, free)
- Robert Shiller S&P 500 historical dataset (`shiller`, free)
- Siblis Research API (`siblis_free` free tier; `siblis_paid` when SIBLIS_API_KEY is configured)

Monthly or sparse historical anchors are expanded smoothly to NYSE trading days using daily
index closes from Yahoo Finance (`^GSPC` / `^NDX`) when available, with deterministic NYSE
calendar linear interpolation as an offline fallback.
"""

from __future__ import annotations

import csv
import io
import logging
import re
import statistics
from abc import ABC, abstractmethod
from bisect import bisect_left
from datetime import date, datetime, timedelta
from typing import Any

import httpx
import yfinance as yf
from bs4 import BeautifulSoup

from app.config import HTTP_TIMEOUT_SECONDS, INSTRUMENTS, SIBLIS_API_KEY
from app.sources.base import parse_float
from app.sources.qqq_holdings import DEFAULT_HEADERS

logger = logging.getLogger(__name__)

MULTPL_SP500_PE_URL = "https://www.multpl.com/s-p-500-pe-ratio/table/by-month"
SHILLER_DATAHUB_CSV_URL = (
    "https://raw.githubusercontent.com/datasets/s-and-p-500/main/data/data.csv"
)
SIBLIS_API_BASE_URL = "https://siblisresearch.com/api/v1"

# Special unscheduled NYSE full-day closures since 2006
_SPECIAL_NYSE_CLOSURES: set[date] = {
    date(2007, 1, 2),   # President Gerald Ford mourning
    date(2012, 10, 29), # Hurricane Sandy
    date(2012, 10, 30), # Hurricane Sandy
    date(2018, 12, 5),  # President George H.W. Bush mourning
    date(2025, 1, 9),   # President Jimmy Carter mourning
}


def _easter_sunday(year: int) -> date:
    """Compute Gregorian Easter Sunday using Butcher's algorithm."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _observed_nyse_holiday(d: date) -> date:
    """Observe weekend holidays on Friday (if Sat) or Monday (if Sun)."""
    if d.weekday() == 5:  # Saturday -> Friday
        return d - timedelta(days=1)
    if d.weekday() == 6:  # Sunday -> Monday
        return d + timedelta(days=1)
    return d


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """Return the n-th (1-based) occurrence of weekday (0=Mon..6=Sun) in year/month."""
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + (n - 1) * 7)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    """Return the last occurrence of weekday (0=Mon..6=Sun) in year/month."""
    next_month = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    last_day = next_month - timedelta(days=1)
    offset = (last_day.weekday() - weekday) % 7
    return last_day - timedelta(days=offset)


def _nyse_holidays_for_year(year: int) -> set[date]:
    """Return all NYSE full-day holidays occurring within calendar `year`."""
    holidays: set[date] = set()

    # New Year's Day: NYSE does not move Saturday Jan 1 back to Dec 31 (end of year)
    nyd = date(year, 1, 1)
    if nyd.weekday() == 6:
        holidays.add(date(year, 1, 2))
    elif nyd.weekday() < 5:
        holidays.add(nyd)

    # Martin Luther King Jr. Day (3rd Monday in January, observed by NYSE since 1998)
    holidays.add(_nth_weekday(year, 1, 0, 3))

    # Presidents' Day (3rd Monday in February)
    holidays.add(_nth_weekday(year, 2, 0, 3))

    # Good Friday (Friday before Easter Sunday)
    holidays.add(_easter_sunday(year) - timedelta(days=2))

    # Memorial Day (Last Monday in May)
    holidays.add(_last_weekday(year, 5, 0))

    # Juneteenth National Independence Day (NYSE holiday starting 2022)
    if year >= 2022:
        holidays.add(_observed_nyse_holiday(date(year, 6, 19)))

    # Independence Day (July 4)
    holidays.add(_observed_nyse_holiday(date(year, 7, 4)))

    # Labor Day (1st Monday in September)
    holidays.add(_nth_weekday(year, 9, 0, 1))

    # Thanksgiving Day (4th Thursday in November)
    holidays.add(_nth_weekday(year, 11, 3, 4))

    # Christmas Day (December 25)
    holidays.add(_observed_nyse_holiday(date(year, 12, 25)))

    for special in _SPECIAL_NYSE_CLOSURES:
        if special.year == year:
            holidays.add(special)

    return holidays


def us_nyse_trading_days(start_date: str, end_date: str) -> list[str]:
    """Return sorted ISO dates for all NYSE trading days in `[start_date, end_date]`."""
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    if end < start:
        return []

    holidays: set[date] = set()
    for yr in range(start.year, end.year + 1):
        holidays.update(_nyse_holidays_for_year(yr))

    days: list[str] = []
    cur = start
    while cur <= end:
        if cur.weekday() < 5 and cur not in holidays:
            days.append(cur.isoformat())
        cur += timedelta(days=1)
    return days


def _parse_flexible_date(raw: str) -> str | None:
    """Parse common financial date strings into ISO `YYYY-MM-DD`."""
    text = re.sub(r"[†*]+", "", raw).strip()
    if not text:
        return None
    for fmt in (
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%m/%d/%Y",
        "%Y-%m-%d %H:%M:%S",
        "%b %d, %Y",
        "%B %d, %Y",
        "%Y-%m",
        "%b %Y",
        "%B %Y",
    ):
        try:
            dt = datetime.strptime(text, fmt).date()
            return dt.isoformat()
        except ValueError:
            continue
    return None


def _clean_numeric(raw: Any) -> float | None:
    if raw is None:
        return None
    text = str(raw)
    text = re.sub(r"Est\.", "", text, flags=re.IGNORECASE)
    text = re.sub(r"[†*]+", "", text)
    text = re.sub(r"(?<=\d)\s*[xX]$", "", text.strip())
    return parse_float(text)


def parse_multpl_pe_html(html_text: str) -> list[tuple[str, float]]:
    """Parse Multpl S&P 500 P/E table HTML into sorted `(date_iso, value)` pairs."""
    soup = BeautifulSoup(html_text, "lxml")
    table = soup.find("table", id="datatable") or soup.find("table")
    if table is None:
        raise ValueError("No data table found in Multpl HTML")

    by_date: dict[str, float] = {}
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 2:
            continue
        date_iso = _parse_flexible_date(tds[0].get_text(" ", strip=True))
        val = _clean_numeric(tds[1].get_text(" ", strip=True))
        if date_iso and val is not None and 0 < val <= 300.0:
            by_date[date_iso] = val

    return sorted(by_date.items(), key=lambda item: item[0])


def parse_shiller_csv(csv_text: str) -> list[tuple[str, float]]:
    """Parse Robert Shiller / DataHub S&P 500 CSV into `(date_iso, pe_ttm)` pairs."""
    reader = csv.DictReader(io.StringIO(csv_text.lstrip("\ufeff")))
    if not reader.fieldnames:
        raise ValueError("Empty Shiller CSV response")

    lower_fields = {name.strip().lower(): name for name in reader.fieldnames if name}
    date_col = lower_fields.get("date")
    price_col = lower_fields.get("sp500") or lower_fields.get("p") or lower_fields.get("price")
    earnings_col = lower_fields.get("earnings") or lower_fields.get("e")
    pe_col = lower_fields.get("pe") or lower_fields.get("pe_ttm")

    if not date_col or (not pe_col and (not price_col or not earnings_col)):
        raise ValueError("Shiller CSV missing required date/SP500/Earnings columns")

    by_date: dict[str, float] = {}
    for row in reader:
        date_iso = _parse_flexible_date(row.get(date_col) or "")
        if not date_iso:
            continue

        pe_val: float | None = None
        if pe_col:
            pe_val = _clean_numeric(row.get(pe_col))
        if pe_val is None and price_col and earnings_col:
            price = _clean_numeric(row.get(price_col))
            earnings = _clean_numeric(row.get(earnings_col))
            if price is not None and earnings is not None and price > 0 and earnings > 0:
                pe_val = price / earnings

        if pe_val is not None and 0 < pe_val <= 200.0:
            by_date[date_iso] = round(pe_val, 4)

    return sorted(by_date.items(), key=lambda item: item[0])


def parse_siblis_csv(csv_text: str) -> list[tuple[str, float]]:
    """Parse Siblis Research CSV response into sorted `(date_iso, value)` pairs."""
    stripped = csv_text.lstrip("\ufeff").strip()
    if not stripped:
        return []

    reader = csv.DictReader(io.StringIO(stripped))
    if not reader.fieldnames:
        return []

    fields = [name.strip() for name in reader.fieldnames if name]
    lower_fields = {name.lower(): name for name in fields}
    date_col = (
        lower_fields.get("trading_day (eod)")
        or lower_fields.get("trading_day")
        or lower_fields.get("date")
    )
    if not date_col:
        raise ValueError(f"Siblis CSV missing date column: {fields}")

    val_col: str | None = None
    for candidate in ("value", "pe_trailing", "pe_forward", "pe_ttm", "pe_fwd"):
        if candidate in lower_fields:
            val_col = lower_fields[candidate]
            break
    if not val_col:
        ignored = {date_col.lower(), "ticker", "symbol", "index_name", "name"}
        remaining = [f for f in fields if f.lower() not in ignored]
        if not remaining:
            raise ValueError(f"Siblis CSV missing value column: {fields}")
        val_col = remaining[-1]

    by_date: dict[str, float] = {}
    for row in reader:
        date_iso = _parse_flexible_date(row.get(date_col) or "")
        val = _clean_numeric(row.get(val_col))
        if date_iso and val is not None and val > 0:
            by_date[date_iso] = round(val, 4)

    return sorted(by_date.items(), key=lambda item: item[0])


def parse_siblis_json(payload: dict[str, Any]) -> list[tuple[str, float]]:
    """Parse Siblis Research JSON response into sorted `(date_iso, value)` pairs."""
    items = payload.get("data")
    if not isinstance(items, list):
        return []

    by_date: dict[str, float] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        raw_date = (
            item.get("trading_day (EOD)")
            or item.get("trading_day")
            or item.get("date")
            or ""
        )
        date_iso = _parse_flexible_date(str(raw_date))
        val = _clean_numeric(item.get("value"))
        if date_iso and val is not None and val > 0:
            by_date[date_iso] = round(val, 4)

    return sorted(by_date.items(), key=lambda item: item[0])


def fetch_yahoo_index_closes(
    yahoo_symbol: str,
    start_date: str,
    end_date: str | None = None,
) -> dict[str, float]:
    """Fetch daily index closes from Yahoo Finance (`{YYYY-MM-DD: close}`), empty dict on failure."""
    try:
        end_arg = None
        if end_date:
            # yfinance `end` is exclusive; add 1 day so `end_date` is included
            end_arg = (date.fromisoformat(end_date) + timedelta(days=1)).isoformat()
        hist = yf.Ticker(yahoo_symbol).history(
            start=start_date,
            end=end_arg,
            interval="1d",
            auto_adjust=False,
        )
        if hist is None or hist.empty or "Close" not in hist.columns:
            return {}
        closes: dict[str, float] = {}
        for idx, val in hist["Close"].items():
            fval = parse_float(val)
            if fval is not None and fval > 0:
                dt_str = idx.date().isoformat() if hasattr(idx, "date") else str(idx)[:10]
                closes[dt_str] = fval
        return closes
    except Exception as exc:
        logger.warning("Failed to fetch Yahoo index closes for %s: %s", yahoo_symbol, exc)
        return {}


def _find_nearest_price_date(
    target_iso: str,
    sorted_price_dates: list[str],
    max_distance_days: int = 7,
) -> str | None:
    if not sorted_price_dates:
        return None
    idx = bisect_left(sorted_price_dates, target_iso)
    target_d = date.fromisoformat(target_iso)
    best_date: str | None = None
    best_dist = max_distance_days + 1
    for cand_idx in (idx - 1, idx, idx + 1):
        if 0 <= cand_idx < len(sorted_price_dates):
            cand_iso = sorted_price_dates[cand_idx]
            dist = abs((date.fromisoformat(cand_iso) - target_d).days)
            if dist <= max_distance_days and dist < best_dist:
                best_dist = dist
                best_date = cand_iso
    return best_date


def _linear_interpolate(x: int, xs: list[int], ys: list[float]) -> float:
    """Piecewise linear interpolation at integer ordinal `x` over sorted `xs`."""
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    idx = bisect_left(xs, x)
    if xs[idx] == x:
        return ys[idx]
    x0, x1 = xs[idx - 1], xs[idx]
    y0, y1 = ys[idx - 1], ys[idx]
    if x1 == x0:
        return y0
    ratio = (x - x0) / (x1 - x0)
    return y0 + ratio * (y1 - y0)


def expand_anchors_to_daily(
    anchors: list[tuple[str, float]],
    price_series: dict[str, float] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    max_pe: float = 200.0,
) -> list[tuple[str, float]]:
    """Expand sparse/monthly P/E anchors to daily NYSE trading-day points without sawtoothing.

    - If anchors are already weekly/daily (median gap <= 7 days) or < 2 anchors, returns
      cleaned anchors directly within `[start_date, end_date]`.
    - When `price_series` (daily index closes) is supplied, derives implied EPS at each
      anchor, linearly interpolates implied EPS across trading days, and computes
      `PE(t) = Close(t) / ImpliedEPS(t)`.
    - Otherwise falls back to linear interpolation of P/E across `us_nyse_trading_days`.
    """
    by_date: dict[str, float] = {}
    for d, v in anchors:
        if v is not None and 0 < v <= max_pe:
            by_date[d] = float(v)

    cleaned = sorted(by_date.items(), key=lambda item: item[0])
    if not cleaned:
        return []

    def _in_window(d: str) -> bool:
        if start_date and d < start_date:
            return False
        if end_date and d > end_date:
            return False
        return True

    if len(cleaned) < 2:
        return [(d, round(v, 4)) for d, v in cleaned if _in_window(d)]

    gaps = [
        (date.fromisoformat(cleaned[i][0]) - date.fromisoformat(cleaned[i - 1][0])).days
        for i in range(1, len(cleaned))
    ]
    if statistics.median(gaps) <= 7:
        return [(d, round(v, 4)) for d, v in cleaned if _in_window(d)]

    # Attempt price-aligned implied-EPS daily expansion
    if price_series:
        clean_prices = {
            d: float(p)
            for d, p in price_series.items()
            if p is not None and float(p) > 0
        }
        sorted_p_dates = sorted(clean_prices.keys())
        matched: dict[str, float] = {}
        for anchor_d, pe_val in cleaned:
            nearest = _find_nearest_price_date(anchor_d, sorted_p_dates, max_distance_days=7)
            if nearest is not None:
                matched[nearest] = clean_prices[nearest] / pe_val

        matched_items = sorted(matched.items(), key=lambda item: item[0])
        if len(matched_items) >= 2:
            eps_xs = [date.fromisoformat(d).toordinal() for d, _ in matched_items]
            eps_ys = [eps for _, eps in matched_items]
            span_start = matched_items[0][0]
            span_end = matched_items[-1][0]
            if start_date and start_date > span_start:
                span_start = start_date
            if end_date and end_date < span_end:
                span_end = end_date

            expanded: list[tuple[str, float]] = []
            for p_date in sorted_p_dates:
                if p_date < span_start or p_date > span_end:
                    continue
                interp_eps = _linear_interpolate(
                    date.fromisoformat(p_date).toordinal(), eps_xs, eps_ys
                )
                if interp_eps <= 0:
                    continue
                pe_daily = clean_prices[p_date] / interp_eps
                if 0 < pe_daily <= max_pe:
                    expanded.append((p_date, round(pe_daily, 4)))
            if expanded:
                return expanded

    # Offline fallback: linear PE interpolation across NYSE trading days
    eff_start = cleaned[0][0]
    eff_end = cleaned[-1][0]
    if start_date and start_date > eff_start:
        eff_start = start_date
    if end_date and end_date < eff_end:
        eff_end = end_date
    if eff_end < eff_start:
        return []

    anchor_xs = [date.fromisoformat(d).toordinal() for d, _ in cleaned]
    anchor_ys = [v for _, v in cleaned]
    trading_days = us_nyse_trading_days(eff_start, eff_end)
    result: list[tuple[str, float]] = []
    for td in trading_days:
        val = _linear_interpolate(date.fromisoformat(td).toordinal(), anchor_xs, anchor_ys)
        if 0 < val <= max_pe:
            result.append((td, round(val, 4)))
    return result


class HistoricalSeriesSource(ABC):
    """Adapter fetching historical index-level P/E series into `raw_series`."""

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


class MultplSP500Source(HistoricalSeriesSource):
    """Free Multpl S&P 500 monthly trailing P/E expanded to daily frequency."""

    source_id = "multpl"
    tier = "free"
    supported_instruments = ("SPY",)
    supported_series = ("pe_ttm",)

    def __init__(
        self,
        timeout: float = HTTP_TIMEOUT_SECONDS,
        use_yahoo_prices: bool = True,
    ) -> None:
        self.timeout = timeout
        self.use_yahoo_prices = use_yahoo_prices

    def fetch_history(
        self,
        instrument_symbol: str,
        start_date: str,
        end_date: str | None = None,
    ) -> dict[str, list[tuple[str, float]]]:
        sym = instrument_symbol.upper()
        if not self.supports(sym):
            return {}

        with httpx.Client(
            timeout=self.timeout,
            headers=DEFAULT_HEADERS,
            follow_redirects=True,
        ) as client:
            resp = client.get(MULTPL_SP500_PE_URL)
            resp.raise_for_status()
            anchors = parse_multpl_pe_html(resp.text)

        if not anchors:
            return {}

        # Include one anchor just before start_date to anchor boundary interpolation cleanly
        pre_start = (date.fromisoformat(start_date) - timedelta(days=45)).isoformat()
        filtered_anchors = [
            (d, v)
            for d, v in anchors
            if d >= pre_start and (not end_date or d <= end_date)
        ]
        if not filtered_anchors:
            return {}

        prices: dict[str, float] | None = None
        if self.use_yahoo_prices:
            yahoo_idx = INSTRUMENTS.get(sym, {}).get("yahoo_index", "^GSPC")
            prices = fetch_yahoo_index_closes(yahoo_idx, pre_start, end_date)

        daily = expand_anchors_to_daily(
            filtered_anchors,
            price_series=prices,
            start_date=start_date,
            end_date=end_date,
        )
        return {"pe_ttm": daily} if daily else {}


class ShillerSP500Source(HistoricalSeriesSource):
    """Free Robert Shiller / DataHub S&P 500 trailing P/E dataset."""

    source_id = "shiller"
    tier = "free"
    supported_instruments = ("SPY",)
    supported_series = ("pe_ttm",)

    def __init__(
        self,
        timeout: float = HTTP_TIMEOUT_SECONDS,
        use_yahoo_prices: bool = True,
    ) -> None:
        self.timeout = timeout
        self.use_yahoo_prices = use_yahoo_prices

    def fetch_history(
        self,
        instrument_symbol: str,
        start_date: str,
        end_date: str | None = None,
    ) -> dict[str, list[tuple[str, float]]]:
        sym = instrument_symbol.upper()
        if not self.supports(sym):
            return {}

        with httpx.Client(
            timeout=self.timeout,
            headers=DEFAULT_HEADERS,
            follow_redirects=True,
        ) as client:
            resp = client.get(SHILLER_DATAHUB_CSV_URL)
            resp.raise_for_status()
            anchors = parse_shiller_csv(resp.text)

        if not anchors:
            return {}

        pre_start = (date.fromisoformat(start_date) - timedelta(days=45)).isoformat()
        filtered_anchors = [
            (d, v)
            for d, v in anchors
            if d >= pre_start and (not end_date or d <= end_date)
        ]
        if not filtered_anchors:
            return {}

        prices: dict[str, float] | None = None
        if self.use_yahoo_prices:
            yahoo_idx = INSTRUMENTS.get(sym, {}).get("yahoo_index", "^GSPC")
            prices = fetch_yahoo_index_closes(yahoo_idx, pre_start, end_date)

        daily = expand_anchors_to_daily(
            filtered_anchors,
            price_series=prices,
            start_date=start_date,
            end_date=end_date,
        )
        return {"pe_ttm": daily} if daily else {}


class SiblisHistoricalSource(HistoricalSeriesSource):
    """Siblis Research historical P/E API (free tier by default, optional paid key)."""

    supported_instruments = ("NDX",)
    supported_series = ("pe_ttm", "pe_fwd")

    def __init__(
        self,
        paid: bool = False,
        api_key: str | None = None,
        timeout: float = HTTP_TIMEOUT_SECONDS,
        use_yahoo_prices: bool = True,
    ) -> None:
        self.paid = paid
        self.api_key = api_key if api_key is not None else (SIBLIS_API_KEY if paid else None)
        self.source_id = "siblis_paid" if paid else "siblis_free"
        self.tier = "paid" if paid else "free"
        self.timeout = timeout
        self.use_yahoo_prices = use_yahoo_prices

    def is_configured(self) -> bool:
        if not self.paid:
            return True
        return bool(self.api_key)

    def fetch_history(
        self,
        instrument_symbol: str,
        start_date: str,
        end_date: str | None = None,
    ) -> dict[str, list[tuple[str, float]]]:
        sym = instrument_symbol.upper()
        if not self.supports(sym):
            return {}
        if self.paid and not self.is_configured():
            raise RuntimeError("SIBLIS_API_KEY is not configured for siblis_paid")

        siblis_ticker = INSTRUMENTS.get(sym, {}).get("siblis_ticker", sym)
        endpoints = {
            "pe_ttm": "pe-trailing",
            "pe_fwd": "pe-forward",
        }

        headers = dict(DEFAULT_HEADERS)
        if self.paid and self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        raw_series_anchors: dict[str, list[tuple[str, float]]] = {}
        errors: list[str] = []

        with httpx.Client(
            timeout=self.timeout,
            headers=headers,
            follow_redirects=True,
        ) as client:
            for series_id, endpoint in endpoints.items():
                params: dict[str, str] = {"format": "csv", "from": start_date}
                if end_date:
                    params["to"] = end_date
                if self.paid and self.api_key:
                    params["apikey"] = self.api_key

                url = f"{SIBLIS_API_BASE_URL}/{siblis_ticker}/{endpoint}"
                try:
                    resp = client.get(url, params=params)
                    resp.raise_for_status()
                    content_type = resp.headers.get("content-type", "").lower()
                    body = resp.text.lstrip()
                    if "json" in content_type or body.startswith("{"):
                        pts = parse_siblis_json(resp.json())
                    else:
                        pts = parse_siblis_csv(resp.text)
                    if pts:
                        raw_series_anchors[series_id] = pts
                except Exception as exc:
                    msg = f"{series_id} ({endpoint}): {exc}"
                    logger.warning("Siblis fetch warning for %s: %s", sym, msg)
                    errors.append(msg)

        if not raw_series_anchors:
            if errors:
                raise RuntimeError("; ".join(errors))
            return {}

        # Only fetch Yahoo closes if any series is sparse (median gap > 7 days)
        needs_expansion = any(
            len(pts) >= 2
            and statistics.median(
                (date.fromisoformat(pts[i][0]) - date.fromisoformat(pts[i - 1][0])).days
                for i in range(1, len(pts))
            )
            > 7
            for pts in raw_series_anchors.values()
        )
        prices: dict[str, float] | None = None
        if needs_expansion and self.use_yahoo_prices:
            yahoo_idx = INSTRUMENTS.get(sym, {}).get("yahoo_index")
            if yahoo_idx:
                prices = fetch_yahoo_index_closes(yahoo_idx, start_date, end_date)

        output: dict[str, list[tuple[str, float]]] = {}
        for series_id, pts in raw_series_anchors.items():
            daily = expand_anchors_to_daily(
                pts,
                price_series=prices,
                start_date=start_date,
                end_date=end_date,
            )
            if daily:
                output[series_id] = daily
        return output


def get_historical_sources(
    instrument_symbol: str,
    include_paid: bool = False,
    source_ids: list[str] | None = None,
) -> list[HistoricalSeriesSource]:
    """Return configured historical adapters for an instrument (defaults to free-only)."""
    sym = instrument_symbol.upper()
    candidates: list[HistoricalSeriesSource] = [
        SiblisHistoricalSource(paid=False),
        MultplSP500Source(),
        ShillerSP500Source(),
        SiblisHistoricalSource(paid=True),
    ]
    requested = {s.strip() for s in source_ids} if source_ids else None

    selected: list[HistoricalSeriesSource] = []
    for adapter in candidates:
        if not adapter.supports(sym):
            continue
        if requested is not None and adapter.source_id not in requested:
            continue
        if adapter.tier == "paid":
            if not include_paid or not adapter.is_configured():
                continue
        selected.append(adapter)
    return selected
