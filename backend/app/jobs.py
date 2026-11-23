"""Scheduled jobs, daily refresh orchestration, CSV import, and CLI commands."""

from __future__ import annotations

import argparse
import csv
import json
import logging
from datetime import date
from pathlib import Path
from typing import Any

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
)
from app.engine import IndicatorEngine
from app.models import ConstituentQuote
from app.sources.base import FundamentalsSource, HoldingsSource
from app.sources.csv_import import _normalize_date, import_csv_file
from app.sources.history_sources import HistoricalSeriesSource, get_historical_sources
from app.sources.qqq_holdings import QQQHoldingsSource
from app.sources.spy_holdings import SPYHoldingsSource
from app.sources.yfinance_source import YFinanceFundamentalsSource
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


def run_daily_refresh(
    store: ValuationStore,
    instrument_symbol: str = DEFAULT_INSTRUMENT_SYMBOL,
    holdings_source: HoldingsSource | None = None,
    fundamentals_source: FundamentalsSource | None = None,
) -> dict[str, Any]:
    """Fetch daily QQQ holdings + yfinance price/EPS, persist snapshots, and recompute indicators.

    If either upstream data source fails, the failure is recorded in `job_runs` while all
    previously stored snapshots and indicator values remain intact.
    """
    symbol_upper = instrument_symbol.upper()
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
    symbol_upper = instrument_symbol.upper()
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


def create_scheduler(store: ValuationStore) -> BackgroundScheduler:
    """Create APScheduler configured to refresh all instruments on US trading days after close."""
    scheduler = BackgroundScheduler(timezone=SCHEDULE_TIMEZONE)
    for idx, symbol in enumerate(INSTRUMENTS):
        scheduler.add_job(
            func=run_daily_refresh,
            kwargs={"store": store, "instrument_symbol": symbol},
            trigger=CronTrigger(
                day_of_week="mon-fri",
                hour=SCHEDULE_CRON_HOUR,
                minute=(SCHEDULE_CRON_MINUTE + idx * 5) % 60,
                timezone=SCHEDULE_TIMEZONE,
            ),
            id=f"{symbol.lower()}_daily_refresh",
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
    p_compute.add_argument("--all", action="store_true", help="Compute all configured instruments")

    p_export = sub.add_parser("export", help="Export single-file static HTML snapshot")
    p_export.add_argument("--instrument", default=DEFAULT_INSTRUMENT_SYMBOL)
    p_export.add_argument("--output", default=str(EXPORT_OUTPUT_PATH))

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
            else {sym: run_compute_indicators(store, instrument_symbol=sym) for sym in INSTRUMENTS}
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
            res = {sym: run_compute_indicators(store, instrument_symbol=sym) for sym in INSTRUMENTS}
        else:
            res = run_compute_indicators(store, instrument_symbol=args.instrument)
        print(res)
    elif args.command == "export":
        from app.export import export_static_html

        out = export_static_html(
            store=store,
            instrument_symbol=args.instrument,
            output_path=Path(args.output),
        )
        print({"exported": str(out)})


if __name__ == "__main__":
    _cli()
