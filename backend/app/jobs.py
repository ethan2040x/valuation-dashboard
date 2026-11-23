"""Scheduled jobs, daily refresh orchestration, CSV import, and CLI commands."""

from __future__ import annotations

import argparse
import csv
import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import (
    DB_PATH,
    DEFAULT_INSTRUMENT_SYMBOL,
    EXPORT_OUTPUT_PATH,
    INSTRUMENTS,
    SCHEDULE_CRON_HOUR,
    SCHEDULE_CRON_MINUTE,
    SCHEDULE_TIMEZONE,
    SEED_DIR,
    YFINANCE_MAX_WORKERS,
)
from app.engine import IndicatorEngine
from app.models import ConstituentQuote, EquitySnapshot
from app.sources.base import FundamentalsSource, HistoricalSeriesSource, HoldingsSource
from app.sources.csv_import import _normalize_date, import_csv_file, import_csv_text
from app.sources.history_sources import get_historical_sources
from app.sources.qqq_holdings import QQQHoldingsSource
from app.sources.spy_holdings import SPYHoldingsSource
from app.sources.yfinance_source import YFinanceFundamentalsSource, fetch_ticker_profile
from app.store import ValuationStore

logger = logging.getLogger(__name__)


def get_holdings_source(instrument_symbol: str) -> HoldingsSource:
    """Return the default HoldingsSource adapter for an instrument."""
    symbol = instrument_symbol.upper()
    if symbol == "SPY":
        return SPYHoldingsSource()
    return QQQHoldingsSource()


def _source_tag_for_instrument(instrument_symbol: str) -> str:
    meta = INSTRUMENTS.get(instrument_symbol.upper())
    if meta and meta.get("source_tag"):
        return meta["source_tag"]
    return f"{instrument_symbol.lower()}+yfinance"


def _market_today_iso() -> str:
    return datetime.now(ZoneInfo("America/New_York")).date().isoformat()


def run_equity_refresh(
    store: ValuationStore,
    symbols: list[str] | None = None,
    fundamentals_source: FundamentalsSource | None = None,
    reuse_constituents: bool = True,
    as_of_date: str | None = None,
    job_id: int | None = None,
) -> dict[str, Any]:
    """Batch-refresh US equity watchlist price, EPS, and indicators.

    Reuses today's `constituent_snapshots` (from NDX/SPY) before calling yfinance,
    limiting concurrency to avoid Yahoo rate limits.
    """
    if symbols is not None:
        target_symbols = list(
            dict.fromkeys(s.strip().upper() for s in symbols if s.strip())
        )
        for sym in target_symbols:
            store.get_or_create_instrument(sym, kind="equity")
    else:
        target_symbols = [
            row["symbol"] for row in store.list_instruments(kind="equity", enabled_only=True)
        ]

    single_sym = target_symbols[0] if len(target_symbols) == 1 else None
    base_details: dict[str, Any] = (
        {"instrument": single_sym} if single_sym else {"symbols": target_symbols}
    )
    eff_job_id = (
        job_id
        if job_id is not None
        else store.start_job_run("refresh_equities", base_details)
    )

    if not target_symbols:
        summary = {
            **base_details,
            "status": "success",
            "as_of_date": as_of_date or _market_today_iso(),
            "total": 0,
            "saved": 0,
            "reused": 0,
            "fetched": 0,
            "failed": [],
            "indicators_computed": {},
        }
        store.finish_job_run(eff_job_id, status="success", details=summary)
        return summary

    eff_date = as_of_date or _market_today_iso()
    reused_quotes: dict[str, ConstituentQuote] = {}
    if reuse_constituents:
        reused_quotes = store.get_constituent_quotes_for_date(eff_date, target_symbols)
        if not reused_quotes and as_of_date is None:
            local_today = date.today().isoformat()
            if local_today != eff_date:
                cand = store.get_constituent_quotes_for_date(local_today, target_symbols)
                if cand:
                    reused_quotes = cand
                    eff_date = local_today

    remaining = [
        sym
        for sym in target_symbols
        if sym not in reused_quotes
        or reused_quotes[sym].price is None
        or (reused_quotes[sym].eps_ttm is None and reused_quotes[sym].eps_fwd is None)
    ]

    fetched_map: dict[str, Any] = {}
    fetch_error: str | None = None
    if remaining:
        f_source = fundamentals_source or YFinanceFundamentalsSource(
            max_workers=min(6, YFINANCE_MAX_WORKERS)
        )
        try:
            fetched_map = f_source.fetch_fundamentals(remaining)
        except Exception as exc:
            fetch_error = str(exc)
            logger.error("Batch equity fundamentals fetch failed: %s", fetch_error)

    saved_count = 0
    reused_count = 0
    fetched_count = 0
    failed_symbols: list[str] = []
    computed_by_sym: dict[str, dict[str, int]] = {}

    for idx, sym in enumerate(target_symbols):
        inst = store.get_or_create_instrument(sym, kind="equity")
        inst_id = int(inst["id"])
        snap: EquitySnapshot | None = None

        fund = fetched_map.get(sym)
        if fund is not None and fund.price is not None and fund.price > 0:
            snap = EquitySnapshot(
                price=fund.price,
                eps_ttm=fund.eps_ttm,
                eps_fwd=fund.eps_fwd,
                market_cap=fund.market_cap,
                source="equity+yfinance",
            )
            fetched_count += 1
            # Opportunistically enrich missing company metadata
            update_fields: dict[str, Any] = {}
            if not inst.get("en_name") and getattr(fund, "name", None):
                update_fields["en_name"] = fund.name
            if not inst.get("sector") and getattr(fund, "sector", None):
                update_fields["sector"] = fund.sector
            if not inst.get("currency") and getattr(fund, "currency", None):
                update_fields["currency"] = fund.currency
            if update_fields:
                store.update_instrument(sym, **update_fields)
        elif sym in reused_quotes:
            rq = reused_quotes[sym]
            snap = EquitySnapshot(
                price=rq.price,
                eps_ttm=rq.eps_ttm,
                eps_fwd=rq.eps_fwd,
                market_cap=None,
                source="equity+yfinance",
            )
            reused_count += 1
        else:
            failed_symbols.append(sym)

        if snap is not None:
            store.save_equity_snapshot(inst_id, eff_date, snap)
            saved_count += 1
            computed_by_sym[sym] = run_compute_indicators(store, instrument_symbol=sym)

        if job_id is not None:
            store.update_job_run_details(
                eff_job_id,
                {
                    **base_details,
                    "completed": idx + 1,
                    "total": len(target_symbols),
                    "current_symbol": sym,
                    "saved": saved_count,
                    "reused": reused_count,
                    "fetched": fetched_count,
                    "failed": failed_symbols,
                },
            )

    if saved_count == 0 and failed_symbols:
        overall_status = "failed"
    elif failed_symbols:
        overall_status = "partial"
    else:
        overall_status = "success"

    err_text = fetch_error or (
        f"{len(failed_symbols)} tickers failed: {', '.join(failed_symbols[:10])}"
        if failed_symbols
        else None
    )
    summary = {
        **base_details,
        "job_id": eff_job_id,
        "status": overall_status,
        "error": err_text,
        "as_of_date": eff_date,
        "total": len(target_symbols),
        "saved": saved_count,
        "reused": reused_count,
        "fetched": fetched_count,
        "failed": failed_symbols,
        "indicators_computed": (
            computed_by_sym[single_sym] if single_sym and single_sym in computed_by_sym else computed_by_sym
        ),
    }
    store.finish_job_run(eff_job_id, status=overall_status, error=err_text, details=summary)
    return summary


