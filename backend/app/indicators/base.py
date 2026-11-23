"""Base classes, registry helpers, and shared math utilities for valuation indicators."""

from __future__ import annotations

import itertools
from abc import ABC, abstractmethod
from typing import Any, Literal

import pandas as pd

from app.config import INCOMPLETE_WEIGHT_THRESHOLD, SOURCE_PRIORITY
from app.models import ConstituentQuote, Display, FormulaInfo, IndicatorPoint


class ComputeDeps(dict[str, list[IndicatorPoint]]):
    """Dependency container passed to `Indicator.compute`.

    Behaves like a standard `dict[indicator_id, list[IndicatorPoint]]` for derived
    indicators while also exposing raw constituent snapshots and raw series for base
    indicators.
    """

    def __init__(
        self,
        indicator_series: dict[str, list[IndicatorPoint]] | None = None,
        *,
        constituents: dict[str, list[ConstituentQuote]] | None = None,
        raw_series_map: dict[str, list[dict[str, Any]]] | None = None,
        source_priority: list[str] | None = None,
    ) -> None:
        super().__init__(indicator_series or {})
        self.constituents: dict[str, list[ConstituentQuote]] = constituents or {}
        self._raw_series_map: dict[str, list[dict[str, Any]]] = raw_series_map or {}
        self.source_priority: list[str] = list(source_priority or SOURCE_PRIORITY)

    def raw_series(self, series_id: str) -> list[dict[str, Any]]:
        return self._raw_series_map.get(series_id, [])


class Indicator(ABC):
    """Base class for all indicators (both base and derived)."""

    id: str = ""
    name: str = ""
    unit: str = ""
    description: str = ""
    depends_on: list[str] = []
    raw_series_ids: list[str] = []
    uses_constituents: bool = False
    params: dict[str, list[Any]] = {}
    display: Display = Display()
    formula: FormulaInfo | None = None

    @property
    def kind(self) -> Literal["base", "derived"]:
        return "derived" if self.depends_on else "base"

    def param_combinations(self) -> list[dict[str, Any]]:
        """Expand `self.params` into all Cartesian-product parameter dicts."""
        if not self.params:
            return [{}]
        keys = list(self.params.keys())
        value_lists = [self.params[k] for k in keys]
        return [dict(zip(keys, combo)) for combo in itertools.product(*value_lists)]

    def metadata(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "unit": self.unit,
            "description": self.description,
            "kind": self.kind,
            "depends_on": list(self.depends_on),
            "params": {k: list(v) for k, v in self.params.items()},
            "display": self.display.to_dict(),
            "formula": self.formula.to_dict() if self.formula is not None else None,
        }

    @abstractmethod
    def compute(self, deps: ComputeDeps, **params: Any) -> list[IndicatorPoint]:
        """Compute time series points for the given dependencies and parameters."""


def compute_weighted_pe(
    quotes: list[ConstituentQuote],
    eps_field: Literal["eps_ttm", "eps_fwd"],
    incomplete_threshold: float = INCOMPLETE_WEIGHT_THRESHOLD,
) -> tuple[float | None, bool, dict[str, Any]]:
    """Compute index-weighted P/E using weighted earnings yield:

        PE = 1 / sum(normalized_weight_i * (eps_i / price_i))

    Rules (per DESIGN.md Section 5):
    - Loss-making companies (eps <= 0) ARE included with negative/zero earnings yield.
    - Companies missing price (None or <= 0) or missing EPS (None) are excluded and
      remaining valid weights are re-normalized to sum to 1.0.
    - If missing companies' weight share exceeds `incomplete_threshold` (5%), the
      resulting day is flagged `is_incomplete = True`.
    """
    if not quotes:
        return None, True, {"total_count": 0, "valid_count": 0, "missing_weight": 1.0}

    total_weight = sum(max(0.0, float(q.weight)) for q in quotes)
    if total_weight <= 0:
        return None, True, {"total_count": len(quotes), "valid_count": 0, "missing_weight": 1.0}

    valid_items: list[tuple[float, float]] = []  # (weight, earnings_yield)
    missing_weight = 0.0
    loss_making_count = 0

    for q in quotes:
        w = max(0.0, float(q.weight))
        if w <= 0:
            continue
        eps = getattr(q, eps_field)
        price = q.price
        if price is None or price <= 0 or eps is None:
            missing_weight += w
            continue
        ey = float(eps) / float(price)
        if eps <= 0:
            loss_making_count += 1
        valid_items.append((w, ey))

    missing_ratio = missing_weight / total_weight
    is_incomplete = missing_ratio > incomplete_threshold

    if not valid_items:
        return (
            None,
            True,
            {
                "total_count": len(quotes),
                "valid_count": 0,
                "missing_weight": round(missing_ratio, 4),
            },
        )

    valid_weight_sum = sum(w for w, _ in valid_items)
    if valid_weight_sum <= 0:
        return None, True, {"total_count": len(quotes), "valid_count": 0, "missing_weight": 1.0}

    weighted_ey = sum((w / valid_weight_sum) * ey for w, ey in valid_items)
    meta = {
        "total_count": len(quotes),
        "valid_count": len(valid_items),
        "loss_making_count": loss_making_count,
        "missing_weight": round(missing_ratio, 4),
        "weighted_earnings_yield": round(weighted_ey, 6),
    }

    if weighted_ey <= 0:
        return None, True, meta

    pe_value = round(1.0 / weighted_ey, 4)
    return pe_value, is_incomplete, meta


