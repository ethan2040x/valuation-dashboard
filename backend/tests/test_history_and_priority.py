"""Tests for 20-year statistics, NYSE calendar, historical sources, and priority profiles."""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.export import build_static_snapshot_bundle, export_static_html
from app.indicators.base import rolling_percentile
from app.jobs import (
    bootstrap_seed_data_if_empty,
    run_backfill_history,
    run_compute_indicators,
    run_csv_import,
)
from app.models import ConstituentQuote, IndicatorPoint
from app.services import get_snapshot_payload
from app.sources.history_sources import (
    HistoricalSeriesSource,
    SiblisHistoricalSource,
    expand_anchors_to_daily,
    get_historical_sources,
    parse_multpl_pe_html,
    parse_shiller_csv,
    parse_siblis_csv,
    parse_siblis_json,
    us_nyse_trading_days,
)
from app.store import ValuationStore


def test_nyse_trading_calendar_excludes_holidays_and_weekends():
    days = set(us_nyse_trading_days("2020-01-01", "2024-12-31"))
    # Weekdays that ARE trading days
    assert "2020-01-02" in days
    assert "2021-06-18" in days  # Juneteenth became NYSE holiday starting 2022
    assert "2021-12-31" in days  # NYD 2022 Sat -> NYSE stays open Dec 31

    # Weekends
    assert "2024-01-06" not in days
    assert "2024-01-07" not in days

    # Standard NYSE holidays
    assert "2024-01-01" not in days  # New Year's Day
    assert "2024-01-15" not in days  # MLK Day
    assert "2024-02-19" not in days  # Presidents' Day
    assert "2024-03-29" not in days  # Good Friday 2024
    assert "2024-05-27" not in days  # Memorial Day
    assert "2022-06-20" not in days  # Juneteenth observed 2022
    assert "2024-06-19" not in days  # Juneteenth 2024
    assert "2024-07-04" not in days  # Independence Day
    assert "2024-09-02" not in days  # Labor Day
    assert "2024-11-28" not in days  # Thanksgiving
    assert "2024-12-25" not in days  # Christmas


def test_parse_multpl_shiller_siblis_and_daily_expansion():
    multpl_html = """
    <html><body>
      <table id="datatable">
        <tr><th>Date</th><th>Value</th></tr>
        <tr><td>Mar 1, 2024</td><td>27.50 Est. †</td></tr>
        <tr><td>Feb 1, 2024</td><td>26.80</td></tr>
        <tr><td>Jan 1, 2024</td><td>25.40</td></tr>
      </table>
    </body></html>
    """
    multpl_pts = parse_multpl_pe_html(multpl_html)
    assert multpl_pts == [
        ("2024-01-01", 25.4),
        ("2024-02-01", 26.8),
        ("2024-03-01", 27.5),
    ]

    shiller_csv = (
        "Date,SP500,Dividend,Earnings,PE10\n"
        "2024-01-01,4800.0,70.0,200.0,32.1\n"
        "2024-02-01,5000.0,71.0,200.0,33.0\n"
        "2024-03-01,5200.0,72.0,200.0,34.2\n"
    )
    shiller_pts = parse_shiller_csv(shiller_csv)
    assert shiller_pts == [
        ("2024-01-01", 24.0),
        ("2024-02-01", 25.0),
        ("2024-03-01", 26.0),
    ]

    siblis_csv = (
        "ticker,trading_day (EOD),pe_trailing\n"
        "NDX,2024-01-31,30.12\n"
        "NDX,2024-02-29,31.45\n"
    )
    assert parse_siblis_csv(siblis_csv) == [
        ("2024-01-31", 30.12),
        ("2024-02-29", 31.45),
    ]

    siblis_json = {
        "ticker": "NDX",
        "ratio": "pe_forward",
        "data": [
            {"trading_day (EOD)": "2024-01-31", "value": 25.5},
            {"trading_day (EOD)": "2024-02-29", "value": 26.2},
        ],
    }
    assert parse_siblis_json(siblis_json) == [
        ("2024-01-31", 25.5),
        ("2024-02-29", 26.2),
    ]

    # Daily expansion with price series uses implied EPS * daily price
    prices = {
        "2024-01-02": 4800.0,
        "2024-01-16": 4900.0,
        "2024-02-01": 5000.0,
        "2024-02-15": 5100.0,
        "2024-03-01": 5200.0,
    }
    expanded_with_px = expand_anchors_to_daily(shiller_pts, price_series=prices)
    assert [d for d, _ in expanded_with_px] == sorted(prices.keys())
    assert expanded_with_px[0] == ("2024-01-02", 24.0)
    assert expanded_with_px[-1] == ("2024-03-01", 26.0)
    assert 24.0 < expanded_with_px[1][1] < 25.0

    # Offline fallback without price series interpolates across NYSE trading days
    expanded_offline = expand_anchors_to_daily(shiller_pts, price_series=None)
    assert len(expanded_offline) > 35
    assert "2024-01-15" not in dict(expanded_offline)  # MLK Day excluded
    assert "2024-02-19" not in dict(expanded_offline)  # Presidents' Day excluded
    assert expanded_offline[0][0] == "2024-01-02"
    assert expanded_offline[-1] == ("2024-03-01", 26.0)


