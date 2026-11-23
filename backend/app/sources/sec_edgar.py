"""Free SEC EDGAR CompanyFacts historical TTM P/E adapter for US equities.

Fetches official SEC XBRL CompanyFacts (10-Q / 10-K diluted EPS), derives Q4 from
FY - 9M (or FY - sum(Q1..Q3)), aligns historical pre-split EPS to Yahoo Finance's
split-adjusted daily closes using `Stock Splits`, guards against 20-F/ADR share-ratio
mismatches, and outputs daily `pe_ttm` series (typically 10-15 years for US stocks).
"""

from __future__ import annotations

import json
import logging
import math
import threading
import time
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx
import yfinance as yf

from app.config import (
    DATA_DIR,
    HTTP_TIMEOUT_SECONDS,
    INSTRUMENTS,
    MAX_EQUITY_PE,
    SEC_USER_AGENT,
)
from app.sources.base import (
    HistoricalSeriesSource,
    normalize_ticker_for_yahoo,
    parse_float,
)

logger = logging.getLogger(__name__)

SEC_COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_COMPANYFACTS_URL_TMPL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

_VALID_FORMS = {"10-Q", "10-K", "10-Q/A", "10-K/A", "20-F", "20-F/A"}
_AMENDED_FORMS = {"10-Q/A", "10-K/A", "20-F/A"}

_SEC_RATE_LOCK = threading.Lock()
_LAST_SEC_REQUEST_AT = 0.0
_MIN_SEC_INTERVAL_SEC = 0.22


def _sec_rate_limit_wait() -> None:
    global _LAST_SEC_REQUEST_AT
    with _SEC_RATE_LOCK:
        now = time.monotonic()
        elapsed = now - _LAST_SEC_REQUEST_AT
        if elapsed < _MIN_SEC_INTERVAL_SEC:
            time.sleep(_MIN_SEC_INTERVAL_SEC - elapsed)
        _LAST_SEC_REQUEST_AT = time.monotonic()


def _sec_headers() -> dict[str, str]:
    return {
        "User-Agent": SEC_USER_AGENT,
        "Accept": "application/json",
        "Accept-Encoding": "gzip, deflate",
    }


def _ticker_candidates(symbol: str) -> set[str]:
    sym = symbol.strip().upper()
    return {
        sym,
        sym.replace(".", "-"),
        sym.replace("-", "."),
        sym.replace(".", "").replace("-", ""),
    }


def resolve_cik(
    symbol: str,
    cache_path: Path | None = None,
    timeout: float = HTTP_TIMEOUT_SECONDS,
) -> str | None:
    """Resolve a US ticker symbol to its 10-digit zero-padded SEC CIK."""
    target_cache = cache_path or (DATA_DIR / "sec_company_tickers.json")
    payload: dict[str, Any] | None = None

    if target_cache.exists():
        age_sec = time.time() - target_cache.stat().st_mtime
        if age_sec < 7 * 86400:
            try:
                payload = json.loads(target_cache.read_text(encoding="utf-8"))
            except Exception:
                payload = None

    if payload is None:
        _sec_rate_limit_wait()
        with httpx.Client(
            timeout=timeout,
            headers=_sec_headers(),
            follow_redirects=True,
        ) as client:
            resp = client.get(SEC_COMPANY_TICKERS_URL)
            resp.raise_for_status()
            payload = resp.json()

        try:
            target_cache.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = target_cache.with_suffix(".tmp")
            tmp_path.write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
            tmp_path.replace(target_cache)
        except Exception as exc:
            logger.debug("Could not cache SEC company_tickers.json: %s", exc)

    if not isinstance(payload, dict):
        return None

    candidates = _ticker_candidates(symbol)
    sym_dash = symbol.strip().upper().replace(".", "-")

    for entry in payload.values():
        if not isinstance(entry, dict):
            continue
        raw_ticker = str(entry.get("ticker", "")).strip().upper()
        if not raw_ticker:
            continue
        if raw_ticker in candidates or raw_ticker.replace(".", "-") == sym_dash:
            cik_raw = entry.get("cik_str")
            if cik_raw is not None:
                try:
                    return f"{int(cik_raw):010d}"
                except (TypeError, ValueError):
                    continue
    return None


@dataclass
class _EpsFact:
    start: str
    end: str
    duration: int
    adj_val: float
    first_filed: str
    latest_filed: str