def run_daily_refresh(
    store: ValuationStore,
    instrument_symbol: str = DEFAULT_INSTRUMENT_SYMBOL,
    holdings_source: HoldingsSource | None = None,
    fundamentals_source: FundamentalsSource | None = None,
) -> dict[str, Any]:
    """Fetch daily holdings + yfinance price/EPS for an index, or delegate equity refresh.

    If either upstream data source fails, the failure is recorded in `job_runs` while all
    previously stored snapshots and indicator values remain intact.
    """
    symbol_upper = instrument_symbol.strip().upper()
    existing_inst = store.get_instrument(symbol_upper)
    is_equity = (
        (existing_inst is not None and existing_inst.get("kind") == "equity")
        or (existing_inst is None and symbol_upper not in INSTRUMENTS)
    )
    if is_equity:
        res = run_equity_refresh(
            store,
            symbols=[symbol_upper],
            fundamentals_source=fundamentals_source,
        )
        return {
            "status": res["status"],
            "error": res.get("error"),
            "as_of_date": res.get("as_of_date"),
            "constituents_saved": res.get("saved", 0),
            "equities_saved": res.get("saved", 0),
            "indicators_computed": res.get("indicators_computed", {}),
        }

    h_source = holdings_source or get_holdings_source(symbol_upper)
    f_source = fundamentals_source or YFinanceFundamentalsSource()
    source_tag = _source_tag_for_instrument(symbol_upper)

    overall_job_id = store.start_job_run("daily_refresh", {"instrument": symbol_upper})
    instrument = store.get_or_create_instrument(symbol_upper)
    instrument_id = int(instrument["id"])

    # Step 1: Fetch QQQ/SPY constituent weights
    holdings_job_id = store.start_job_run(h_source.name, {"instrument": symbol_upper})
    try:
        as_of_date, holdings = h_source.fetch_holdings()
        store.finish_job_run(
            holdings_job_id,
            status="success",
            details={
                "instrument": symbol_upper,
                "as_of_date": as_of_date,
                "count": len(holdings),
            },
        )
    except Exception as exc:
        err_msg = str(exc)
        logger.error("Holdings fetch failed: %s", err_msg)
        store.finish_job_run(holdings_job_id, status="failed", error=err_msg)
        store.finish_job_run(overall_job_id, status="failed", error=f"Holdings fetch failed: {err_msg}")
        return {
            "status": "failed",
            "error": err_msg,
            "as_of_date": None,
            "constituents_saved": 0,
            "indicators_computed": {},
        }

    # Step 2: Fetch price, trailing EPS, forward EPS from yfinance
    yf_job_id = store.start_job_run(f_source.name, {"instrument": symbol_upper})
    try:
        tickers = [h.ticker for h in holdings]
        fundamentals = f_source.fetch_fundamentals(tickers)

        quotes: list[ConstituentQuote] = []
        ok_ttm = 0
        ok_fwd = 0
        failed_tickers: list[str] = []

        for h in holdings:
            fund = fundamentals.get(h.ticker)
            if fund is None or (fund.price is None and fund.error):
                failed_tickers.append(h.ticker)
                quotes.append(
                    ConstituentQuote(
                        ticker=h.ticker,
                        weight=h.weight,
                        price=None,
                        eps_ttm=None,
                        eps_fwd=None,
                        source=source_tag,
                    )
                )
            else:
                if fund.price and fund.eps_ttm is not None:
                    ok_ttm += 1
                if fund.price and fund.eps_fwd is not None:
                    ok_fwd += 1
                quotes.append(
                    ConstituentQuote(
                        ticker=h.ticker,
                        weight=h.weight,
                        price=fund.price,
                        eps_ttm=fund.eps_ttm,
                        eps_fwd=fund.eps_fwd,
                        source=source_tag,
                    )
                )

        if ok_ttm == 0 and ok_fwd == 0:
            raise RuntimeError(
                f"yfinance returned no valid price/EPS quotes for {len(tickers)} tickers"
            )

        yf_status = "success" if not failed_tickers else "partial"
        store.finish_job_run(
            yf_job_id,
            status=yf_status,
            error=(
                f"{len(failed_tickers)} tickers failed: {', '.join(failed_tickers[:10])}"
                if failed_tickers
                else None
            ),
            details={
                "instrument": symbol_upper,
                "total": len(tickers),
                "ok_ttm": ok_ttm,
                "ok_fwd": ok_fwd,
                "failed_count": len(failed_tickers),
            },
        )
    except Exception as exc:
        err_msg = str(exc)
        logger.error("Fundamentals fetch failed: %s", err_msg)
        store.finish_job_run(yf_job_id, status="failed", error=err_msg)
        store.finish_job_run(
            overall_job_id, status="failed", error=f"Fundamentals fetch failed: {err_msg}"
        )
        return {
            "status": "failed",
            "error": err_msg,
            "as_of_date": as_of_date,
            "constituents_saved": 0,
            "indicators_computed": {},
        }

    # Step 3: Persist constituent snapshot & recompute indicators
    saved_constituents = store.save_constituent_snapshots(
        instrument_id=instrument_id,
        date=as_of_date,
        quotes=quotes,
    )
    computed = run_compute_indicators(store, instrument_symbol=symbol_upper)

    overall_status = "partial" if failed_tickers else "success"
    store.finish_job_run(
        overall_job_id,
        status=overall_status,
        details={
            "instrument": symbol_upper,
            "as_of_date": as_of_date,
            "constituents_saved": saved_constituents,
            "ok_ttm": ok_ttm,
            "ok_fwd": ok_fwd,
            "indicators_computed": computed,
        },
    )
    return {
        "status": overall_status,
        "error": None,
        "as_of_date": as_of_date,
        "constituents_saved": saved_constituents,
        "ok_ttm": ok_ttm,
        "ok_fwd": ok_fwd,
        "indicators_computed": computed,
    }