def test_default_free_first_priority_and_switching_to_external_first(tmp_path):
    db_path = tmp_path / "priority.db"
    store = ValuationStore(db_path)
    spy = store.get_or_create_instrument("SPY")
    spy_id = int(spy["id"])

    # Default priority should be free_first
    profile, priority = store.get_source_priority()
    assert profile == "free_first"
    assert priority.index("multpl") < priority.index("csv_import")
    assert priority.index("csv_import") < priority.index("seed_csv")

    # Seed overlapping dates across seed_csv, csv_import, multpl, and computed
    store.save_raw_series(
        spy_id,
        "pe_ttm",
        [("2024-01-02", 18.0), ("2024-01-03", 18.5), ("2024-01-04", 19.0)],
        source="seed_csv",
    )
    store.save_raw_series(
        spy_id,
        "pe_ttm",
        [("2024-01-02", 22.0), ("2024-01-03", 22.5)],
        source="csv_import",
    )
    store.save_raw_series(
        spy_id,
        "pe_ttm",
        [("2024-01-02", 20.0)],
        source="multpl",
    )
    store.save_constituent_snapshots(
        spy_id,
        "2024-01-04",
        [ConstituentQuote(ticker="AAPL", weight=1.0, price=200.0, eps_ttm=8.0, eps_fwd=10.0, source="spy+yfinance")],
    )
    run_compute_indicators(store, instrument_symbol="SPY")

    app = create_app(db_path=db_path, enable_scheduler=False, auto_seed=False, frontend_dist_dir=None)
    client = TestClient(app)

    # Under default free_first: 2024-01-02 -> multpl (20.0), 2024-01-03 -> csv_import (22.5), 2024-01-04 -> computed (25.0)
    s1 = client.get("/api/indicators/pe_ttm/series?instrument=SPY&range=all").json()
    by_date_1 = {pt["date"]: pt for pt in s1["series"]}
    assert by_date_1["2024-01-02"]["source"] == "multpl"
    assert by_date_1["2024-01-02"]["value"] == 20.0
    assert by_date_1["2024-01-03"]["source"] == "csv_import"
    assert by_date_1["2024-01-04"]["source"] == "computed"
    assert by_date_1["2024-01-04"]["value"] == 25.0

    # Switch to external_first via API -> csv_import overrides multpl on 2024-01-02, computed still #1
    resp = client.put(
        "/api/sources/priority",
        json={"profile": "external_first", "instrument": "SPY"},
    )
    assert resp.status_code == 200
    sources_body = resp.json()
    assert sources_body["profile"] == "external_first"

    s2 = client.get("/api/indicators/pe_ttm/series?instrument=SPY&range=all").json()
    by_date_2 = {pt["date"]: pt for pt in s2["series"]}
    assert by_date_2["2024-01-02"]["source"] == "csv_import"
    assert by_date_2["2024-01-02"]["value"] == 22.0
    assert by_date_2["2024-01-04"]["source"] == "computed"

    # Custom priority order putting seed_csv above csv_import and multpl
    resp_custom = client.put(
        "/api/sources/priority",
        json={
            "priority": ["computed", "seed_csv", "csv_import", "multpl"],
            "instrument": "SPY",
        },
    )
    assert resp_custom.status_code == 200
    assert resp_custom.json()["profile"] == "custom"

    s3 = client.get("/api/indicators/pe_ttm/series?instrument=SPY&range=all").json()
    by_date_3 = {pt["date"]: pt for pt in s3["series"]}
    assert by_date_3["2024-01-02"]["source"] == "seed_csv"
    assert by_date_3["2024-01-02"]["value"] == 18.0


