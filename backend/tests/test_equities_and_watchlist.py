"""Tests for US equity watchlist, schema migration, SEC EDGAR / Yahoo history, batch CSV, and static export."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, timedelta
from typing import Any

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.config import DEFAULT_EQUITIES
from app.export import build_static_snapshot_bundle, export_static_html, export_static_site
from app.jobs import (
    run_compute_indicators,
    run_csv_import,
    run_daily_refresh,
    run_equity_refresh,
)
from app.models import EquitySnapshot
from app.services import get_snapshot_payload, get_watchlist_overview_payload
from app.sources.base import FundamentalItem, FundamentalsSource, HoldingItem, HoldingsSource
from app.sources.history_sources import extract_ttm_eps_from_yfinance_frames
from app.sources.sec_edgar import (
    build_daily_pe_from_eps_anchors,
    extract_ttm_eps_anchors_from_companyfacts,
)
from app.store import ValuationStore


class _StubHoldings(HoldingsSource):
    name = "qqq_holdings"

    def __init__(self, as_of: str, holdings: list[HoldingItem]) -> None:
        self._as_of = as_of
        self._holdings = holdings

    def fetch_holdings(self) -> tuple[str, list[HoldingItem]]:
        return self._as_of, self._holdings


class _RecordingFundamentals(FundamentalsSource):
    name = "yfinance"

    def __init__(self, mapping: dict[str, FundamentalItem]) -> None:
        self._mapping = mapping
        self.requested_batches: list[list[str]] = []

    def fetch_fundamentals(self, tickers: list[str]) -> dict[str, FundamentalItem]:
        self.requested_batches.append(list(tickers))
        return {t: self._mapping[t] for t in tickers if t in self._mapping}


def test_legacy_db_migration_and_onetime_top10_seeding(tmp_path):
    """Legacy 3-col instruments table migrates cleanly; Top 10 equities seed once; deletions persist."""
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE instruments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL
        );
        INSERT INTO instruments (symbol, name) VALUES ('NDX', '纳指 100');
        INSERT INTO instruments (symbol, name) VALUES ('SPY', '标普 500');
        """
    )
    conn.commit()
    conn.close()

    store = ValuationStore(db_path)
    indices = store.list_instruments(kind="index")
    equities = store.list_instruments(kind="equity")
    assert [i["symbol"] for i in indices] == ["NDX", "SPY"]
    assert len(equities) == len(DEFAULT_EQUITIES) == 10
    assert {i["symbol"] for i in equities} == {e["symbol"] for e in DEFAULT_EQUITIES}

    # Index deletion must be rejected
    with pytest.raises(ValueError, match="Cannot delete index"):
        store.delete_instrument("NDX")

    # Deleting a seeded equity persists across re-initialization
    assert store.delete_instrument("TSLA") is True
    store_reloaded = ValuationStore(db_path)
    equities_after = {i["symbol"] for i in store_reloaded.list_instruments(kind="equity")}
    assert "TSLA" not in equities_after
    assert len(equities_after) == 9


