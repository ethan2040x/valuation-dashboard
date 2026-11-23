"""Tests for CSV import, failure resilience (last-known-good preservation), API, and static export."""

from __future__ import annotations

import json
import re
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.export import build_static_snapshot_bundle, export_static_html
from app.jobs import run_csv_import, run_daily_refresh
from app.sources.base import FundamentalItem, FundamentalsSource, HoldingItem, HoldingsSource
from app.store import ValuationStore


class FailingHoldingsSource(HoldingsSource):
    name = "qqq_holdings"

    def fetch_holdings(self) -> tuple[str, list[HoldingItem]]:
        raise RuntimeError("Upstream 503 Service Unavailable")


class StubHoldingsSource(HoldingsSource):
    name = "qqq_holdings"

    def __init__(self, as_of: str, holdings: list[HoldingItem]) -> None:
        self._as_of = as_of
        self._holdings = holdings

    def fetch_holdings(self) -> tuple[str, list[HoldingItem]]:
        return self._as_of, self._holdings


class StubFundamentalsSource(FundamentalsSource):
    name = "yfinance"

    def __init__(self, mapping: dict[str, FundamentalItem]) -> None:
        self._mapping = mapping

    def fetch_fundamentals(self, tickers: list[str]) -> dict[str, FundamentalItem]:
        return {t: self._mapping[t] for t in tickers if t in self._mapping}


def _create_sample_csv(path, num_days: int = 420):
    start = date(2025, 1, 2)
    lines = ["date,pe_ttm,pe_fwd"]
    d = start
    written = 0
    while written < num_days:
        if d.weekday() < 5:
            pe_ttm = round(26.0 + (written % 30) * 0.25, 2)
            pe_fwd = round(22.0 + (written % 30) * 0.20, 2)
            lines.append(f"{d.isoformat()},{pe_ttm},{pe_fwd}")
            written += 1
        d += timedelta(days=1)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_csv_import_and_failure_preserves_last_successful_values(tmp_path):
    """Acceptance test: when live source fails, API still shows last successful date & value (never null/0)."""
    db_path = tmp_path / "resilience.db"
    csv_path = tmp_path / "history.csv"
    _create_sample_csv(csv_path, num_days=300)

    store = ValuationStore(db_path)
    imp = run_csv_import(store, csv_path, instrument_symbol="NDX", source="csv_import")
    assert imp["imported"]["pe_ttm"] == 300
    assert imp["imported"]["pe_fwd"] == 300

    # Successful daily live refresh on a newer date
    ok_res = run_daily_refresh(
        store,
        instrument_symbol="NDX",
        holdings_source=StubHoldingsSource(
            "2026-11-20",
            [
                HoldingItem(ticker="NVDA", weight=0.5),
                HoldingItem(ticker="AAPL", weight=0.5),
            ],
        ),
        fundamentals_source=StubFundamentalsSource(
            {
                "NVDA": FundamentalItem("NVDA", price=150.0, eps_ttm=5.0, eps_fwd=6.0),
                "AAPL": FundamentalItem("AAPL", price=200.0, eps_ttm=8.0, eps_fwd=10.0),
            }
        ),
    )
    assert ok_res["status"] == "success"

    # Now simulate upstream source failure on the next refresh
    fail_res = run_daily_refresh(
        store,
        instrument_symbol="NDX",
        holdings_source=FailingHoldingsSource(),
    )
    assert fail_res["status"] == "failed"

    app = create_app(db_path=db_path, enable_scheduler=False, auto_seed=False, frontend_dist_dir=None)
    client = TestClient(app)

    snap = client.get("/api/snapshot?instrument=NDX").json()
    pe_ttm = snap["indicators"]["pe_ttm"]["latest"]
    pe_fwd = snap["indicators"]["pe_fwd"]["latest"]

    # Last successful values are preserved intact
    assert pe_ttm["date"] == "2026-11-20"
    assert pe_ttm["value"] is not None and pe_ttm["value"] > 0
    assert pe_fwd["date"] == "2026-11-20"
    assert pe_fwd["value"] is not None and pe_fwd["value"] > 0

    # Job status bar surfaces the failure
    jobs_by_name = {j["job"]: j for j in snap["status"]["jobs"]}
    assert jobs_by_name["daily_refresh"]["status"] == "failed"
    assert "503" in jobs_by_name["daily_refresh"]["error"]
    assert jobs_by_name["daily_refresh"]["last_success_at"] is not None


def test_static_export_matches_api_payload_and_embeds_json(tmp_path):
    """Acceptance test: static snapshot bundle matches REST API identically and embeds into standalone HTML."""
    db_path = tmp_path / "export.db"
    csv_path = tmp_path / "history.csv"
    _create_sample_csv(csv_path, num_days=300)

    store = ValuationStore(db_path)
    run_csv_import(store, csv_path, instrument_symbol="NDX", source="seed_csv")

    app = create_app(db_path=db_path, enable_scheduler=False, auto_seed=False, frontend_dist_dir=None)
    client = TestClient(app)

    api_indicators = client.get("/api/indicators").json()
    api_snapshot = client.get("/api/snapshot?instrument=NDX").json()
    api_series_ttm_5y = client.get("/api/indicators/pe_ttm/series?instrument=NDX&range=5y").json()

    bundle = build_static_snapshot_bundle(store, instrument_symbol="NDX")
    assert bundle["indicators"] == api_indicators
    assert bundle["snapshot"]["indicators"] == api_snapshot["indicators"]
    assert bundle["series"]["pe_ttm"]["5y"] == api_series_ttm_5y

    # Render into a mock single-file template HTML
    tpl = tmp_path / "template.html"
    tpl.write_text(
        "<!doctype html><html><head><!--VALUATION_SNAPSHOT_INJECT--></head><body><div id='root'></div></body></html>",
        encoding="utf-8",
    )
    out = tmp_path / "snapshot.html"
    export_static_html(store, instrument_symbol="NDX", template_path=tpl, output_path=out)
    html = out.read_text(encoding="utf-8")

    match = re.search(r"window\.__VALUATION_SNAPSHOT__=(\{.*\});</script>", html)
    assert match is not None
    embedded = json.loads(match.group(1))
    assert embedded["mode"] == "static"
    assert embedded["snapshot"]["indicators"]["pe_ttm"]["latest"]["value"] == (
        api_snapshot["indicators"]["pe_ttm"]["latest"]["value"]
    )