def merge_with_raw_series(
    computed_points: list[IndicatorPoint],
    raw_rows: list[dict[str, Any]],
    source_priority: list[str] | None = None,
) -> list[IndicatorPoint]:
    """Merge computed constituent-based points with imported `raw_series` rows.

    When multiple sources exist on the same date, the highest-priority source wins.
    Unknown sources rank after all explicitly configured priorities.
    """
    priority = list(source_priority or SOURCE_PRIORITY)

    def rank(src: str) -> tuple[int, str]:
        try:
            return (priority.index(src), src)
        except ValueError:
            return (len(priority), src)

    by_date: dict[str, IndicatorPoint] = {}

    # Load raw series points first
    for row in raw_rows:
        d = row["date"]
        pt = IndicatorPoint(
            date=d,
            value=float(row["value"]),
            source=row["source"],
            is_incomplete=False,
            meta={"raw_source": row["source"]},
        )
        existing = by_date.get(d)
        if existing is None or rank(pt.source) < rank(existing.source):
            by_date[d] = pt

    # Overlay computed points respecting priority
    for pt in computed_points:
        if pt.value is None:
            continue
        existing = by_date.get(pt.date)
        if existing is None or rank(pt.source) < rank(existing.source):
            by_date[pt.date] = pt

    return [by_date[d] for d in sorted(by_date.keys())]


def rolling_percentile(
    series_points: list[IndicatorPoint],
    window_years: int,
) -> list[IndicatorPoint]:
    """Compute trailing rolling percentile for each date in `series_points`.

    Rules (per DESIGN.md Section 5):
    - Percentile = fraction of trading days in `[date - window_years, date]`
      whose value <= current value (expressed as 0..100%).
    - If actual history span < 1 year (365 days): value is None (not displayed).
    - If 1 year <= span < window_years (with 15-day holiday tolerance):
      display normally and annotate `partial_window=True`, `note="基于 X 年数据"`.
    """
    valid = [pt for pt in series_points if pt.value is not None]
    if not valid:
        return []

    df = pd.DataFrame(
        {
            "date_str": [pt.date for pt in valid],
            "ts": pd.to_datetime([pt.date for pt in valid]),
            "value": [float(pt.value) for pt in valid],
            "source": [pt.source for pt in valid],
            "is_incomplete": [pt.is_incomplete for pt in valid],
        }
    ).sort_values("ts").reset_index(drop=True)

    timestamps = df["ts"].tolist()
    values = df["value"].to_numpy()
    sources = df["source"].tolist()
    incompletes = df["is_incomplete"].tolist()
    date_strs = df["date_str"].tolist()

    # Maintain sliding left pointer since timestamps are sorted ascending
    left = 0
    results: list[IndicatorPoint] = []
    full_window_min_days = window_years * 365 - 15

    for i, current_ts in enumerate(timestamps):
        window_start = current_ts - pd.DateOffset(years=window_years)
        while left < i and timestamps[left] < window_start:
            left += 1

        span_days = int((current_ts - timestamps[left]).days)
        history_years = round(span_days / 365.25, 1)
        window_vals = values[left : i + 1]
        window_sources = sorted(set(sources[left : i + 1]))
        current_val = float(values[i])

        if span_days < 365:
            results.append(
                IndicatorPoint(
                    date=date_strs[i],
                    value=None,
                    source=sources[i],
                    is_incomplete=bool(incompletes[i]),
                    meta={
                        "window_years": window_years,
                        "insufficient_history": True,
                        "partial_window": False,
                        "history_years": history_years,
                        "count": int(len(window_vals)),
                        "current_base_value": round(current_val, 4),
                        "sources": window_sources,
                        "note": "历史数据不足 1 年",
                    },
                )
            )
            continue

        pct = float((window_vals <= current_val).mean() * 100.0)
        partial_window = span_days < full_window_min_days
        note = f"基于 {history_years:.1f} 年数据" if partial_window else None

        meta: dict[str, Any] = {
            "window_years": window_years,
            "insufficient_history": False,
            "partial_window": partial_window,
            "history_years": history_years,
            "count": int(len(window_vals)),
            "min": round(float(window_vals.min()), 2),
            "median": round(float(pd.Series(window_vals).median()), 2),
            "max": round(float(window_vals.max()), 2),
            "mean": round(float(window_vals.mean()), 2),
            "current_base_value": round(current_val, 2),
            "sources": window_sources,
        }
        if note:
            meta["note"] = note

        results.append(
            IndicatorPoint(
                date=date_strs[i],
                value=round(pct, 2),
                source=sources[i],
                is_incomplete=bool(incompletes[i]),
                meta=meta,
            )
        )

    return results