def test_sec_edgar_companyfacts_q4_derivation_splits_and_clamping():
    """Verify SEC XBRL parser handles Q4=FY-9M, Q4=FY-sum(Q1..Q3), restatements, splits, and avail clamping."""
    # Splits: 4:1 on 2020-08-31, 10:1 on 2024-06-10
    splits = [("2020-08-31", 4.0), ("2024-06-10", 10.0)]

    # Build FY2020 (2020-01-01..2020-12-31) with Q1, Q2, Q3, 9M, FY filed pre-2024 split
    # Suppose true post-all-splits EPS is $0.25/quarter ($1.00/yr).
    # In 2020 before 2020-08-31 4:1 split, raw reported EPS = $0.25 * 40 = $10.00/quarter.
    # After 2020-08-31 split (before 2024-06-10 10:1 split), raw reported EPS = $0.25 * 10 = $2.50/quarter.
    entries = [
        # Q1 2020 filed 2020-05-01 (pre both splits: factor 40) -> 10.0 / 40 = 0.25
        {"start": "2020-01-01", "end": "2020-03-31", "filed": "2020-05-01", "form": "10-Q", "val": 10.0},
        # Comparative Q1 2020 filed in 2021-05-01 (post 4:1 split, pre 10:1 split: factor 10) -> 2.5 / 10 = 0.25
        {"start": "2020-01-01", "end": "2020-03-31", "filed": "2021-05-01", "form": "10-Q", "val": 2.5},
        # Q2 2020 filed 2020-08-01 (pre both splits: factor 40) -> 10.0 / 40 = 0.25
        {"start": "2020-04-01", "end": "2020-06-30", "filed": "2020-08-01", "form": "10-Q", "val": 10.0},
        # Q3 2020 filed 2020-11-01 (post 4:1, pre 10:1: factor 10) -> 2.5 / 10 = 0.25
        {"start": "2020-07-01", "end": "2020-09-30", "filed": "2020-11-01", "form": "10-Q", "val": 2.5},
        # 9M 2020 filed 2020-11-01 -> 7.5 / 10 = 0.75
        {"start": "2020-01-01", "end": "2020-09-30", "filed": "2020-11-01", "form": "10-Q", "val": 7.5},
        # FY 2020 filed 2021-02-20 -> 10.0 / 10 = 1.00 => Q4 = 1.00 - 0.75 = 0.25
        {"start": "2020-01-01", "end": "2020-12-31", "filed": "2021-02-20", "form": "10-K", "val": 10.0},
        # Comparative FY2019 Q1..Q4 only disclosed in 2021-02-20 (>120d after end => avail clamped to end+45d)
        # Also test Q4 = FY - (Q1+Q2+Q3) when 9M is absent!
        {"start": "2019-01-01", "end": "2019-03-31", "filed": "2021-02-20", "form": "10-K", "val": 2.0},
        {"start": "2019-04-01", "end": "2019-06-30", "filed": "2021-02-20", "form": "10-K", "val": 2.0},
        {"start": "2019-07-01", "end": "2019-09-30", "filed": "2021-02-20", "form": "10-K", "val": 2.0},
        {"start": "2019-01-01", "end": "2019-12-31", "filed": "2021-02-20", "form": "10-K", "val": 8.0},
        # Q1 2021 restatement via 10-Q/A
        {"start": "2021-01-01", "end": "2021-03-31", "filed": "2021-05-01", "form": "10-Q", "val": 2.5},
        {"start": "2021-01-01", "end": "2021-03-31", "filed": "2021-05-15", "form": "10-Q/A", "val": 3.5},
    ]
    facts = {
        "facts": {
            "us-gaap": {
                "EarningsPerShareDiluted": {
                    "units": {"USD/shares": entries}
                }
            }
        }
    }

    anchors, is_foreign = extract_ttm_eps_anchors_from_companyfacts(facts, splits=splits)
    assert is_foreign is False
    by_avail = dict(anchors)
    # 2019 Q4 derived via FY - (Q1+Q2+Q3) = 0.8 - 0.6 = 0.2, TTM = 0.8, clamped avail = 2019-12-31 + 45d = 2020-02-14
    assert by_avail["2020-02-14"] == pytest.approx(0.8, abs=1e-4)
    # 2020 Q4 TTM = 0.25*4 = 1.00, available on 2021-02-20
    assert by_avail["2021-02-20"] == pytest.approx(1.0, abs=1e-4)
    # 2021 Q1 restated to 0.35 (from 0.25) -> TTM = 0.25*3 + 0.35 = 1.10, available on first_filed 2021-05-01
    assert by_avail["2021-05-01"] == pytest.approx(1.10, abs=1e-4)

    # Combine with daily closes (including a spike >300x that should be excluded)
    closes = {
        "2020-02-14": 20.0,   # PE = 20 / 0.8 = 25.0
        "2021-02-22": 30.0,   # PE = 30 / 1.0 = 30.0
        "2021-05-03": 44.0,   # PE = 44 / 1.10 = 40.0
        "2021-05-04": 400.0,  # PE = 400 / 1.10 = 363.6 > 300 -> filtered out
    }
    daily = build_daily_pe_from_eps_anchors(anchors, closes, start_date="2020-01-01")
    assert daily == [
        ("2020-02-14", 25.0),
        ("2021-02-22", 30.0),
        ("2021-05-03", 40.0),
    ]

    # Foreign 20-F IFRS check
    ifrs_facts = {
        "facts": {
            "ifrs-full": {
                "DilutedEarningsLossPerShare": {
                    "units": {
                        "USD/shares": [
                            {"start": "2023-01-01", "end": "2023-12-31", "filed": "2024-03-01", "form": "20-F", "val": 5.0}
                        ]
                    }
                }
            }
        }
    }
    ifrs_anchors, ifrs_flag = extract_ttm_eps_anchors_from_companyfacts(ifrs_facts)
    assert ifrs_flag is True
    assert ifrs_anchors == [("2024-03-01", 5.0)]


