"""Tests for IndicatorEngine dependency resolution and zero-change indicator extensibility."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.engine import IndicatorEngine, resolve_execution_order
from app.indicators import (
    ComputeDeps,
    Indicator,
    discover_indicators,
    register,
    unregister,
)
from app.models import ConstituentQuote, Display, IndicatorPoint
from app.store import ValuationStore


def test_topological_sort_detects_missing_and_cyclic_deps():
    class MissingDep(Indicator):
        id = "bad_missing"
        depends_on = ["does_not_exist"]

        def compute(self, deps: ComputeDeps, **params: Any) -> list[IndicatorPoint]:
            return []

    with pytest.raises(ValueError, match="unknown indicator"):
        resolve_execution_order([MissingDep()])

    class CycleA(Indicator):
        id = "cycle_a"
        depends_on = ["cycle_b"]

        def compute(self, deps: ComputeDeps, **params: Any) -> list[IndicatorPoint]:
            return []

    class CycleB(Indicator):
        id = "cycle_b"
        depends_on = ["cycle_a"]

        def compute(self, deps: ComputeDeps, **params: Any) -> list[IndicatorPoint]:
            return []

    with pytest.raises(ValueError, match="Circular"):
        resolve_execution_order([CycleA(), CycleB()])


def test_adding_earnings_yield_indicator_requires_zero_api_changes(tmp_path):
    """Acceptance test: adding EarningsYield = 1 / PE works across engine & all APIs with zero framework changes."""
    discover_indicators()
    db_path = tmp_path / "extensibility.db"
    store = ValuationStore(db_path)
    inst = store.get_or_create_instrument("NDX")

    # Seed 2 trading days of constituent snapshots
    store.save_constituent_snapshots(
        inst["id"],
        "2026-11-19",
        [
            ConstituentQuote(ticker="AAPL", weight=0.5, price=200.0, eps_ttm=8.0, eps_fwd=10.0),
            ConstituentQuote(ticker="MSFT", weight=0.5, price=400.0, eps_ttm=16.0, eps_fwd=20.0),
        ],
    )
    store.save_constituent_snapshots(
        inst["id"],
        "2026-11-20",
        [
            ConstituentQuote(ticker="AAPL", weight=0.5, price=250.0, eps_ttm=8.0, eps_fwd=10.0),
            ConstituentQuote(ticker="MSFT", weight=0.5, price=500.0, eps_ttm=16.0, eps_fwd=20.0),
        ],
    )

    # Register a new indicator dynamically (simulating dropping a single file into app/indicators/)
    @register
    class EarningsYieldExample(Indicator):
        id = "earnings_yield"
        name = "盈利收益率"
        unit = "%"
        description = "1 ÷ 滚动 PE × 100%"
        depends_on = ["pe_ttm"]
        display = Display(kind="metric_card", chartable=True, precision=2, suffix="%", order=25)

        def compute(self, deps: ComputeDeps, **params: Any) -> list[IndicatorPoint]:
            out: list[IndicatorPoint] = []
            for pt in deps["pe_ttm"]:
                if pt.value is not None and pt.value != 0:
                    out.append(
                        IndicatorPoint(
                            date=pt.date,
                            value=round((1.0 / pt.value) * 100.0, 4),
                            source=pt.source,
                            is_incomplete=pt.is_incomplete,
                        )
                    )
            return out

    try:
        engine = IndicatorEngine(store)
        counts = engine.compute_all("NDX")
        assert counts["pe_ttm"] == 2
        assert counts["earnings_yield"] == 2

        app = create_app(db_path=db_path, enable_scheduler=False, auto_seed=False, frontend_dist_dir=None)
        client = TestClient(app)

        # 1. /api/indicators automatically exposes the new indicator & display config
        inds = client.get("/api/indicators").json()
        ey_meta = next((i for i in inds if i["id"] == "earnings_yield"), None)
        assert ey_meta is not None
        assert ey_meta["name"] == "盈利收益率"
        assert ey_meta["display"]["kind"] == "metric_card"

        # 2. /api/snapshot automatically includes its latest value & daily change
        snap = client.get("/api/snapshot?instrument=NDX").json()
        ey_latest = snap["indicators"]["earnings_yield"]["latest"]
        # On 2026-11-19: PE = 25.0 => EY = 4.0%
        # On 2026-11-20: PE = 31.25 => EY = 3.2%
        assert ey_latest["date"] == "2026-11-20"
        assert ey_latest["value"] == pytest.approx(3.2, rel=1e-3)
        assert ey_latest["delta"] == pytest.approx(-0.8, rel=1e-3)

        # 3. /api/indicators/earnings_yield/series works out of the box
        series_resp = client.get("/api/indicators/earnings_yield/series?instrument=NDX&range=5y")
        assert series_resp.status_code == 200
        body = series_resp.json()
        assert len(body["series"]) == 2
        assert body["series"][0]["value"] == pytest.approx(4.0, rel=1e-3)
        assert body["series"][1]["value"] == pytest.approx(3.2, rel=1e-3)
    finally:
        unregister("earnings_yield")