def run_compute_indicators(
    store: ValuationStore,
    instrument_symbol: str = DEFAULT_INSTRUMENT_SYMBOL,
) -> dict[str, int]:
    """Recompute all registered indicators (used after refresh or CSV backfill)."""
    symbol_upper = instrument_symbol.upper()
    job_id = store.start_job_run("compute_indicators", {"instrument": symbol_upper})
    try:
        engine = IndicatorEngine(store)
        counts = engine.compute_all(instrument_symbol=symbol_upper)
        store.finish_job_run(
            job_id,
            status="success",
            details={"instrument": symbol_upper, "counts": counts},
        )
        return counts
    except Exception as exc:
        store.finish_job_run(job_id, status="failed", error=str(exc))
        raise


def run_csv_import(
    store: ValuationStore,
    csv_path: str | Path,
    instrument_symbol: str = DEFAULT_INSTRUMENT_SYMBOL,
    source: str = "csv_import",
    series_id: str | None = None,
    recompute: bool = True,
) -> dict[str, Any]:
    """Import historical CSV data into `raw_series` and optionally recompute indicators."""
    symbol_upper = instrument_symbol.strip().upper()
    if symbol_upper == "AUTO":
        csv_text = Path(csv_path).read_text(encoding="utf-8-sig")
        res = run_csv_text_import(
            store=store,
            csv_text=csv_text,
            instrument_symbol=None,
            source=source,
            series_id=series_id,
            recompute=recompute,
        )
        totals: dict[str, int] = {}
        for sym_counts in res["imported_by_symbol"].values():
            for sid, cnt in sym_counts.items():
                totals[sid] = totals.get(sid, 0) + cnt
        return {
            "imported": totals,
            "imported_by_symbol": res["imported_by_symbol"],
            "computed": res["computed_by_symbol"],
        }

    job_id = store.start_job_run(
        "csv_import",
        {
            "path": str(csv_path),
            "instrument": symbol_upper,
            "source": source,
            "series_id": series_id,
        },
    )
    try:
        counts = import_csv_file(
            store=store,
            csv_path=csv_path,
            instrument_symbol=symbol_upper,
            default_source=source,
            series_id=series_id,
        )
        computed = (
            run_compute_indicators(store, instrument_symbol=symbol_upper)
            if recompute
            else {}
        )
        store.finish_job_run(
            job_id,
            status="success",
            details={
                "instrument": symbol_upper,
                "imported": counts,
                "computed": computed,
            },
        )
        return {"imported": counts, "computed": computed}
    except Exception as exc:
        store.finish_job_run(job_id, status="failed", error=str(exc))
        raise