def test_yfinance_history_frames_parser():
    """Verify yfinance quarterly + annual income_stmt TTM EPS anchor extraction."""
    q_df = pd.DataFrame(
        {
            pd.Timestamp("2025-12-31"): [1.5],
            pd.Timestamp("2025-09-30"): [1.2],
            pd.Timestamp("2025-06-30"): [1.1],
            pd.Timestamp("2025-03-31"): [1.0],
        },
        index=["Diluted EPS"],
    )
    a_df = pd.DataFrame(
        {
            pd.Timestamp("2024-12-31"): [3.2],
            pd.Timestamp("2023-12-31"): [2.1],
        },
        index=["Diluted EPS"],
    )
    anchors = extract_ttm_eps_from_yfinance_frames(q_df, a_df)
    # 2023 annual -> 2024-02-14 (2.1)
    # 2024 annual -> 2025-02-14 (3.2)
    # 2025 Q1..Q4 TTM -> 2026-02-14 (4.8)
    assert anchors == [
        ("2024-02-14", 2.1),
        ("2025-02-14", 3.2),
        ("2026-02-14", 4.8),
    ]


def test_equity_pe_negative_eps_and_extreme_pe_semantics(tmp_path):
    """Loss-making latest date must surface '亏损' rather than falling back to old profitable PE."""
    db_path = tmp_path / "eq_edge.db"
    store = ValuationStore(db_path)
    inst = store.upsert_instrument("LOSSCO", name="亏损测试", kind="equity")
    inst_id = int(inst["id"])

    # Historical SEC EDGAR profitable day, older computed profitable day, then newer loss-making day
    store.save_raw_series(inst_id, "pe_ttm", [("2026-11-18", 24.0)], source="sec_edgar")
    store.save_equity_snapshot(
        inst_id,
        "2026-11-19",
        EquitySnapshot(price=100.0, eps_ttm=4.0, eps_fwd=5.0, source="equity+yfinance"),
    )
    store.save_equity_snapshot(
        inst_id,
        "2026-11-20",
        EquitySnapshot(price=90.0, eps_ttm=-2.0, eps_fwd=0.1, source="equity+yfinance"),
    )
    run_compute_indicators(store, "LOSSCO")

    snap = get_snapshot_payload(store, "LOSSCO")
    ttm_latest = snap["indicators"]["pe_ttm"]["latest"]
    fwd_latest = snap["indicators"]["pe_fwd"]["latest"]

    assert ttm_latest["date"] == "2026-11-20"
    assert ttm_latest["value"] is None
    assert ttm_latest["meta"]["negative_eps"] is True

    # 90 / 0.1 = 900x > 300x -> extreme_pe sentinel
    assert fwd_latest["date"] == "2026-11-20"
    assert fwd_latest["value"] is None
    assert fwd_latest["meta"]["extreme_pe"] is True
    assert fwd_latest["meta"]["raw_pe"] == 900.0


