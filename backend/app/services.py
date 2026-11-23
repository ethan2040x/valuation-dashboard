"""Shared query services used by both REST API routes and the static HTML exporter.

Keeping one implementation guarantees the dynamic dashboard and static snapshot
consume the exact same data structures.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pandas as pd

from app.config import DEFAULT_INSTRUMENT_SYMBOL, INSTRUMENTS
from app.indicators import Indicator, discover_indicators, get_indicator, list_indicators
from app.models import IndicatorPoint
from app.store import ValuationStore, utc_now_iso

RANGE_YEARS: dict[str, int | None] = {
    "1y": 1,
    "5y": 5,
    "10y": 10,
    "all": None,
}

# Approximate count of US market holidays per year (used to avoid false-positive missing-day alerts)
US_MARKET_HOLIDAYS_PER_YEAR = 9


def get_instruments_payload(store: ValuationStore) -> list[dict[str, Any]]:
    """Return metadata for all available instruments."""
    db_rows = {row["symbol"].upper(): row for row in store.list_instruments()}
    result: list[dict[str, Any]] = []
    for symbol, meta in INSTRUMENTS.items():
        row = db_rows.get(symbol)
        result.append(
            {
                "id": row["id"] if row else None,
                "symbol": meta["symbol"],
                "name": meta["name"],
                "en_name": meta["en_name"],
                "etf_ticker": meta["etf_ticker"],
                "description": meta["description"],
            }
        )
    return result


def get_indicators_payload() -> list[dict[str, Any]]:
    """Return metadata and display config for every registered indicator."""
    discover_indicators()
    return [ind.metadata() for ind in list_indicators()]


def _filter_by_range(
    points: list[IndicatorPoint],
    range_key: str,
) -> list[IndicatorPoint]:
    years = RANGE_YEARS.get(range_key.lower())
    if years is None or not points:
        return points

    latest_ts = pd.Timestamp(points[-1].date)
    cutoff_date = (latest_ts - pd.DateOffset(years=years)).date().isoformat()
    return [pt for pt in points if pt.date >= cutoff_date]


def _compute_series_stats(points: list[IndicatorPoint]) -> dict[str, Any]:
    vals = [float(pt.value) for pt in points if pt.value is not None]
    if not vals:
        return {
            "count": 0,
            "mean": None,
            "std": None,
            "plus_1std": None,
            "minus_1std": None,
            "min": None,
            "median": None,
            "max": None,
        }
    s = pd.Series(vals, dtype="float64")
    mean = float(s.mean())
    std = float(s.std(ddof=0)) if len(vals) > 1 else 0.0
    return {
        "count": len(vals),
        "mean": round(mean, 4),
        "std": round(std, 4),
        "plus_1std": round(mean + std, 4),
        "minus_1std": round(mean - std, 4),
        "min": round(float(s.min()), 4),
        "median": round(float(s.median()), 4),
        "max": round(float(s.max()), 4),
    }


def get_indicator_series_payload(
    store: ValuationStore,
    indicator_id: str,
    instrument_symbol: str = DEFAULT_INSTRUMENT_SYMBOL,
    range_key: str = "5y",
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return time series and summary stats for a single indicator."""
    discover_indicators()
    indicator = get_indicator(indicator_id)
    if indicator is None:
        raise KeyError(f"Unknown indicator: {indicator_id}")

    instrument = store.get_instrument(instrument_symbol)
    if instrument is None:
        raise KeyError(f"Unknown instrument: {instrument_symbol}")

    effective_params = params if params is not None else dict(indicator.display.default_params)
    all_points = store.get_indicator_values(
        instrument_id=int(instrument["id"]),
        indicator_id=indicator.id,
        params=effective_params,
    )
    filtered = _filter_by_range(all_points, range_key)
    sources = sorted({pt.source for pt in filtered if pt.source})

    return {
        "indicator_id": indicator.id,
        "name": indicator.name,
        "unit": indicator.unit,
        "instrument": instrument["symbol"],
        "range": range_key.lower(),
        "params": effective_params,
        "sources": sources,
        "stats": _compute_series_stats(filtered),
        "series": [
            {
                "date": pt.date,
                "value": pt.value,
                "source": pt.source,
                "is_incomplete": pt.is_incomplete,
                "meta": pt.meta,
            }
            for pt in filtered
        ],
    }