def test_backfill_history_defaults_to_free_sources_only(tmp_path):
    class MockFreeSource(HistoricalSeriesSource):
        source_id = "multpl"
        tier = "free"
        supported_instruments = ("SPY",)
        supported_series = ("pe_ttm",)

        def __init__(self) -> None:
            self.calls = 0

        def fetch_history(self, instrument_symbol: str, start_date: str, end_date: str | None = None):
            self.calls += 1
            return {"pe_ttm": [("2020-01-02", 24.5), ("2020-01-03", 24.6)]}

    class MockFailingFreeSource(HistoricalSeriesSource):
        source_id = "siblis_free"
        tier = "free"
        supported_instruments = ("SPY",)
        supported_series = ("pe_ttm", "pe_fwd")

        def fetch_history(self, instrument_symbol: str, start_date: str, end_date: str | None = None):
            raise RuntimeError("Upstream 429 rate limited")

    class MockPaidSource(HistoricalSeriesSource):
        source_id = "siblis_paid"
        tier = "paid"
        supported_instruments = ("SPY",)
        supported_series = ("pe_ttm", "pe_fwd")

        def __init__(self) -> None:
            self.calls = 0

        def is_configured(self) -> bool:
            return True

        def fetch_history(self, instrument_symbol: str, start_date: str, end_date: str | None = None):
            self.calls += 1
            return {"pe_fwd": [("2020-01-02", 19.1)]}

    store = ValuationStore(tmp_path / "backfill.db")
    free_src = MockFreeSource()
    failing_free = MockFailingFreeSource()
    paid_src = MockPaidSource()

    # Default include_paid=False must skip paid source and isolate failing free source
    res_free = run_backfill_history(
        store,
        instrument_symbol="SPY",
        years=20,
        adapters=[free_src, failing_free, paid_src],
    )
    assert res_free["include_paid"] is False
    assert res_free["status"] == "partial"
    assert free_src.calls == 1
    assert paid_src.calls == 0
    assert res_free["total_saved"] == 2
    assert "siblis_paid" not in res_free["sources"]
    assert res_free["sources"]["siblis_free"]["status"] == "failed"

    # Explicit include_paid=True calls configured paid adapter
    res_paid = run_backfill_history(
        store,
        instrument_symbol="SPY",
        years=20,
        include_paid=True,
        adapters=[free_src, paid_src],
    )
    assert res_paid["status"] == "success"
    assert paid_src.calls == 1
    assert res_paid["sources"]["siblis_paid"]["saved"] == {"pe_fwd": 1}


def test_csv_import_supports_platform_aliases_and_series_id(tmp_path):
    store = ValuationStore(tmp_path / "csv_aliases.db")

    # Siblis wide export
    siblis_path = tmp_path / "siblis.csv"
    siblis_path.write_text(
        "ticker,trading_day (EOD),pe_trailing,pe_forward\n"
        "NDX,2024-01-02,31.20,25.10\n"
        "NDX,2024-01-03,31.50,25.30\n",
        encoding="utf-8",
    )
    cnt1 = run_csv_import(store, siblis_path, instrument_symbol="NDX", source="siblis_free")
    assert cnt1["imported"] == {"pe_ttm": 2, "pe_fwd": 2}

    # Multpl / MacroMicro 2-column export with Est. / † and explicit series_id
    two_col_path = tmp_path / "multpl.csv"
    two_col_path.write_text(
        "Date,Value\n"
        '"Jan 2, 2024",26.45 Est. †\n'
        '"Jan 3, 2024",26.80x\n',
        encoding="utf-8",
    )
    cnt2 = run_csv_import(
        store,
        two_col_path,
        instrument_symbol="SPY",
        source="macromicro_csv",
        series_id="pe_fwd",
    )
    assert cnt2["imported"] == {"pe_fwd": 2}
    rows = store.get_raw_series(int(store.get_instrument("SPY")["id"]), "pe_fwd", source="macromicro_csv")
    assert [(r["date"], r["value"]) for r in rows] == [
        ("2024-01-02", 26.45),
        ("2024-01-03", 26.8),
    ]

    # Chinese headers
    cn_path = tmp_path / "cn.csv"
    cn_path.write_text(
        "日期,滚动PE,前瞻PE\n"
        "2024/01/04,32.10,26.00\n",
        encoding="utf-8",
    )
    cnt3 = run_csv_import(store, cn_path, instrument_symbol="NDX", source="wind_csv")
    assert cnt3["imported"] == {"pe_ttm": 1, "pe_fwd": 1}