def test_equity_refresh_reuses_constituent_snapshots(tmp_path):
    """Refreshing NDX first allows equity refresh to reuse NVDA & AAPL without re-fetching yfinance."""
    db_path = tmp_path / "reuse.db"
    store = ValuationStore(db_path)

    ndx_fund = _RecordingFundamentals(
        {
            "NVDA": FundamentalItem("NVDA", price=140.0, eps_ttm=2.8, eps_fwd=4.0),
            "AAPL": FundamentalItem("AAPL", price=220.0, eps_ttm=6.5, eps_fwd=7.5),
        }
    )
    run_daily_refresh(
        store,
        instrument_symbol="NDX",
        holdings_source=_StubHoldings(
            "2026-11-20",
            [HoldingItem("NVDA", 0.5), HoldingItem("AAPL", 0.5)],
        ),
        fundamentals_source=ndx_fund,
    )

    store.upsert_instrument("AMD", name="超威半导体", kind="equity")
    eq_fund = _RecordingFundamentals(
        {
            "AMD": FundamentalItem("AMD", price=150.0, eps_ttm=3.0, eps_fwd=5.0, market_cap=240e9),
        }
    )
    res = run_equity_refresh(
        store,
        symbols=["NVDA", "AAPL", "AMD"],
        fundamentals_source=eq_fund,
        reuse_constituents=True,
        as_of_date="2026-11-20",
    )
    assert res["status"] == "success"
    assert res["reused"] == 2
    assert res["fetched"] == 1
    assert eq_fund.requested_batches == [["AMD"]]

    nvda_snap = get_snapshot_payload(store, "NVDA")
    assert nvda_snap["indicators"]["pe_ttm"]["latest"]["value"] == pytest.approx(50.0, rel=1e-4)
    assert nvda_snap["indicators"]["pe_fwd"]["latest"]["value"] == pytest.approx(35.0, rel=1e-4)