def _parse_iso_date(raw: Any) -> date | None:
    if not isinstance(raw, str):
        return None
    text = raw.strip()[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _pick_unit_entries(concept_obj: dict[str, Any]) -> list[dict[str, Any]]:
    units = concept_obj.get("units")
    if not isinstance(units, dict):
        return []
    if isinstance(units.get("USD/shares"), list) and units["USD/shares"]:
        return units["USD/shares"]
    for unit_key, entries in units.items():
        if (
            isinstance(entries, list)
            and entries
            and "usd" in unit_key.lower()
            and unit_key.lower().endswith("/shares")
        ):
            return entries
    for unit_key, entries in units.items():
        if isinstance(entries, list) and entries and unit_key.lower().endswith("/shares"):
            return entries
    return []


def _adjust_eps_for_splits(
    val: float,
    ref_filed: str,
    splits: list[tuple[str, float]] | None,
) -> float:
    if not splits:
        return val
    factor = 1.0
    for sp_date, ratio in splits:
        if ratio and ratio > 0 and sp_date > ref_filed:
            factor *= ratio
    return val / factor if factor > 0 else val


def _avail_date(period_end: str, first_filed: str) -> str:
    end_d = date.fromisoformat(period_end)
    filed_d = date.fromisoformat(first_filed)
    delay = (filed_d - end_d).days
    if 0 <= delay <= 120:
        return first_filed
    return (end_d + timedelta(days=45)).isoformat()


def extract_ttm_eps_anchors_from_companyfacts(
    facts_json: dict[str, Any],
    splits: list[tuple[str, float]] | None = None,
) -> tuple[list[tuple[str, float]], bool]:
    """Parse SEC EDGAR CompanyFacts JSON into `([(avail_date, split_adj_ttm_eps)], is_foreign_20f)`.

    Handles:
    - US-GAAP (`EarningsPerShareDiluted` -> `EarningsPerShareBasic`) and IFRS (`DilutedEarningsLossPerShare` -> `BasicEarningsLossPerShare`)
    - Restatements & comparative filings (uses latest `filed` value and earliest `filed` availability clamped at +45d if >120d)
    - Historical stock splits relative to each filing's `filed` date
    - Standalone Q4 derivation (`FY - 9M` or `FY - sum(Q1..Q3)`)
    - Rolling 4-quarter TTM EPS sums (fallback to FY if <4 quarters exist)
    """
    facts_root = facts_json.get("facts")
    if not isinstance(facts_root, dict):
        return [], False

    us_gaap = facts_root.get("us-gaap") if isinstance(facts_root.get("us-gaap"), dict) else {}
    ifrs_full = facts_root.get("ifrs-full") if isinstance(facts_root.get("ifrs-full"), dict) else {}

    candidates: list[tuple[bool, list[dict[str, Any]]]] = []
    for concept_name in ("EarningsPerShareDiluted", "EarningsPerShareBasic"):
        obj = us_gaap.get(concept_name)
        if isinstance(obj, dict):
            entries = _pick_unit_entries(obj)
            if entries:
                candidates.append((False, entries))
    for concept_name in ("DilutedEarningsLossPerShare", "BasicEarningsLossPerShare"):
        obj = ifrs_full.get(concept_name)
        if isinstance(obj, dict):
            entries = _pick_unit_entries(obj)
            if entries:
                candidates.append((True, entries))

    if not candidates:
        return [], False

    selected_is_ifrs = False
    selected_entries: list[dict[str, Any]] = []
    for is_ifrs, entries in candidates:
        filtered = [
            e
            for e in entries
            if isinstance(e, dict)
            and str(e.get("form", "")).strip().upper() in _VALID_FORMS
        ]
        if filtered:
            selected_is_ifrs = is_ifrs
            selected_entries = filtered
            break

    if not selected_entries:
        return [], False

    has_domestic_form = any(
        str(e.get("form", "")).strip().upper() in {"10-Q", "10-K", "10-Q/A", "10-K/A"}
        for e in selected_entries
    )
    has_20f_form = any(
        str(e.get("form", "")).strip().upper() in {"20-F", "20-F/A"}
        for e in selected_entries
    )
    is_foreign_20f = selected_is_ifrs or (has_20f_form and not has_domestic_form)

    sorted_splits = sorted(
        [(d, float(r)) for d, r in (splits or []) if r and r > 0],
        key=lambda item: item[0],
    )

    # Deduplicate by (start, end): keep earliest `filed` for point-in-time availability
    # and latest `filed` for value & split-reference date.
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for entry in selected_entries:
        start_d = _parse_iso_date(entry.get("start"))
        end_d = _parse_iso_date(entry.get("end"))
        filed_d = _parse_iso_date(entry.get("filed"))
        raw_val = entry.get("val")
        if start_d is None or end_d is None or filed_d is None:
            continue
        if isinstance(raw_val, bool) or not isinstance(raw_val, (int, float)):
            continue
        val = float(raw_val)
        if not math.isfinite(val):
            continue

        duration = (end_d - start_d).days
        if duration <= 0:
            continue

        start_s = start_d.isoformat()
        end_s = end_d.isoformat()
        filed_s = filed_d.isoformat()
        form = str(entry.get("form", "")).strip().upper()
        is_amended = 1 if form in _AMENDED_FORMS else 0
        key = (start_s, end_s)

        rec = grouped.get(key)
        if rec is None:
            grouped[key] = {
                "start": start_s,
                "end": end_s,
                "duration": duration,
                "first_filed": filed_s,
                "latest_filed": filed_s,
                "latest_amended": is_amended,
                "latest_val": val,
            }
        else:
            if filed_s < rec["first_filed"]:
                rec["first_filed"] = filed_s
            if (filed_s, is_amended) >= (rec["latest_filed"], rec["latest_amended"]):
                rec["latest_filed"] = filed_s
                rec["latest_amended"] = is_amended
                rec["latest_val"] = val

    quarters_raw: list[_EpsFact] = []
    nine_months: list[_EpsFact] = []
    full_years: list[_EpsFact] = []

    for rec in grouped.values():
        dur = rec["duration"]
        adj_val = _adjust_eps_for_splits(
            rec["latest_val"], rec["latest_filed"], sorted_splits
        )
        fact = _EpsFact(
            start=rec["start"],
            end=rec["end"],
            duration=dur,
            adj_val=adj_val,
            first_filed=rec["first_filed"],
            latest_filed=rec["latest_filed"],
        )
        if 70 <= dur <= 115:
            quarters_raw.append(fact)
        elif 250 <= dur <= 295:
            nine_months.append(fact)
        elif 330 <= dur <= 390:
            full_years.append(fact)

    # Merge nearly-identical standalone quarters (e.g. 52/53-week calendar 1-2 day drift)
    quarters_raw.sort(key=lambda f: (f.end, f.latest_filed))
    quarters: list[_EpsFact] = []
    for q in quarters_raw:
        if quarters:
            prev = quarters[-1]
            if abs((date.fromisoformat(q.end) - date.fromisoformat(prev.end)).days) <= 10:
                merged_first = min(prev.first_filed, q.first_filed)
                chosen = q if q.latest_filed >= prev.latest_filed else prev
                quarters[-1] = _EpsFact(
                    start=chosen.start,
                    end=chosen.end,
                    duration=chosen.duration,
                    adj_val=chosen.adj_val,
                    first_filed=merged_first,
                    latest_filed=chosen.latest_filed,
                )
                continue
        quarters.append(q)

    # Derive Q4 for every FY lacking a standalone Q4 ending within ±10 days of fy.end
    derived_q4: list[_EpsFact] = []
    for fy in sorted(full_years, key=lambda f: f.end):
        fy_start_d = date.fromisoformat(fy.start)
        fy_end_d = date.fromisoformat(fy.end)

        has_standalone_q4 = any(
            abs((date.fromisoformat(q.end) - fy_end_d).days) <= 10 for q in quarters
        )
        if has_standalone_q4:
            continue

        # 1. Try FY - matching 9M YTD
        matched_9m: _EpsFact | None = None
        for m9 in nine_months:
            m9_start_d = date.fromisoformat(m9.start)
            m9_end_d = date.fromisoformat(m9.end)
            if (
                abs((m9_start_d - fy_start_d).days) <= 10
                and 70 <= (fy_end_d - m9_end_d).days <= 115
            ):
                if matched_9m is None or m9.latest_filed > matched_9m.latest_filed:
                    matched_9m = m9

        if matched_9m is not None:
            q4_start = (date.fromisoformat(matched_9m.end) + timedelta(days=1)).isoformat()
            q4_dur = (fy_end_d - date.fromisoformat(matched_9m.end)).days
            derived_q4.append(
                _EpsFact(
                    start=q4_start,
                    end=fy.end,
                    duration=q4_dur,
                    adj_val=fy.adj_val - matched_9m.adj_val,
                    first_filed=fy.first_filed,
                    latest_filed=fy.latest_filed,
                )
            )
            continue

        # 2. Fallback: FY - sum(Q1, Q2, Q3)
        qs_in_fy = [
            q
            for q in quarters
            if date.fromisoformat(q.start) >= fy_start_d - timedelta(days=10)
            and date.fromisoformat(q.end) <= fy_end_d - timedelta(days=60)
        ]
        if len(qs_in_fy) == 3:
            last_q_end = date.fromisoformat(qs_in_fy[-1].end)
            q4_dur = (fy_end_d - last_q_end).days
            if 70 <= q4_dur <= 115:
                derived_q4.append(
                    _EpsFact(
                        start=(last_q_end + timedelta(days=1)).isoformat(),
                        end=fy.end,
                        duration=q4_dur,
                        adj_val=fy.adj_val - sum(q.adj_val for q in qs_in_fy),
                        first_filed=fy.first_filed,
                        latest_filed=fy.latest_filed,
                    )
                )

    all_quarters = sorted([*quarters, *derived_q4], key=lambda f: f.end)

    # Deduplicate again after adding derived Q4s
    dedup_quarters: list[_EpsFact] = []
    for q in all_quarters:
        if dedup_quarters:
            prev = dedup_quarters[-1]
            if abs((date.fromisoformat(q.end) - date.fromisoformat(prev.end)).days) <= 10:
                merged_first = min(prev.first_filed, q.first_filed)
                chosen = q if q.latest_filed >= prev.latest_filed else prev
                dedup_quarters[-1] = _EpsFact(
                    start=chosen.start,
                    end=chosen.end,
                    duration=chosen.duration,
                    adj_val=chosen.adj_val,
                    first_filed=merged_first,
                    latest_filed=chosen.latest_filed,
                )
                continue
        dedup_quarters.append(q)

    anchors_by_avail: dict[str, tuple[str, float]] = {}
    if len(dedup_quarters) >= 4:
        for idx in range(3, len(dedup_quarters)):
            q_oldest = dedup_quarters[idx - 3]
            q_newest = dedup_quarters[idx]
            span = (
                date.fromisoformat(q_newest.end) - date.fromisoformat(q_oldest.start)
            ).days
            if 320 <= span <= 405:
                ttm_eps = sum(q.adj_val for q in dedup_quarters[idx - 3 : idx + 1])
                av = _avail_date(q_newest.end, q_newest.first_filed)
                prev_anchor = anchors_by_avail.get(av)
                if prev_anchor is None or q_newest.end >= prev_anchor[0]:
                    anchors_by_avail[av] = (q_newest.end, round(ttm_eps, 6))
    elif full_years:
        for fy in sorted(full_years, key=lambda f: f.end):
            av = _avail_date(fy.end, fy.first_filed)
            prev_anchor = anchors_by_avail.get(av)
            if prev_anchor is None or fy.end >= prev_anchor[0]:
                anchors_by_avail[av] = (fy.end, round(fy.adj_val, 6))

    anchors = [
        (av, val)
        for av, (_, val) in sorted(anchors_by_avail.items(), key=lambda item: item[0])
    ]
    return anchors, is_foreign_20f


def build_daily_pe_from_eps_anchors(
    anchors: list[tuple[str, float]],
    closes: dict[str, float],
    start_date: str | None = None,
    end_date: str | None = None,
    max_pe: float = MAX_EQUITY_PE,
) -> list[tuple[str, float]]:
    """Combine step-function TTM EPS anchors `[(avail_date, eps)]` with daily closes."""
    if not anchors or not closes:
        return []

    sorted_anchors = sorted(anchors, key=lambda item: item[0])
    anchor_dates = [d for d, _ in sorted_anchors]
    anchor_vals = [float(v) for _, v in sorted_anchors]

    output: list[tuple[str, float]] = []
    for dt_str in sorted(closes.keys()):
        if start_date and dt_str < start_date:
            continue
        if end_date and dt_str > end_date:
            continue
        px = closes[dt_str]
        if px is None or px <= 0:
            continue
        idx = bisect_right(anchor_dates, dt_str) - 1
        if idx < 0:
            continue
        eps = anchor_vals[idx]
        if eps is None or eps <= 0:
            continue
        pe = px / eps
        if 1.0 <= pe <= max_pe:
            output.append((dt_str, round(pe, 4)))

    return output


def fetch_yahoo_equity_prices_and_splits(
    symbol: str,
    start_date: str,
    end_date: str | None = None,
) -> tuple[dict[str, float], list[tuple[str, float]], float | None]:
    """Fetch split-adjusted daily closes, stock splits, and optional trailingEps from Yahoo."""
    yf_symbol = normalize_ticker_for_yahoo(symbol)
    try:
        end_arg = None
        if end_date:
            end_arg = (date.fromisoformat(end_date) + timedelta(days=1)).isoformat()
        ticker_obj = yf.Ticker(yf_symbol)
        hist = ticker_obj.history(
            start=start_date,
            end=end_arg,
            interval="1d",
            auto_adjust=False,
            actions=True,
        )
        closes: dict[str, float] = {}
        splits: list[tuple[str, float]] = []
        if hist is not None and not hist.empty:
            if "Close" in hist.columns:
                for idx, val in hist["Close"].items():
                    fval = parse_float(val)
                    if fval is not None and fval > 0:
                        dt_str = (
                            idx.date().isoformat()
                            if hasattr(idx, "date")
                            else str(idx)[:10]
                        )
                        closes[dt_str] = fval
            if "Stock Splits" in hist.columns:
                for idx, val in hist["Stock Splits"].items():
                    fval = parse_float(val)
                    if fval is not None and fval > 0 and abs(fval - 1.0) > 1e-6:
                        dt_str = (
                            idx.date().isoformat()
                            if hasattr(idx, "date")
                            else str(idx)[:10]
                        )
                        splits.append((dt_str, fval))

        trailing_eps: float | None = None
        try:
            info = ticker_obj.info or {}
            trailing_eps = parse_float(info.get("trailingEps"))
        except Exception:
            trailing_eps = None

        return closes, sorted(splits, key=lambda item: item[0]), trailing_eps
    except Exception as exc:
        logger.warning(
            "Failed to fetch Yahoo equity closes/splits for %s (%s): %s",
            symbol,
            yf_symbol,
            exc,
        )
        return {}, [], None


class SECEdgarEquitySource(HistoricalSeriesSource):
    """Free SEC EDGAR CompanyFacts historical trailing P/E adapter for US equities."""

    source_id = "sec_edgar"
    tier = "free"
    supported_instruments = ()
    supported_series = ("pe_ttm",)

    def __init__(self, timeout: float = HTTP_TIMEOUT_SECONDS) -> None:
        self.timeout = timeout

    def supports(self, instrument_symbol: str) -> bool:
        return instrument_symbol.strip().upper() not in INSTRUMENTS

    def fetch_history(
        self,
        instrument_symbol: str,
        start_date: str,
        end_date: str | None = None,
    ) -> dict[str, list[tuple[str, float]]]:
        sym = instrument_symbol.strip().upper()
        if not self.supports(sym):
            return {}

        cik = resolve_cik(sym, timeout=self.timeout)
        if not cik:
            logger.info("SEC EDGAR CIK not found for %s; skipping", sym)
            return {}

        pre_start = (date.fromisoformat(start_date) - timedelta(days=120)).isoformat()
        closes, splits, trailing_eps = fetch_yahoo_equity_prices_and_splits(
            sym, start_date=pre_start, end_date=end_date
        )
        if not closes:
            return {}

        _sec_rate_limit_wait()
        url = SEC_COMPANYFACTS_URL_TMPL.format(cik=cik)
        with httpx.Client(
            timeout=self.timeout,
            headers=_sec_headers(),
            follow_redirects=True,
        ) as client:
            resp = client.get(url)
            if resp.status_code == 404:
                return {}
            resp.raise_for_status()
            facts_json = resp.json()

        anchors, is_foreign_20f = extract_ttm_eps_anchors_from_companyfacts(
            facts_json, splits=splits
        )
        if not anchors:
            return {}

        if is_foreign_20f:
            logger.info(
                "Skipping SEC EDGAR for %s due to 20-F/IFRS ADR ratio ambiguity",
                sym,
            )
            return {}

        # Guard against ADR or share-class EPS mismatch against yfinance trailingEps
        latest_av_date, latest_eps = anchors[-1]
        if (
            trailing_eps is not None
            and trailing_eps > 0
            and latest_eps > 0
            and (date.today() - date.fromisoformat(latest_av_date)).days <= 365
        ):
            ratio = max(trailing_eps / latest_eps, latest_eps / trailing_eps)
            if ratio > 5.0:
                logger.warning(
                    "SEC EDGAR EPS mismatch for %s (sec=%.4f vs yf=%.4f, ratio=%.2fx); falling back",
                    sym,
                    latest_eps,
                    trailing_eps,
                    ratio,
                )
                return {}

        daily_pe = build_daily_pe_from_eps_anchors(
            anchors,
            closes=closes,
            start_date=start_date,
            end_date=end_date,
            max_pe=MAX_EQUITY_PE,
        )
        return {"pe_ttm": daily_pe} if daily_pe else {}
