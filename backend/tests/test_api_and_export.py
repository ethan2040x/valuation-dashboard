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
from app.sources.spy_holdings import parse_ishares_csv, parse_slickcharts_sp500_html
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


def test_spy_instrument_api_and_multi_instrument_static_export(tmp_path):
    """Verify SPY REST API endpoints and multi-instrument static snapshot bundle."""
    db_path = tmp_path / "multi.db"
    ndx_csv = tmp_path / "ndx.csv"
    spy_csv = tmp_path / "spy.csv"
    _create_sample_csv(ndx_csv, num_days=300)
    _create_sample_csv(spy_csv, num_days=300)

    store = ValuationStore(db_path)
    run_csv_import(store, ndx_csv, instrument_symbol="NDX", source="seed_csv")
    run_csv_import(store, spy_csv, instrument_symbol="SPY", source="seed_csv")

    app = create_app(db_path=db_path, enable_scheduler=False, auto_seed=False, frontend_dist_dir=None)
    client = TestClient(app)

    instruments = client.get("/api/instruments").json()
    symbols = [inst["symbol"] for inst in instruments]
    assert "NDX" in symbols
    assert "SPY" in symbols

    spy_snap = client.get("/api/snapshot?instrument=SPY").json()
    assert spy_snap["instrument"]["symbol"] == "SPY"
    assert spy_snap["indicators"]["pe_ttm"]["latest"]["value"] is not None
    assert spy_snap["indicators"]["pe_fwd"]["latest"]["value"] is not None

    spy_series = client.get("/api/indicators/pe_ttm/series?instrument=SPY&range=5y").json()
    assert spy_series["instrument"] == "SPY"
    assert len(spy_series["series"]) == 300

    bundle = build_static_snapshot_bundle(store, instrument_symbol="NDX")
    assert [inst["symbol"] for inst in bundle["instruments"]] == symbols
    assert "NDX" in bundle["snapshots"] and "SPY" in bundle["snapshots"]
    assert "NDX" in bundle["series_by_instrument"] and "SPY" in bundle["series_by_instrument"]
    assert bundle["snapshots"]["SPY"]["indicators"]["pe_ttm"]["latest"]["value"] == (
        spy_snap["indicators"]["pe_ttm"]["latest"]["value"]
    )
    assert bundle["series_by_instrument"]["SPY"]["pe_ttm"]["5y"] == spy_series


def test_spy_holdings_parsers():
    """Verify Slickcharts S&P 500 HTML and iShares IVV CSV parsers."""
    def _alpha_ticker(idx: int) -> str:
        # Generate valid 4-letter tickers (AAAA..AAZZ) matching ^[A-Z]{1,5}([.-][A-Z])?$
        a = chr(65 + (idx // 26) % 26)
        b = chr(65 + idx % 26)
        return f"SY{a}{b}"

    # 1. Synthetic Slickcharts S&P 500 HTML table with 210 rows
    rows_html = "\n".join(
        f"<tr><td>{i}</td><td>Company {i}</td><td>{_alpha_ticker(i)}</td><td>{100.0 / 210:.4f}%</td></tr>"
        for i in range(1, 211)
    )
    html = f"<html><body><table><tr><th>#</th><th>Company</th><th>Symbol</th><th>Weight</th></tr>{rows_html}</table></body></html>"
    as_of_html, html_holdings = parse_slickcharts_sp500_html(html)
    assert re.match(r"^\d{4}-\d{2}-\d{2}$", as_of_html)
    assert len(html_holdings) == 210
    assert abs(sum(h.weight for h in html_holdings) - 1.0) < 1e-9

    # 2. Synthetic iShares IVV CSV with UTF-8 BOM, Fund Holdings as of, BRKB->BRK.B, and Cash row
    csv_lines = [
        '\ufeffiShares Core S&P 500 ETF',
        'Fund Holdings as of,"Nov 20, 2026"',
        'Inception Date,"May 15, 2000"',
        'Ticker,Name,Sector,Asset Class,Market Value,Weight (%),Notional Value,Shares',
        'BRKB,BERKSHIRE HATHAWAY INC CLASS B,Financials,Equity,1000,1.70,1000,10',
        'XTSLA,BLK CSH FND TREASURY SL AGENCY,Cash and/or Derivatives,Money Market,10,0.05,10,10',
        'USD,USD CASH,Cash and/or Derivatives,Cash,5,0.02,5,5',
    ]
    for i in range(1, 211):
        csv_lines.append(f"{_alpha_ticker(i)},STOCK {i},Tech,Equity,100,{98.3 / 210:.4f},100,1")
    as_of_csv, csv_holdings = parse_ishares_csv("\n".join(csv_lines))
    assert as_of_csv == "2026-11-20"
    tickers = {h.ticker for h in csv_holdings}
    assert "BRK.B" in tickers
    assert "BRKB" not in tickers
    assert "XTSLA" not in tickers
    assert "USD" not in tickers
    assert len(csv_holdings) == 211
    assert abs(sum(h.weight for h in csv_holdings) - 1.0) < 1e-9
