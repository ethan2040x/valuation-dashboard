"""Unit tests for indicator math, boundary rules, and source priority."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.indicators.base import (
    compute_weighted_pe,
    merge_with_raw_series,
    rolling_percentile,
)
from app.models import ConstituentQuote, IndicatorPoint
from app.sources.yfinance_source import _sanitize_eps


def test_weighted_pe_exact_formula():
    # Company A: weight 0.6, price 100, eps 5 => EY = 0.05
    # Company B: weight 0.4, price 50, eps 1 => EY = 0.02
    # Weighted EY = 0.6 * 0.05 + 0.4 * 0.02 = 0.03 + 0.008 = 0.038
    # Weighted PE = 1 / 0.038 = 26.3158
    quotes = [
        ConstituentQuote(ticker="AAA", weight=0.6, price=100.0, eps_ttm=5.0, eps_fwd=6.0),
        ConstituentQuote(ticker="BBB", weight=0.4, price=50.0, eps_ttm=1.0, eps_fwd=1.5),
    ]
    pe, is_incomplete, meta = compute_weighted_pe(quotes, eps_field="eps_ttm")
    assert pe == pytest.approx(1.0 / 0.038, rel=1e-4)
    assert is_incomplete is False
    assert meta["valid_count"] == 2
    assert meta["missing_weight"] == 0.0


def test_weighted_pe_includes_loss_making_companies():
    # Company A: weight 0.8, price 100, eps 5 => EY = +0.05
    # Company B (loss-making): weight 0.2, price 100, eps -5 => EY = -0.05
    # Weighted EY = 0.8 * 0.05 + 0.2 * (-0.05) = 0.04 - 0.01 = 0.03
    # Weighted PE = 1 / 0.03 = 33.3333
    quotes = [
        ConstituentQuote(ticker="PROF", weight=0.8, price=100.0, eps_ttm=5.0),
        ConstituentQuote(ticker="LOSS", weight=0.2, price=100.0, eps_ttm=-5.0),
    ]
    pe, is_incomplete, meta = compute_weighted_pe(quotes, eps_field="eps_ttm")
    assert pe == pytest.approx(1.0 / 0.03, rel=1e-4)
    assert is_incomplete is False
    assert meta["loss_making_count"] == 1


def test_weighted_pe_missing_data_renormalizes_and_flags_incomplete():
    # 3% missing -> complete (<= 5%), weights renormalized among valid
    quotes_ok = [
        ConstituentQuote(ticker="A", weight=0.582, price=100.0, eps_fwd=5.0),
        ConstituentQuote(ticker="B", weight=0.388, price=100.0, eps_fwd=2.5),
        ConstituentQuote(ticker="MISS", weight=0.03, price=100.0, eps_fwd=None),
    ]
    pe_ok, incomplete_ok, meta_ok = compute_weighted_pe(quotes_ok, eps_field="eps_fwd")
    # Renormalized weights: 0.6 and 0.4 => EY = 0.6*0.05 + 0.4*0.025 = 0.04 => PE = 25.0
    assert pe_ok == pytest.approx(25.0, rel=1e-4)
    assert incomplete_ok is False
    assert meta_ok["missing_weight"] == pytest.approx(0.03, abs=1e-4)

    # 8% missing -> flagged incomplete (> 5%)
    quotes_inc = [
        ConstituentQuote(ticker="A", weight=0.92, price=100.0, eps_fwd=4.0),
        ConstituentQuote(ticker="MISS", weight=0.08, price=None, eps_fwd=4.0),
    ]
    pe_inc, incomplete_inc, meta_inc = compute_weighted_pe(quotes_inc, eps_field="eps_fwd")
    assert pe_inc == pytest.approx(25.0, rel=1e-4)
    assert incomplete_inc is True
    assert meta_inc["missing_weight"] == pytest.approx(0.08, abs=1e-4)


def test_merge_with_raw_series_respects_source_priority():
    computed = [
        IndicatorPoint(date="2026-01-02", value=31.5, source="computed"),
        IndicatorPoint(date="2026-01-03", value=32.0, source="computed"),
    ]
    raw_rows = [
        {"date": "2026-01-01", "value": 30.0, "source": "seed_csv"},
        {"date": "2026-01-02", "value": 29.8, "source": "csv_import"},
        {"date": "2026-01-02", "value": 29.5, "source": "seed_csv"},
    ]
    merged = merge_with_raw_series(
        computed_points=computed,
        raw_rows=raw_rows,
        source_priority=["computed", "csv_import", "seed_csv"],
    )
    by_date = {p.date: p for p in merged}
    assert by_date["2026-01-01"].source == "seed_csv"
    assert by_date["2026-01-01"].value == 30.0
    assert by_date["2026-01-02"].source == "computed"
    assert by_date["2026-01-02"].value == 31.5
    assert by_date["2026-01-03"].source == "computed"


def test_rolling_percentile_boundary_rules():
    # Generate 2.5 years of weekly points (linearly increasing 20 -> 40)
    start = date(2023, 1, 2)
    points: list[IndicatorPoint] = []
    for i in range(131):  # ~2.5 years
        d = start + timedelta(days=i * 7)
        points.append(
            IndicatorPoint(
                date=d.isoformat(),
                value=round(20.0 + i * 0.15, 2),
                source="csv_import" if i < 120 else "computed",
            )
        )

    pct_5y = rolling_percentile(points, window_years=5)
    assert len(pct_5y) == len(points)

    # First 52 weeks (< 365 days) must NOT display percentile
    assert pct_5y[0].value is None
    assert pct_5y[0].meta["insufficient_history"] is True
    assert pct_5y[40].value is None
    assert pct_5y[40].meta["insufficient_history"] is True

    # At 2.5 years (>= 1 year, < 5 years): display normally and annotate "基于 X 年数据"
    last = pct_5y[-1]
    assert last.value == 100.0  # strictly increasing => highest value is 100th percentile
    assert last.meta["insufficient_history"] is False
    assert last.meta["partial_window"] is True
    assert "基于" in last.meta["note"]
    assert last.meta["sources"] == ["computed", "csv_import"]
    assert last.meta["min"] == 20.0
    assert last.meta["max"] == points[-1].value


def test_rolling_percentile_full_window_and_median():
    # 6 years of monthly points cycling cleanly
    start = date(2020, 1, 15)
    points = []
    for m in range(73):  # 6 years + 1 month
        year = start.year + (start.month - 1 + m) // 12
        month = (start.month - 1 + m) % 12 + 1
        d = date(year, month, 15)
        # Value oscillates between 20 and 30; final value = 25 (median)
        val = 25.0 if m == 72 else (20.0 + (m % 11))
        points.append(IndicatorPoint(date=d.isoformat(), value=val, source="seed_csv"))

    pct_5y = rolling_percentile(points, window_years=5)
    last = pct_5y[-1]
    assert last.value is not None
    assert last.meta["partial_window"] is False
    assert last.meta["insufficient_history"] is False
    assert last.meta["min"] == 20.0
    assert last.meta["max"] == 30.0
    assert 45.0 <= last.value <= 60.0


def test_sanitize_eps_drops_corrupted_positive_outlier_but_keeps_loss_makers():
    # Corrupted unadjusted split EPS (e.g. price=79.24, eps=923.7 => PE=0.086x) -> dropped
    assert _sanitize_eps("SNDK", "eps_ttm", 923.7, 79.24) is None
    # Normal positive EPS -> kept
    assert _sanitize_eps("AAPL", "eps_ttm", 8.98, 342.56) == 8.98
    # Loss-making company with large negative EPS -> ALWAYS preserved
    assert _sanitize_eps("MSTR", "eps_ttm", -52.33, 77.8) == -52.33