def _estimate_missing_trading_days(dates: list[str], lookback_days: int = 365) -> int:
    """Estimate missing trading days in the most recent `lookback_days` window.

    Uses Mon-Fri business days between earliest and latest observed date in the window,
    subtracting approximate US market holidays so normal market closures are not over-counted.
    """
    if len(dates) < 2:
        return 0
    latest = date.fromisoformat(dates[-1])
    cutoff = (latest - timedelta(days=lookback_days)).isoformat()
    recent = [d for d in dates if d >= cutoff]
    if len(recent) < 2:
        return 0

    start = date.fromisoformat(recent[0])
    end = date.fromisoformat(recent[-1])
    bday_count = len(pd.bdate_range(start=start, end=end))
    span_years = max(0.0, (end - start).days / 365.25)
    expected_trading_days = max(0, int(round(bday_count - span_years * US_MARKET_HOLIDAYS_PER_YEAR)))
    return max(0, expected_trading_days - len(set(recent)))


def _build_indicator_snapshot_entry(
    store: ValuationStore,
    instrument_id: int,
    indicator: Indicator,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Build latest value + daily delta for one indicator + param set.

    Guarantees we return the latest non-null value when available (so upstream failures
    never wipe the displayed number to null or 0). For percentile indicators with
    insufficient history (< 1 year), returns the latest metadata explaining why it is hidden.
    """
    latest_valid = store.get_latest_indicator_points(
        instrument_id=instrument_id,
        indicator_id=indicator.id,
        params=params,
        limit=2,
        include_null=False,
    )

    if not latest_valid:
        latest_any = store.get_latest_indicator_points(
            instrument_id=instrument_id,
            indicator_id=indicator.id,
            params=params,
            limit=1,
            include_null=True,
        )
        pt = latest_any[0] if latest_any else None
        return {
            "params": params,
            "date": pt.date if pt else None,
            "value": None,
            "prev_date": None,
            "prev_value": None,
            "delta": None,
            "delta_pct": None,
            "source": pt.source if pt else None,
            "is_incomplete": pt.is_incomplete if pt else False,
            "meta": pt.meta if pt else {},
        }

    curr = latest_valid[0]
    prev = latest_valid[1] if len(latest_valid) > 1 else None
    delta: float | None = None
    delta_pct: float | None = None
    if curr.value is not None and prev is not None and prev.value is not None:
        delta = round(curr.value - prev.value, 4)
        if prev.value != 0:
            delta_pct = round((curr.value - prev.value) / abs(prev.value) * 100.0, 2)

    return {
        "params": params,
        "date": curr.date,
        "value": curr.value,
        "prev_date": prev.date if prev else None,
        "prev_value": prev.value if prev else None,
        "delta": delta,
        "delta_pct": delta_pct,
        "source": curr.source,
        "is_incomplete": curr.is_incomplete,
        "meta": curr.meta,
    }


def get_snapshot_payload(
    store: ValuationStore,
    instrument_symbol: str = DEFAULT_INSTRUMENT_SYMBOL,
) -> dict[str, Any]:
    """Return latest snapshot across all indicators + data status bar info."""
    discover_indicators()
    instrument = store.get_instrument(instrument_symbol)
    if instrument is None:
        raise KeyError(f"Unknown instrument: {instrument_symbol}")
    instrument_id = int(instrument["id"])

    indicators_data: dict[str, Any] = {}
    reference_dates: list[str] = []

    for ind in list_indicators():
        combos = ind.param_combinations()
        if combos == [{}]:
            entry = _build_indicator_snapshot_entry(store, instrument_id, ind, {})
            indicators_data[ind.id] = {
                "id": ind.id,
                "name": ind.name,
                "unit": ind.unit,
                "latest": entry,
                "variants": [entry],
            }
            if not reference_dates:
                pts = store.get_indicator_values(instrument_id, ind.id, {})
                reference_dates = [pt.date for pt in pts if pt.value is not None]
        else:
            variants = [
                _build_indicator_snapshot_entry(store, instrument_id, ind, combo)
                for combo in combos
            ]
            # Pick default_params variant as the primary headline entry
            default_entry = next(
                (v for v in variants if v["params"] == ind.display.default_params),
                variants[0],
            )
            indicators_data[ind.id] = {
                "id": ind.id,
                "name": ind.name,
                "unit": ind.unit,
                "latest": default_entry,
                "variants": variants,
            }

    job_runs = store.get_latest_job_runs()
    last_updated_at = None
    for j in job_runs:
        ts = j.get("last_success_at") or j.get("finished_at") or j.get("started_at")
        if ts and (last_updated_at is None or ts > last_updated_at):
            last_updated_at = ts

    missing_days = _estimate_missing_trading_days(reference_dates)
    latest_data_date = reference_dates[-1] if reference_dates else None

    return {
        "instrument": instrument,
        "generated_at": utc_now_iso(),
        "indicators": indicators_data,
        "status": {
            "last_updated_at": last_updated_at,
            "latest_data_date": latest_data_date,
            "missing_days_1y": missing_days,
            "total_trading_days": len(reference_dates),
            "jobs": job_runs,
        },
    }
