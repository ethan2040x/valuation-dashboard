"""Scheduled jobs, daily refresh orchestration, CSV import, and CLI commands."""

from __future__ import annotations

import argparse
import logging
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
from app.sources.csv_import import import_csv_file
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
    h_source = holdings_source or get_holdings_source(instrument_symbol)
    f_source = fundamentals_source or YFinanceFundamentalsSource()
    source_tag = _source_tag_for_instrument(instrument_symbol)

    overall_job_id = store.start_job_run("daily_refresh", {"instrument": instrument_symbol})
    instrument = store.get_or_create_instrument(instrument_symbol)
    instrument_id = int(instrument["id"])

    # Step 1: Fetch QQQ constituent weights
    holdings_job_id = store.start_job_run(h_source.name)
    try:
        as_of_date, holdings = h_source.fetch_holdings()
        store.finish_job_run(
            holdings_job_id,
            status="success",
            details={"as_of_date": as_of_date, "count": len(holdings)},
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
    yf_job_id = store.start_job_run(f_source.name)
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
    computed = run_compute_indicators(store, instrument_symbol=instrument_symbol)

    overall_status = "partial" if failed_tickers else "success"
    store.finish_job_run(
        overall_job_id,
        status=overall_status,
        details={
            "instrument": instrument_symbol,
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
    job_id = store.start_job_run("compute_indicators", {"instrument": instrument_symbol})
    try:
        engine = IndicatorEngine(store)
        counts = engine.compute_all(instrument_symbol=instrument_symbol)
        store.finish_job_run(job_id, status="success", details={"counts": counts})
        return counts
    except Exception as exc:
        store.finish_job_run(job_id, status="failed", error=str(exc))
        raise


def run_csv_import(
    store: ValuationStore,
    csv_path: str | Path,
    instrument_symbol: str = DEFAULT_INSTRUMENT_SYMBOL,
    source: str = "csv_import",
    recompute: bool = True,
) -> dict[str, Any]:
    """Import historical CSV data into `raw_series` and optionally recompute indicators."""
    job_id = store.start_job_run(
        "csv_import",
        {"path": str(csv_path), "instrument": instrument_symbol, "source": source},
    )
    try:
        counts = import_csv_file(
            store=store,
            csv_path=csv_path,
            instrument_symbol=instrument_symbol,
            default_source=source,
        )
        computed = (
            run_compute_indicators(store, instrument_symbol=instrument_symbol)
            if recompute
            else {}
        )
        store.finish_job_run(
            job_id,
            status="success",
            details={"imported": counts, "computed": computed},
        )
        return {"imported": counts, "computed": computed}
    except Exception as exc:
        store.finish_job_run(job_id, status="failed", error=str(exc))
        raise


def bootstrap_seed_data_if_empty(
    store: ValuationStore,
    seed_dir: Path = SEED_DIR,
    instrument_symbol: str = DEFAULT_INSTRUMENT_SYMBOL,
) -> bool:
    """Import seed CSVs from `data/seed/` if `raw_series` is currently empty for an instrument."""
    symbol_upper = instrument_symbol.upper()
    instrument = store.get_or_create_instrument(symbol_upper)
    existing = store.get_raw_series(int(instrument["id"]), "pe_ttm")
    if existing:
        return False

    if not seed_dir.exists():
        return False

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
) -> dict[str, bool]:
    """Bootstrap seed CSVs for all configured instruments that have empty data."""
    return {
        symbol: bootstrap_seed_data_if_empty(
            store=store,
            seed_dir=seed_dir,
            instrument_symbol=symbol,
        )
        for symbol in INSTRUMENTS
    }


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

    p_refresh = sub.add_parser("refresh", help="Fetch live QQQ + yfinance data and compute indicators")
    p_refresh.add_argument("--instrument", default=DEFAULT_INSTRUMENT_SYMBOL)

    p_import = sub.add_parser("import-csv", help="Import historical CSV series")
    p_import.add_argument("csv_path", help="Path to CSV file")
    p_import.add_argument("--instrument", default=DEFAULT_INSTRUMENT_SYMBOL)
    p_import.add_argument("--source", default="csv_import")

    p_seed = sub.add_parser("seed", help="Bootstrap seed CSV files if database is empty")
    p_seed.add_argument("--instrument", default=DEFAULT_INSTRUMENT_SYMBOL)

    p_compute = sub.add_parser("compute", help="Recompute all registered indicators (backfill)")
    p_compute.add_argument("--instrument", default=DEFAULT_INSTRUMENT_SYMBOL)

    p_export = sub.add_parser("export", help="Export single-file static HTML snapshot")
    p_export.add_argument("--instrument", default=DEFAULT_INSTRUMENT_SYMBOL)
    p_export.add_argument("--output", default=str(EXPORT_OUTPUT_PATH))

    args = parser.parse_args()
    store = ValuationStore(args.db)

    if args.command == "refresh":
        if args.instrument.upper() == "ALL":
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
        )
        print(res)
    elif args.command == "seed":
        if args.instrument.upper() == "ALL":
            results = bootstrap_all_seed_data_if_empty(store)
            print({"seeded": results})
        else:
            did_seed = bootstrap_seed_data_if_empty(store, instrument_symbol=args.instrument)
            print({"seeded": did_seed})
    elif args.command == "compute":
        if args.instrument.upper() == "ALL":
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