def test_20y_percentile_window_and_range_api_and_compact_export(tmp_path):
    # 1. rolling_percentile with 21 years of monthly data (full 20y window) vs 12 years (partial 20y window)
    pts_21y: list[IndicatorPoint] = []
    start = date(2005, 1, 15)
    for m in range(253):  # 21 years + 1 month
        yr = start.year + (start.month - 1 + m) // 12
        mo = (start.month - 1 + m) % 12 + 1
        val = 25.0 if m == 252 else 20.0 + (m % 11)
        pts_21y.append(IndicatorPoint(date=date(yr, mo, 15).isoformat(), value=val, source="seed_csv"))

    pct_20y = rolling_percentile(pts_21y, window_years=20)
    last_20y = pct_20y[-1]
    assert last_20y.value is not None
    assert last_20y.meta["window_years"] == 20
    assert last_20y.meta["partial_window"] is False
    assert last_20y.meta["insufficient_history"] is False

    # Partial 12y data under 20y window -> displays normally with "基于 12.0 年数据"
    pts_12y = pts_21y[:145]
    pct_12y_under_20 = rolling_percentile(pts_12y, window_years=20)
    assert pct_12y_under_20[-1].value is not None
    assert pct_12y_under_20[-1].meta["partial_window"] is True
    assert "基于" in pct_12y_under_20[-1].meta["note"]

    # 2. API range=20y and compact static HTML bundle
    db_path = tmp_path / "range20y.db"
    csv_path = tmp_path / "hist21y.csv"
    lines = ["date,pe_ttm,pe_fwd"]
    for pt in pts_21y:
        lines.append(f"{pt.date},{pt.value},{round(pt.value * 0.85, 2)}")
    csv_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    store = ValuationStore(db_path)
    run_csv_import(store, csv_path, instrument_symbol="NDX", source="seed_csv")

    app = create_app(db_path=db_path, enable_scheduler=False, auto_seed=False, frontend_dist_dir=None)
    client = TestClient(app)

    r20 = client.get("/api/indicators/pe_ttm/series?instrument=NDX&range=20y")
    assert r20.status_code == 200
    body20 = r20.json()
    assert body20["range"] == "20y"
    assert body20["series"][0]["date"] >= "2006-01-15"
    assert body20["series"][-1]["date"] == "2026-01-15"

    snap = client.get("/api/snapshot?instrument=NDX").json()
    variants = snap["indicators"]["pe_percentile"]["variants"]
    win_years_set = {(v["params"]["base"], v["params"]["window_years"]) for v in variants}
    assert ("pe_ttm", 20) in win_years_set
    assert ("pe_fwd", 20) in win_years_set

    sources_api = client.get("/api/sources?instrument=NDX").json()
    assert sources_api["profile"] == "free_first"
    assert any(s["id"] == "seed_csv" and s["active_in_db"] for s in sources_api["sources"])

    # Compact export embeds "all" + sources_by_instrument
    compact_bundle = build_static_snapshot_bundle(store, instrument_symbol="NDX", compact_ranges=True)
    assert list(compact_bundle["series"]["pe_ttm"].keys()) == ["all"]
    assert "NDX" in compact_bundle["sources_by_instrument"]

    tpl = tmp_path / "template.html"
    tpl.write_text(
        "<!doctype html><html><head><!--VALUATION_SNAPSHOT_INJECT--></head><body></body></html>",
        encoding="utf-8",
    )
    out = tmp_path / "compact.html"
    export_static_html(store, instrument_symbol="NDX", template_path=tpl, output_path=out)
    html = out.read_text(encoding="utf-8")
    match = re.search(r"window\.__VALUATION_SNAPSHOT__=(\{.*\});</script>", html)
    assert match is not None
    embedded = json.loads(match.group(1))
    assert list(embedded["series"]["pe_ttm"].keys()) == ["all"]
    assert embedded["sources"]["profile"] == "free_first"