def test_batch_csv_import_watchlist_crud_and_compact_export(tmp_path):
    """Verify multi-company CSV import, CRUD APIs, watchlist overview, and >5y weekly static downsampling."""
    db_path = tmp_path / "api_eq.db"
    app = create_app(db_path=db_path, enable_scheduler=False, auto_seed=False, frontend_dist_dir=None)
    client = TestClient(app)

    # 1. Watchlist CRUD
    add_res = client.post(
        "/api/instruments",
        json={
            "symbol": "AMD",
            "name": "超威半导体",
            "sector": "半导体",
            "group_name": "AI 芯片",
            "backfill": False,
            "refresh": False,
        },
    )
    assert add_res.status_code == 200
    assert add_res.json()["instruments"][0]["symbol"] == "AMD"

    patch_res = client.patch("/api/instruments/AMD", json={"sector": "半导体与 GPU"})
    assert patch_res.status_code == 200
    assert patch_res.json()["sector"] == "半导体与 GPU"

    # Cannot delete index
    assert client.delete("/api/instruments/NDX").status_code == 400

    # 2. Multi-ticker batch CSV import (creates ORCL automatically + updates NVDA)
    # Generate 7 years of weekday data for NVDA so >5y downsampling triggers in static export
    rows = ["ticker,date,pe_ttm,pe_fwd"]
    d = date(2019, 11, 20)
    end = date(2026, 11, 20)
    idx = 0
    while d <= end:
        if d.weekday() < 5:
            rows.append(f"NVDA,{d.isoformat()},{round(35.0 + (idx % 20) * 0.5, 2)},{round(25.0 + (idx % 15) * 0.4, 2)}")
            idx += 1
        d += timedelta(days=1)
    rows.append("ORCL,2026-11-19,28.5,24.0")
    rows.append("ORCL,2026-11-20,29.0,24.5")

    imp_res = client.post(
        "/api/import/csv",
        json={"csv_text": "\n".join(rows), "instrument": None, "source": "gurufocus_csv"},
    )
    assert imp_res.status_code == 200
    imp_body = imp_res.json()
    assert "NVDA" in imp_body["imported_by_symbol"]
    assert "ORCL" in imp_body["imported_by_symbol"]

    # 3. Watchlist overview API
    overview = client.get("/api/watchlist/overview?kind=equity").json()
    by_sym = {item["instrument"]["symbol"]: item for item in overview["items"]}
    assert "NVDA" in by_sym
    assert "ORCL" in by_sym
    assert "AMD" in by_sym
    assert by_sym["ORCL"]["pe_ttm"]["value"] == 29.0
    assert by_sym["ORCL"]["pe_fwd"]["value"] == 24.5
    assert by_sym["NVDA"]["percentiles"]["pe_ttm_5y"]["value"] is not None

    # Delete AMD
    assert client.delete("/api/instruments/AMD").status_code == 200
    overview_after = client.get("/api/watchlist/overview?kind=equity").json()
    assert all(i["instrument"]["symbol"] != "AMD" for i in overview_after["items"])

    # 4. Compact static export embeds watchlist_overview & downsamples >5y NVDA history
    store = app.state.store
    # Seed NDX with 7 years too to confirm index stays daily (not downsampled)
    ndx_id = int(store.get_instrument("NDX")["id"])
    ndx_rows = []
    d = date(2019, 11, 20)
    while d <= end:
        if d.weekday() < 5:
            ndx_rows.append((d.isoformat(), 28.0))
        d += timedelta(days=1)
    store.save_raw_series(ndx_id, "pe_ttm", ndx_rows, source="seed_csv")
    run_compute_indicators(store, "NDX")

    bundle = build_static_snapshot_bundle(store, instrument_symbol="NDX", compact_ranges=True)
    assert "watchlist_overview" in bundle
    assert any(i["instrument"]["symbol"] == "NVDA" for i in bundle["watchlist_overview"]["items"])

    ndx_len = len(bundle["series_by_instrument"]["NDX"]["pe_ttm"]["all"]["series"])
    nvda_pts = bundle["series_by_instrument"]["NVDA"]["pe_ttm"]["all"]["series"]
    # NDX keeps full ~1827 daily rows; NVDA keeps 5y daily (~1305) + 2y weekly (~105) < ndx_len
    assert ndx_len > 1700
    assert len(nvda_pts) < ndx_len - 300
    assert len(nvda_pts) > 1300
    # Stripped empty meta in equity compact points
    assert "meta" not in nvda_pts[0]

    # Render standalone HTML
    tpl = tmp_path / "template.html"
    tpl.write_text(
        "<!doctype html><html><head><!--VALUATION_SNAPSHOT_INJECT--></head><body></body></html>",
        encoding="utf-8",
    )
    out = tmp_path / "out.html"
    export_static_html(store, template_path=tpl, output_path=out)
    html = out.read_text(encoding="utf-8")
    m = re.search(r"window\.__VALUATION_SNAPSHOT__=(\{.*\});</script>", html)
    assert m is not None
    embedded = json.loads(m.group(1))
    assert "watchlist_overview" in embedded


def test_cli_csv_import_auto_multi_ticker_recomputes_without_auto_instrument(tmp_path):
    """Verify run_csv_import(..., instrument_symbol='AUTO') imports and recomputes all tickers without creating 'AUTO'."""
    store = ValuationStore(tmp_path / "cli_auto.db")
    csv_path = tmp_path / "batch.csv"
    csv_path.write_text(
        "ticker,date,pe_ttm,pe_fwd\n"
        "MSFT,2026-11-19,33.0,29.0\n"
        "MSFT,2026-11-20,34.0,30.0\n"
        "ADBE,2026-11-20,25.5,21.0\n",
        encoding="utf-8",
    )

    res = run_csv_import(store, csv_path, instrument_symbol="AUTO", source="csv_import")
    assert res["imported"] == {"pe_ttm": 3, "pe_fwd": 3}
    assert "MSFT" in res["imported_by_symbol"]
    assert "ADBE" in res["imported_by_symbol"]
    assert store.get_instrument("AUTO") is None

    msft_snap = get_snapshot_payload(store, "MSFT")
    adbe_snap = get_snapshot_payload(store, "ADBE")
    assert msft_snap["indicators"]["pe_ttm"]["latest"]["value"] == 34.0
    assert adbe_snap["indicators"]["pe_fwd"]["latest"]["value"] == 21.0