def run_csv_text_import(
    store: ValuationStore,
    csv_text: str,
    instrument_symbol: str | None = None,
    source: str = "csv_import",
    series_id: str | None = None,
    recompute: bool = True,
) -> dict[str, Any]:
    """Import raw CSV text into `raw_series` (single or multi-company) and recompute."""
    eff_inst = (
        instrument_symbol.strip().upper()
        if instrument_symbol and instrument_symbol.strip().upper() != "AUTO"
        else None
    )
    job_id = store.start_job_run(
        "csv_import",
        {
            "instrument": eff_inst,
            "source": source,
            "series_id": series_id,
        },
    )
    try:
        imported_by_symbol = import_csv_text(
            store=store,
            csv_text=csv_text,
            instrument_symbol=eff_inst,
            default_source=source,
            series_id=series_id,
        )
        computed_by_symbol: dict[str, dict[str, int]] = {}
        if recompute:
            for sym in imported_by_symbol:
                computed_by_symbol[sym] = run_compute_indicators(
                    store, instrument_symbol=sym
                )
        affected = sorted(imported_by_symbol.keys())
        details: dict[str, Any] = {
            "imported_by_symbol": imported_by_symbol,
            "computed_by_symbol": computed_by_symbol,
            "symbols": affected,
        }
        if len(affected) == 1:
            details["instrument"] = affected[0]
        store.finish_job_run(job_id, status="success", details=details)
        return {
            "status": "success",
            "imported_by_symbol": imported_by_symbol,
            "computed_by_symbol": computed_by_symbol,
            "total_rows": sum(
                cnt for m in imported_by_symbol.values() for cnt in m.values()
            ),
        }
    except Exception as exc:
        store.finish_job_run(job_id, status="failed", error=str(exc))
        raise


def _read_earliest_csv_date(csv_file: Path) -> str | None:
    """Inspect a seed CSV file and return its earliest valid ISO date."""
    try:
        with csv_file.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames:
                return None
            lower_fields = {name.strip().lower(): name.strip() for name in reader.fieldnames if name}
            date_col = lower_fields.get("date")
            if not date_col:
                return None
            earliest: str | None = None
            for row in reader:
                raw_date = (row.get(date_col) or "").strip()
                if not raw_date:
                    continue
                try:
                    iso = _normalize_date(raw_date)
                except ValueError:
                    continue
                if earliest is None or iso < earliest:
                    earliest = iso
            return earliest
    except Exception:
        return None


