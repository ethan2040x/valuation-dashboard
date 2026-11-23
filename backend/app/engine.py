"""Indicator dependency resolution and computation engine."""

from __future__ import annotations

import logging
from collections import deque
from typing import Any

from app.config import DEFAULT_INSTRUMENT_SYMBOL, SOURCE_PRIORITY
from app.indicators import ComputeDeps, Indicator, discover_indicators, list_indicators
from app.models import IndicatorPoint
from app.store import ValuationStore, utc_now_iso

logger = logging.getLogger(__name__)


def resolve_execution_order(indicators: list[Indicator]) -> list[Indicator]:
    """Topologically sort indicators by `depends_on`.

    Raises ValueError on missing dependencies or cycles.
    """
    by_id: dict[str, Indicator] = {ind.id: ind for ind in indicators}
    in_degree: dict[str, int] = {ind.id: 0 for ind in indicators}
    dependents: dict[str, list[str]] = {ind.id: [] for ind in indicators}

    for ind in indicators:
        for dep_id in ind.depends_on:
            if dep_id not in by_id:
                raise ValueError(
                    f"Indicator {ind.id!r} depends on unknown indicator {dep_id!r}"
                )
            dependents[dep_id].append(ind.id)
            in_degree[ind.id] += 1

    queue: deque[str] = deque(
        sorted(
            [ind_id for ind_id, deg in in_degree.items() if deg == 0],
            key=lambda i: (by_id[i].display.order, i),
        )
    )
    ordered: list[Indicator] = []

    while queue:
        current_id = queue.popleft()
        ordered.append(by_id[current_id])
        next_ready: list[str] = []
        for child_id in dependents[current_id]:
            in_degree[child_id] -= 1
            if in_degree[child_id] == 0:
                next_ready.append(child_id)
        for child_id in sorted(next_ready, key=lambda i: (by_id[i].display.order, i)):
            queue.append(child_id)

    if len(ordered) != len(indicators):
        remaining = sorted(set(by_id.keys()) - {ind.id for ind in ordered})
        raise ValueError(f"Circular indicator dependency detected: {remaining}")

    return ordered


class IndicatorEngine:
    """Computes all registered indicators in dependency order and writes back to SQLite."""

    def __init__(
        self,
        store: ValuationStore,
        source_priority: list[str] | None = None,
    ) -> None:
        self.store = store
        self.source_priority = (
            list(source_priority)
            if source_priority is not None
            else store.get_source_priority()[1]
        )

    def compute_all(
        self,
        instrument_symbol: str = DEFAULT_INSTRUMENT_SYMBOL,
        indicators: list[Indicator] | None = None,
    ) -> dict[str, int]:
        """Compute and persist all registered indicators for an instrument.

        Returns mapping `{indicator_id: total_saved_points}`.
        """
        discover_indicators()
        target_indicators = indicators if indicators is not None else list_indicators()
        ordered = resolve_execution_order(target_indicators)

        instrument = self.store.get_or_create_instrument(instrument_symbol)
        instrument_id = int(instrument["id"])

        # Load constituent snapshots once if any indicator needs them
        constituents = (
            self.store.get_constituent_snapshots(instrument_id)
            if any(ind.uses_constituents for ind in ordered)
            else {}
        )

        # Load required raw series once
        raw_ids: set[str] = set()
        for ind in ordered:
            raw_ids.update(ind.raw_series_ids)
        raw_series_map: dict[str, list[dict[str, Any]]] = {
            sid: self.store.get_raw_series(instrument_id, sid) for sid in raw_ids
        }

        computed_cache: dict[str, list[IndicatorPoint]] = {}
        saved_counts: dict[str, int] = {}
        computed_at = utc_now_iso()

        for ind in ordered:
            deps = ComputeDeps(
                computed_cache,
                constituents=constituents,
                raw_series_map=raw_series_map,
                source_priority=self.source_priority,
            )
            combos = ind.param_combinations()
            total_saved = 0
            default_series: list[IndicatorPoint] | None = None

            for param_set in combos:
                points = ind.compute(deps, **param_set)
                saved = self.store.save_indicator_values(
                    instrument_id=instrument_id,
                    indicator_id=ind.id,
                    params=param_set,
                    points=points,
                    computed_at=computed_at,
                    replace=True,
                )
                total_saved += saved
                if param_set == {} or param_set == ind.display.default_params:
                    default_series = points
                elif default_series is None:
                    default_series = points

            computed_cache[ind.id] = default_series or []
            saved_counts[ind.id] = total_saved
            logger.info(
                "Computed indicator %s for %s: %d rows across %d param sets",
                ind.id,
                instrument_symbol,
                total_saved,
                len(combos),
            )

        return saved_counts