def test_compact_columnar_bundle_deduplication_and_export_static_site(tmp_path):
    """Verify columnar compact encoding, primary deduplication, and split static site export."""
    store = ValuationStore(tmp_path / "site.db")
    ndx_id = int(store.get_instrument("NDX")["id"])
    nvda_id = int(store.get_instrument("NVDA")["id"])

    from app.models import ConstituentQuote

    store.save_raw_series(
        ndx_id,
        "pe_ttm",
        [("2026-11-19", 30.1), ("2026-11-20", 30.5)],
        source="seed_csv",
    )
    store.save_constituent_snapshots(
        ndx_id,
        "2026-11-21",
        [
            ConstituentQuote("NVDA", 0.90, price=100.0, eps_ttm=4.0, eps_fwd=5.0),
            ConstituentQuote("MISS", 0.10, price=None, eps_ttm=None, eps_fwd=None),
        ],
    )
    store.save_raw_series(
        nvda_id,
        "pe_ttm",
        [("2026-11-19", 40.0), ("2026-11-20", 41.0)],
        source="sec_edgar",
    )
    run_compute_indicators(store, "NDX")
    run_compute_indicators(store, "NVDA")

    bundle = build_static_snapshot_bundle(
        store,
        instrument_symbol="NDX",
        compact_ranges=True,
        compact_points=True,
        deduplicate_primary=True,
    )
    # Primary NDX deduplicated from secondary maps
    assert "NDX" not in bundle["snapshots"]
    assert "NDX" not in bundle["series_by_instrument"]
    assert "NDX" not in bundle["sources_by_instrument"]

    # Columnar encoding preserves dates, values, sources, and is_incomplete flag
    ndx_ttm = bundle["series"]["pe_ttm"]["all"]
    assert "series" not in ndx_ttm
    compact = ndx_ttm["compact"]
    assert compact["s"] == ["seed_csv", "computed"]
    assert compact["d"] == [
        ["2026-11-19", 30.1, 0],
        ["2026-11-20", 30.5, 0],
        ["2026-11-21", 25.0, 1, 1],
    ]

    # Mock built frontend dist dir and run export_static_site
    fake_dist = tmp_path / "dist"
    (fake_dist / "assets").mkdir(parents=True)
    (fake_dist / "assets" / "app.js").write_text("console.log('ok');", encoding="utf-8")
    (fake_dist / "index.html").write_text(
        "<!doctype html><html><head><title>Test</title></head><body></body></html>",
        encoding="utf-8",
    )

    out_site = tmp_path / "_site"
    export_static_site(store, output_dir=out_site, frontend_dist_dir=fake_dist)

    site_html = (out_site / "index.html").read_text(encoding="utf-8")
    assert 'window.__VALUATION_SITE__={"dataBase":"./data"};' in site_html
    assert (out_site / "assets" / "app.js").exists()

    boot = json.loads((out_site / "data" / "bootstrap.json").read_text(encoding="utf-8"))
    assert boot["mode"] == "static_site"
    assert "NDX" in boot["index_snapshots"]
    assert "NDX" in boot["index_sources"]
    assert "watchlist_overview" not in boot
    assert "series" not in boot

    wl = json.loads((out_site / "data" / "watchlist.json").read_text(encoding="utf-8"))
    assert wl["kind"] == "equity"
    assert wl["count"] == len(DEFAULT_EQUITIES)

    ndx_file = json.loads((out_site / "data" / "instruments" / "NDX.json").read_text(encoding="utf-8"))
    assert ndx_file["kind"] == "index"
    assert "compact" in ndx_file["series"]["pe_ttm"]["all"]

    nvda_file = json.loads((out_site / "data" / "instruments" / "NVDA.json").read_text(encoding="utf-8"))
    assert nvda_file["kind"] == "equity"
    assert nvda_file["snapshot"]["indicators"]["pe_ttm"]["latest"]["value"] == 41.0
    assert "compact" in nvda_file["series"]["pe_ttm"]["all"]