def bootstrap_seed_data_if_empty(
    store: ValuationStore,
    seed_dir: Path = SEED_DIR,
    instrument_symbol: str = DEFAULT_INSTRUMENT_SYMBOL,
    force: bool = False,
) -> bool:
    """Import seed CSVs from `data/seed/` if empty or if a longer seed history is available.

    When upgrading an existing `seed_csv` dataset (e.g. from 10y to 20y), only `seed_csv`
    rows are replaced; any live `computed`, free historical, or imported CSV rows are preserved.
    """
    if not seed_dir.exists():
        return False

    symbol_upper = instrument_symbol.upper()
    meta = INSTRUMENTS.get(symbol_upper)
    if meta and meta.get("seed_file"):
        candidate = seed_dir / meta["seed_file"]
        csv_files = [candidate] if candidate.exists() else []
    else:
        prefix = symbol_upper.lower()
        csv_files = sorted(
            p for p in seed_dir.glob(f"{prefix}*.csv") if not p.name.endswith("_holdings.csv")
        )

    if not csv_files:
        return False

    instrument = store.get_or_create_instrument(symbol_upper)
    instrument_id = int(instrument["id"])
    existing_all = store.get_raw_series(instrument_id, "pe_ttm")
    existing_seed = store.get_raw_series(instrument_id, "pe_ttm", source="seed_csv")

    earliest_candidates = [d for d in (_read_earliest_csv_date(p) for p in csv_files) if d]
    csv_earliest = min(earliest_candidates) if earliest_candidates else None

    should_upgrade_seed = bool(
        existing_seed and csv_earliest and csv_earliest < existing_seed[0]["date"]
    )

    if not force and existing_all and not should_upgrade_seed:
        return False

    if (force or should_upgrade_seed) and existing_seed:
        removed = store.delete_raw_series_by_source(instrument_id, "seed_csv")
        logger.info(
            "Replacing %d existing seed_csv rows for %s (earliest seed date -> %s)",
            removed,
            symbol_upper,
            csv_earliest,
        )

    imported_any = False
    for csv_file in csv_files:
        logger.info("Bootstrapping seed CSV for %s: %s", symbol_upper, csv_file)
        run_csv_import(
            store=store,
            csv_path=csv_file,
            instrument_symbol=symbol_upper,
            source="seed_csv",
            recompute=False,
        )
        imported_any = True

    if imported_any:
        run_compute_indicators(store, instrument_symbol=symbol_upper)
    return imported_any


def bootstrap_all_seed_data_if_empty(
    store: ValuationStore,
    seed_dir: Path = SEED_DIR,
    force: bool = False,
) -> dict[str, bool]:
    """Bootstrap seed CSVs for all configured instruments that have empty or upgradable seed data."""
    return {
        symbol: bootstrap_seed_data_if_empty(
            store=store,
            seed_dir=seed_dir,
            instrument_symbol=symbol,
            force=force,
        )
        for symbol in INSTRUMENTS
    }


def run_backfill_history(
    store: ValuationStore,
    instrument_symbol: str = DEFAULT_INSTRUMENT_SYMBOL,
    years: int = 20,
    include_paid: bool = False,
    sources: list[str] | None = None,
    adapters: list[HistoricalSeriesSource] | None = None,
    recompute: bool = True,
) -> dict[str, Any]:
    """Fetch historical index P/E series from configured adapters (defaults to free-only)."""
    symbol_upper = instrument_symbol.upper()
    today = date.today()
    start_year = max(1970, today.year - max(1, int(years)))
    start_date = date(start_year, 1, 1).isoformat()

    overall_job_id = store.start_job_run(
        "backfill_history",
        {
            "instrument": symbol_upper,
            "years": years,
            "start_date": start_date,
            "include_paid": include_paid,
            "sources": sources,
        },
    )

    instrument = store.get_or_create_instrument(symbol_upper)
    instrument_id = int(instrument["id"])

    if adapters is not None:
        requested = {s.strip() for s in sources} if sources else None
        selected_adapters = [
            adp
            for adp in adapters
            if adp.supports(symbol_upper)
            and (requested is None or adp.source_id in requested)
            and (adp.tier != "paid" or (include_paid and adp.is_configured()))
        ]
    else:
        selected_adapters = get_historical_sources(
            instrument_symbol=symbol_upper,
            include_paid=include_paid,
            source_ids=sources,
        )

    source_results: dict[str, dict[str, Any]] = {}
    total_saved = 0
    succeeded = 0
    failed = 0

    for adapter in selected_adapters:
        if (
            sources is None
            and adapter.source_id == "yfinance_history"
            and source_results.get("sec_edgar", {}).get("saved", {}).get("pe_ttm", 0) > 0
        ):
            source_results["yfinance_history"] = {
                "status": "skipped",
                "saved": {},
                "reason": "sec_edgar already populated pe_ttm",
            }
            continue

        src_job_id = store.start_job_run(
            adapter.source_id,
            {"instrument": symbol_upper, "start_date": start_date},
        )
        try:
            series_map = adapter.fetch_history(symbol_upper, start_date=start_date)
            saved_by_series: dict[str, int] = {}
            for sid, rows in series_map.items():
                if not rows:
                    continue
                cnt = store.save_raw_series(
                    instrument_id=instrument_id,
                    series_id=sid,
                    rows=rows,
                    source=adapter.source_id,
                )
                saved_by_series[sid] = cnt
                total_saved += cnt

            if saved_by_series:
                store.finish_job_run(
                    src_job_id,
                    status="success",
                    details={"instrument": symbol_upper, "saved": saved_by_series},
                )
                source_results[adapter.source_id] = {
                    "status": "success",
                    "saved": saved_by_series,
                }
                succeeded += 1
            else:
                store.finish_job_run(
                    src_job_id,
                    status="partial",
                    error="No historical points returned",
                    details={"instrument": symbol_upper, "saved": {}},
                )
                source_results[adapter.source_id] = {
                    "status": "empty",
                    "saved": {},
                }
        except Exception as exc:
            err_msg = str(exc)
            logger.warning(
                "Historical backfill failed for %s / %s: %s",
                symbol_upper,
                adapter.source_id,
                err_msg,
            )
            store.finish_job_run(src_job_id, status="failed", error=err_msg)
            source_results[adapter.source_id] = {
                "status": "failed",
                "error": err_msg,
                "saved": {},
            }
            failed += 1

    computed: dict[str, int] = {}
    if recompute and total_saved > 0:
        computed = run_compute_indicators(store, instrument_symbol=symbol_upper)

    if failed > 0 and succeeded == 0 and total_saved == 0:
        overall_status = "failed"
    elif failed > 0:
        overall_status = "partial"
    else:
        overall_status = "success"

    summary = {
        "status": overall_status,
        "instrument": symbol_upper,
        "start_date": start_date,
        "include_paid": include_paid,
        "total_saved": total_saved,
        "sources": source_results,
        "indicators_computed": computed,
    }
    store.finish_job_run(overall_job_id, status=overall_status, details=summary)
    return summary