def test_seed_upgrade_extends_10y_to_20y_without_losing_live_or_csv_data(tmp_path):
    seed_dir = tmp_path / "seed"
    seed_dir.mkdir()
    seed_csv = seed_dir / "ndx_pe_history.csv"
    # New 20y seed starting 2006-01-03 (excluding holidays)
    seed_csv.write_text(
        "date,pe_ttm,pe_fwd\n"
        "2006-01-03,29.10,22.00\n"
        "2016-01-04,21.80,18.20\n"
        "2026-11-20,25.60,17.60\n",
        encoding="utf-8",
    )

    store = ValuationStore(tmp_path / "upgrade.db")
    inst = store.get_or_create_instrument("NDX")
    inst_id = int(inst["id"])

    # Simulate old 10y seed (including a US holiday row 2016-01-18) + imported CSV + live constituent snapshot
    store.save_raw_series(
        inst_id,
        "pe_ttm",
        [("2016-01-04", 21.80), ("2016-01-18", 21.50), ("2026-11-20", 25.60)],
        source="seed_csv",
    )
    store.save_raw_series(
        inst_id,
        "pe_fwd",
        [("2016-01-04", 18.20), ("2016-01-18", 18.00), ("2026-11-20", 17.60)],
        source="seed_csv",
    )
    store.save_raw_series(
        inst_id,
        "pe_ttm",
        [("2026-11-19", 25.99)],
        source="csv_import",
    )
    store.save_constituent_snapshots(
        inst_id,
        "2026-11-22",
        [ConstituentQuote(ticker="NVDA", weight=1.0, price=150.0, eps_ttm=5.0, eps_fwd=6.0)],
    )

    upgraded = bootstrap_seed_data_if_empty(store, seed_dir=seed_dir, instrument_symbol="NDX")
    assert upgraded is True

    # seed_csv upgraded to 2006-01-03 and old holiday row removed
    seed_ttm = store.get_raw_series(inst_id, "pe_ttm", source="seed_csv")
    assert [r["date"] for r in seed_ttm] == ["2006-01-03", "2016-01-04", "2026-11-20"]

    # csv_import and live constituent snapshot remained completely intact
    csv_rows = store.get_raw_series(inst_id, "pe_ttm", source="csv_import")
    assert len(csv_rows) == 1 and csv_rows[0]["date"] == "2026-11-19"
    computed_vals = {pt.date: pt for pt in store.get_indicator_values(inst_id, "pe_ttm", {})}
    assert "2016-01-18" not in computed_vals
    assert computed_vals["2006-01-03"].value == 29.10
    assert computed_vals["2026-11-22"].source == "computed"
    assert computed_vals["2026-11-22"].value == 30.0

    # Second run is a no-op
    assert bootstrap_seed_data_if_empty(store, seed_dir=seed_dir, instrument_symbol="NDX") is False


def test_siblis_limited_to_ndx_and_job_status_scoped_per_instrument(tmp_path):
    # 1. Siblis adapter only supports NDX (not SPY)
    assert SiblisHistoricalSource(paid=False).supports("NDX") is True
    assert SiblisHistoricalSource(paid=False).supports("SPY") is False
    assert [s.source_id for s in get_historical_sources("NDX")] == ["siblis_free"]
    assert [s.source_id for s in get_historical_sources("SPY")] == ["multpl", "shiller"]

    # 2. Per-instrument job isolation prevents NDX/SPY statuses from overwriting or leaking
    store = ValuationStore(tmp_path / "job_scope.db")
    ndx_id = int(store.get_or_create_instrument("NDX")["id"])
    spy_id = int(store.get_or_create_instrument("SPY")["id"])
    store.save_raw_series(ndx_id, "pe_ttm", [("2026-11-20", 25.0)], source="seed_csv")
    store.save_raw_series(spy_id, "pe_ttm", [("2026-11-20", 24.0)], source="seed_csv")
    run_compute_indicators(store, instrument_symbol="NDX")
    run_compute_indicators(store, instrument_symbol="SPY")

    # Record NDX jobs (success) and SPY jobs (including stale failed siblis_free for SPY)
    j1 = store.start_job_run("siblis_free", {"instrument": "NDX"})
    store.finish_job_run(j1, status="success", details={"instrument": "NDX"})
    j2 = store.start_job_run("qqq_holdings", {"instrument": "NDX"})
    store.finish_job_run(j2, status="success", details={"instrument": "NDX"})

    j3 = store.start_job_run("siblis_free", {"instrument": "SPY"})
    store.finish_job_run(j3, status="failed", error="404 Not Found", details={"instrument": "SPY"})
    j4 = store.start_job_run("multpl", {"instrument": "SPY"})
    store.finish_job_run(j4, status="success", details={"instrument": "SPY"})
    j5 = store.start_job_run("shiller", {"instrument": "SPY"})
    store.finish_job_run(j5, status="success", details={"instrument": "SPY"})
    j6 = store.start_job_run("spy_holdings", {"instrument": "SPY"})
    store.finish_job_run(j6, status="success", details={"instrument": "SPY"})

    ndx_jobs = {j["job"]: j for j in get_snapshot_payload(store, "NDX")["status"]["jobs"]}
    spy_jobs = {j["job"]: j for j in get_snapshot_payload(store, "SPY")["status"]["jobs"]}

    assert ndx_jobs["siblis_free"]["status"] == "success"
    assert "qqq_holdings" in ndx_jobs
    assert "spy_holdings" not in ndx_jobs
    assert "multpl" not in ndx_jobs
    assert "shiller" not in ndx_jobs

    assert "siblis_free" not in spy_jobs
    assert "qqq_holdings" not in spy_jobs
    assert spy_jobs["multpl"]["status"] == "success"
    assert spy_jobs["shiller"]["status"] == "success"
    assert spy_jobs["spy_holdings"]["status"] == "success"