def run_backfill_equities(
    store: ValuationStore,
    symbols: list[str] | None = None,
    years: int = 15,
    include_paid: bool = False,
    sources: list[str] | None = None,
    job_id: int | None = None,
) -> dict[str, Any]:
    """Backfill historical P/E across enabled US equities via free adapters (SEC EDGAR -> Yahoo)."""
    if symbols is not None:
        target_symbols = list(
            dict.fromkeys(s.strip().upper() for s in symbols if s.strip())
        )
        for sym in target_symbols:
            store.get_or_create_instrument(sym, kind="equity")
    else:
        target_symbols = [
            row["symbol"] for row in store.list_instruments(kind="equity", enabled_only=True)
        ]

    base_details: dict[str, Any] = {
        "symbols": target_symbols,
        "years": years,
        "total": len(target_symbols),
        "completed": 0,
    }
    eff_job_id = (
        job_id
        if job_id is not None
        else store.start_job_run("backfill_equities", base_details)
    )

    results: dict[str, dict[str, Any]] = {}
    total_saved = 0
    succeeded = 0
    failed = 0

    for idx, sym in enumerate(target_symbols):
        try:
            res = run_backfill_history(
                store,
                instrument_symbol=sym,
                years=years,
                include_paid=include_paid,
                sources=sources,
            )
            results[sym] = {
                "status": res["status"],
                "total_saved": res["total_saved"],
            }
            total_saved += int(res.get("total_saved", 0))
            if res["status"] == "failed":
                failed += 1
            else:
                succeeded += 1
        except Exception as exc:
            logger.warning("Equity backfill failed for %s: %s", sym, exc)
            results[sym] = {"status": "failed", "total_saved": 0, "error": str(exc)}
            failed += 1

        store.update_job_run_details(
            eff_job_id,
            {
                "symbols": target_symbols,
                "years": years,
                "total": len(target_symbols),
                "completed": idx + 1,
                "current_symbol": sym,
                "succeeded": succeeded,
                "failed": failed,
                "total_saved": total_saved,
                "results": results,
            },
        )

    if failed > 0 and succeeded == 0 and total_saved == 0:
        overall_status = "failed"
    elif failed > 0:
        overall_status = "partial"
    else:
        overall_status = "success"

    summary = {
        "job_id": eff_job_id,
        "status": overall_status,
        "symbols": target_symbols,
        "years": years,
        "total": len(target_symbols),
        "completed": len(target_symbols),
        "succeeded": succeeded,
        "failed": failed,
        "total_saved": total_saved,
        "results": results,
    }
    store.finish_job_run(eff_job_id, status=overall_status, details=summary)
    return summary


def create_scheduler(store: ValuationStore) -> BackgroundScheduler:
    """Create APScheduler configured to refresh indices and watchlist equities after US close."""
    scheduler = BackgroundScheduler(timezone=SCHEDULE_TIMEZONE)
    for idx, symbol in enumerate(INSTRUMENTS):
        total_min = SCHEDULE_CRON_HOUR * 60 + SCHEDULE_CRON_MINUTE + idx * 5
        scheduler.add_job(
            func=run_daily_refresh,
            kwargs={"store": store, "instrument_symbol": symbol},
            trigger=CronTrigger(
                day_of_week="mon-fri",
                hour=(total_min // 60) % 24,
                minute=total_min % 60,
                timezone=SCHEDULE_TIMEZONE,
            ),
            id=f"{symbol.lower()}_daily_refresh",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )

    eq_total_min = SCHEDULE_CRON_HOUR * 60 + SCHEDULE_CRON_MINUTE + 15
    scheduler.add_job(
        func=run_equity_refresh,
        kwargs={"store": store},
        trigger=CronTrigger(
            day_of_week="mon-fri",
            hour=(eq_total_min // 60) % 24,
            minute=eq_total_min % 60,
            timezone=SCHEDULE_TIMEZONE,
        ),
        id="equities_daily_refresh",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    return scheduler


def _cli() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="Valuation Dashboard job runner")
    parser.add_argument("--db", default=str(DB_PATH), help="Path to SQLite database")
    sub = parser.add_subparsers(dest="command", required=True)

    p_refresh = sub.add_parser("refresh", help="Fetch live QQQ/SPY + yfinance data and compute indicators")
    p_refresh.add_argument("--instrument", default=DEFAULT_INSTRUMENT_SYMBOL)
    p_refresh.add_argument("--all", action="store_true", help="Refresh all configured instruments")

    p_import = sub.add_parser("import-csv", help="Import historical CSV series")
    p_import.add_argument("csv_path", help="Path to CSV file")
    p_import.add_argument("--instrument", default=DEFAULT_INSTRUMENT_SYMBOL)
    p_import.add_argument("--source", default="csv_import")
    p_import.add_argument("--series-id", default=None, help="Target series_id for 2-column CSVs")

    p_seed = sub.add_parser("seed", help="Bootstrap or upgrade seed CSV files")
    p_seed.add_argument("--instrument", default=DEFAULT_INSTRUMENT_SYMBOL)
    p_seed.add_argument("--all", action="store_true", help="Seed all configured instruments")
    p_seed.add_argument("--force", action="store_true", help="Force re-import of seed_csv")

    p_backfill = sub.add_parser(
        "backfill-history",
        help="Fetch 20y historical valuation data from external adapters (free sources by default)",
    )
    p_backfill.add_argument("--instrument", default=DEFAULT_INSTRUMENT_SYMBOL)
    p_backfill.add_argument("--all", action="store_true", help="Backfill all configured instruments")
    p_backfill.add_argument("--years", type=int, default=20)
    p_backfill.add_argument(
        "--include-paid",
        action="store_true",
        help="Also query configured paid historical sources (requires API key)",
    )
    p_backfill.add_argument(
        "--sources",
        default=None,
        help="Comma-separated list of historical source IDs to query",
    )

    p_sources = sub.add_parser("sources", help="Show active source priority and database coverage")
    p_sources.add_argument("--instrument", default=DEFAULT_INSTRUMENT_SYMBOL)

    p_prio = sub.add_parser("set-priority", help="Set data source priority preset or custom order")
    p_prio.add_argument("--profile", default=None, help="Preset profile (free_first | external_first)")
    p_prio.add_argument("--priority", default=None, help="Comma-separated source priority list")
    p_prio.add_argument("--no-recompute", action="store_true", help="Skip indicator recomputation")

    p_compute = sub.add_parser("compute", help="Recompute all registered indicators (backfill)")
    p_compute.add_argument("--instrument", default=DEFAULT_INSTRUMENT_SYMBOL)
    p_compute.add_argument("--all", action="store_true", help="Compute all enabled instruments")

    p_ref_eq = sub.add_parser("refresh-equities", help="Batch-refresh US equity watchlist")
    p_ref_eq.add_argument("--symbols", default=None, help="Comma-separated equity tickers")

    p_bf_eq = sub.add_parser("backfill-equities", help="Batch-backfill free history for US equity watchlist")
    p_bf_eq.add_argument("--symbols", default=None, help="Comma-separated equity tickers")
    p_bf_eq.add_argument("--years", type=int, default=15)

    p_wl = sub.add_parser("watchlist", help="Manage US equity watchlist")
    wl_sub = p_wl.add_subparsers(dest="watchlist_cmd", required=True)
    wl_sub.add_parser("list", help="List watchlist equities")
    wl_add = wl_sub.add_parser("add", help="Add a US equity to watchlist")
    wl_add.add_argument("ticker", help="Ticker symbol (e.g. NVDA, AAPL)")
    wl_add.add_argument("--name", default=None)
    wl_add.add_argument("--sector", default=None)
    wl_add.add_argument("--group", default=None)
    wl_add.add_argument("--backfill", action="store_true", help="Run free backfill and refresh immediately")
    wl_rm = wl_sub.add_parser("remove", help="Remove an equity from watchlist")
    wl_rm.add_argument("ticker", help="Ticker symbol")

    p_export = sub.add_parser("export", help="Export single-file static HTML snapshot")
    p_export.add_argument("--instrument", default=DEFAULT_INSTRUMENT_SYMBOL)
    p_export.add_argument("--output", default=str(EXPORT_OUTPUT_PATH))
    p_export.add_argument("--indices-only", action="store_true", help="Export index instruments only")
    p_export.add_argument("--symbols", default=None, help="Comma-separated list of symbols to include")

    args = parser.parse_args()
    store = ValuationStore(args.db)

    if args.command == "refresh":
        if args.all or args.instrument.upper() == "ALL":
            res = {sym: run_daily_refresh(store, instrument_symbol=sym) for sym in INSTRUMENTS}
        else:
            res = run_daily_refresh(store, instrument_symbol=args.instrument)
        print(res)
    elif args.command == "import-csv":
        res = run_csv_import(
            store,
            csv_path=args.csv_path,
            instrument_symbol=args.instrument,
            source=args.source,
            series_id=args.series_id,
        )
        print(res)
    elif args.command == "seed":
        if args.all or args.instrument.upper() == "ALL":
            results = bootstrap_all_seed_data_if_empty(store, force=args.force)
            print({"seeded": results})
        else:
            did_seed = bootstrap_seed_data_if_empty(
                store, instrument_symbol=args.instrument, force=args.force
            )
            print({"seeded": did_seed})
    elif args.command == "backfill-history":
        src_list = (
            [s.strip() for s in args.sources.split(",") if s.strip()]
            if args.sources
            else None
        )
        if args.all or args.instrument.upper() == "ALL":
            res = {
                sym: run_backfill_history(
                    store,
                    instrument_symbol=sym,
                    years=args.years,
                    include_paid=args.include_paid,
                    sources=src_list,
                )
                for sym in INSTRUMENTS
            }
        else:
            res = run_backfill_history(
                store,
                instrument_symbol=args.instrument,
                years=args.years,
                include_paid=args.include_paid,
                sources=src_list,
            )
        print(json.dumps(res, ensure_ascii=False, indent=2))
    elif args.command == "sources":
        from app.services import get_sources_payload

        print(
            json.dumps(
                get_sources_payload(store, instrument_symbol=args.instrument),
                ensure_ascii=False,
                indent=2,
            )
        )
    elif args.command == "set-priority":
        prio_list = (
            [s.strip() for s in args.priority.split(",") if s.strip()]
            if args.priority
            else None
        )
        eff_profile, eff_prio = store.set_source_priority(
            priority=prio_list,
            profile=args.profile,
        )
        computed = (
            {}
            if args.no_recompute
            else {
                row["symbol"]: run_compute_indicators(store, instrument_symbol=row["symbol"])
                for row in store.list_instruments(enabled_only=True)
            }
        )
        print(
            json.dumps(
                {
                    "profile": eff_profile,
                    "priority": eff_prio,
                    "recomputed": computed,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    elif args.command == "compute":
        if args.all or args.instrument.upper() == "ALL":
            targets = [row["symbol"] for row in store.list_instruments(enabled_only=True)]
            res = {sym: run_compute_indicators(store, instrument_symbol=sym) for sym in targets}
        else:
            res = run_compute_indicators(store, instrument_symbol=args.instrument)
        print(res)
    elif args.command == "refresh-equities":
        sym_list = (
            [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
            if args.symbols
            else None
        )
        print(json.dumps(run_equity_refresh(store, symbols=sym_list), ensure_ascii=False, indent=2))
    elif args.command == "backfill-equities":
        sym_list = (
            [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
            if args.symbols
            else None
        )
        print(
            json.dumps(
                run_backfill_equities(store, symbols=sym_list, years=args.years),
                ensure_ascii=False,
                indent=2,
            )
        )
    elif args.command == "watchlist":
        if args.watchlist_cmd == "list":
            print(json.dumps(store.list_instruments(kind="equity"), ensure_ascii=False, indent=2))
        elif args.watchlist_cmd == "add":
            tk = args.ticker.strip().upper()
            prof = fetch_ticker_profile(tk)
            inst = store.upsert_instrument(
                tk,
                name=args.name or prof.get("en_name") or tk,
                kind="equity",
                en_name=prof.get("en_name", ""),
                sector=args.sector or prof.get("sector", ""),
                group_name=args.group or "",
                currency=prof.get("currency", "USD"),
            )
            out: dict[str, Any] = {"instrument": inst}
            if args.backfill:
                out["backfill"] = run_backfill_history(store, instrument_symbol=tk, years=15)
                out["refresh"] = run_equity_refresh(store, symbols=[tk])
            print(json.dumps(out, ensure_ascii=False, indent=2))
        elif args.watchlist_cmd == "remove":
            removed = store.delete_instrument(args.ticker)
            print(json.dumps({"removed": removed, "symbol": args.ticker.strip().upper()}))
    elif args.command == "export":
        from app.export import export_static_html

        sym_list = (
            [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
            if args.symbols
            else None
        )
        out = export_static_html(
            store=store,
            instrument_symbol=args.instrument,
            output_path=Path(args.output),
            include_equities=not args.indices_only,
            symbols=sym_list,
        )
        print({"exported": str(out)})


if __name__ == "__main__":
    _cli()
