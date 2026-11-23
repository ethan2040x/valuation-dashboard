"""FastAPI application and REST API routes."""

from __future__ import annotations

import logging
import re
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.config import (
    DB_PATH,
    DEFAULT_INSTRUMENT_SYMBOL,
    INSTRUMENTS,
    SCHEDULE_ENABLED,
    STATIC_DIST_DIR,
)
from app.indicators import discover_indicators
from app.jobs import (
    bootstrap_all_seed_data_if_empty,
    create_scheduler,
    run_backfill_equities,
    run_backfill_history,
    run_compute_indicators,
    run_csv_text_import,
    run_daily_refresh,
    run_equity_refresh,
)
from app.services import (
    RANGE_YEARS,
    get_indicator_series_payload,
    get_indicators_payload,
    get_instruments_payload,
    get_snapshot_payload,
    get_sources_payload,
    get_watchlist_overview_payload,
)
from app.sources.yfinance_source import fetch_ticker_profile
from app.store import ValuationStore

logger = logging.getLogger(__name__)


class SourcePriorityUpdateRequest(BaseModel):
    profile: str | None = None
    priority: list[str] | None = None
    instrument: str = DEFAULT_INSTRUMENT_SYMBOL
    recompute: bool = True


class AddInstrumentsRequest(BaseModel):
    symbol: str | None = None
    symbols: list[str] | None = None
    name: str | None = None
    en_name: str | None = None
    sector: str | None = None
    group_name: str | None = None
    currency: str | None = None
    backfill: bool = True
    refresh: bool = True


class UpdateInstrumentRequest(BaseModel):
    name: str | None = None
    en_name: str | None = None
    sector: str | None = None
    group_name: str | None = None
    description: str | None = None


class EquityJobRequest(BaseModel):
    symbols: list[str] | None = None
    years: int = 15
    async_run: bool = True


class CsvImportRequest(BaseModel):
    csv_text: str
    instrument: str | None = None
    source: str = "csv_import"
    series_id: str | None = None


def create_app(
    db_path: str | Path = DB_PATH,
    enable_scheduler: bool = SCHEDULE_ENABLED,
    auto_seed: bool = True,
    frontend_dist_dir: Path | None = STATIC_DIST_DIR,
) -> FastAPI:
    """Create and configure the FastAPI application."""
    store = ValuationStore(db_path)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        discover_indicators()
        if auto_seed:
            try:
                bootstrap_all_seed_data_if_empty(store)
            except Exception as exc:
                logger.warning("Auto-seed skipped due to error: %s", exc)

        scheduler = None
        if enable_scheduler:
            scheduler = create_scheduler(store)
            scheduler.start()
            logger.info("APScheduler started for daily valuation refresh")
        app.state.store = store
        app.state.scheduler = scheduler
        try:
            yield
        finally:
            if scheduler is not None:
                scheduler.shutdown(wait=False)

    app = FastAPI(
        title="纳指 100 估值看板 API",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.store = store

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/instruments")
    def list_all_instruments(
        kind: str | None = Query(default=None, pattern="^(index|equity)$"),
    ) -> list[dict[str, Any]]:
        """Return all enabled instruments (optionally filtered by kind)."""
        return get_instruments_payload(app.state.store, kind=kind)

    @app.post("/api/instruments")
    def add_instruments(body: AddInstrumentsRequest) -> dict[str, Any]:
        """Add one or more US equity symbols to the watchlist and optionally backfill/refresh."""
        raw_tokens: list[str] = []
        if body.symbol:
            raw_tokens.extend(re.split(r"[\s,;]+", body.symbol.strip()))
        if body.symbols:
            for item in body.symbols:
                raw_tokens.extend(re.split(r"[\s,;]+", item.strip()))

        tickers = list(
            dict.fromkeys(tok.strip().upper() for tok in raw_tokens if tok.strip())
        )
        if not tickers:
            raise HTTPException(status_code=400, detail="请至少提供一个有效的股票代码。")

        invalid = [
            tk for tk in tickers if not re.match(r"^[A-Z0-9]{1,6}([.\-][A-Z0-9]{1,2})?$", tk)
        ]
        if invalid:
            raise HTTPException(
                status_code=400,
                detail=f"股票代码格式无效: {', '.join(invalid)}",
            )

        single_mode = len(tickers) == 1
        added_or_updated: list[dict[str, Any]] = []

        for tk in tickers:
            existing = app.state.store.get_instrument(tk)
            if existing is not None and existing.get("kind") == "index":
                added_or_updated.append(existing)
                continue

            prof: dict[str, Any] = {}
            if (body.refresh or body.backfill) and (
                not existing or not existing.get("en_name") or not existing.get("sector")
            ):
                prof = fetch_ticker_profile(tk)

            eff_name = (
                (body.name.strip() if single_mode and body.name else None)
                or (existing["name"] if existing else None)
                or prof.get("en_name")
                or tk
            )
            eff_en_name = (
                (body.en_name.strip() if single_mode and body.en_name else None)
                or (existing.get("en_name") if existing else None)
                or prof.get("en_name")
                or ""
            )
            eff_sector = (
                (body.sector.strip() if body.sector else None)
                or (existing.get("sector") if existing else None)
                or prof.get("sector")
                or ""
            )
            eff_group = (
                (body.group_name.strip() if body.group_name else None)
                or (existing.get("group_name") if existing else None)
                or "自选股"
            )
            eff_currency = (
                (body.currency.strip().upper() if body.currency else None)
                or (existing.get("currency") if existing else None)
                or prof.get("currency")
                or "USD"
            )
            inst = app.state.store.upsert_instrument(
                tk,
                name=eff_name,
                kind="equity",
                en_name=eff_en_name,
                sector=eff_sector,
                group_name=eff_group,
                currency=eff_currency,
            )
            added_or_updated.append(inst)

        equity_tickers = [
            inst["symbol"] for inst in added_or_updated if inst.get("kind") == "equity"
        ]
        if not equity_tickers or (not body.backfill and not body.refresh):
            return {
                "instruments": added_or_updated,
                "job_id": None,
                "status": "saved",
            }

        if len(equity_tickers) == 1:
            sym = equity_tickers[0]
            backfill_res = (
                run_backfill_history(app.state.store, instrument_symbol=sym, years=15)
                if body.backfill
                else None
            )
            refresh_res = (
                run_equity_refresh(app.state.store, symbols=[sym])
                if body.refresh
                else None
            )
            return {
                "instruments": [app.state.store.get_instrument(sym) or added_or_updated[0]],
                "job_id": None,
                "status": "completed",
                "backfill": backfill_res,
                "refresh": refresh_res,
            }

        # Batch add (>1 company): run backfill/refresh in background thread and return job_id
        job_name = "backfill_equities" if body.backfill else "refresh_equities"
        job_id = app.state.store.start_job_run(
            job_name,
            {"symbols": equity_tickers, "total": len(equity_tickers), "completed": 0},
        )

        def _run_batch() -> None:
            try:
                if body.refresh:
                    run_equity_refresh(
                        app.state.store,
                        symbols=equity_tickers,
                        job_id=None if body.backfill else job_id,
                    )
                if body.backfill:
                    run_backfill_equities(
                        app.state.store,
                        symbols=equity_tickers,
                        years=15,
                        job_id=job_id,
                    )
            except Exception as exc:
                logger.exception("Background batch add job %s failed", job_id)
                app.state.store.finish_job_run(job_id, status="failed", error=str(exc))

        threading.Thread(target=_run_batch, daemon=True).start()
        return {
            "instruments": added_or_updated,
            "job_id": job_id,
            "status": "running",
        }

    @app.patch("/api/instruments/{symbol}")
    def update_instrument_meta(
        symbol: str,
        body: UpdateInstrumentRequest,
    ) -> dict[str, Any]:
        """Update editable metadata fields (name, sector, group_name, description)."""
        updated = app.state.store.update_instrument(
            symbol,
            name=body.name,
            en_name=body.en_name,
            sector=body.sector,
            group_name=body.group_name,
            description=body.description,
        )
        if updated is None:
            raise HTTPException(status_code=404, detail=f"Unknown instrument: {symbol}")
        return updated

    @app.delete("/api/instruments/{symbol}")
    def delete_equity_instrument(symbol: str) -> dict[str, Any]:
        """Delete an equity instrument from the watchlist and remove its stored series."""
        try:
            removed = app.state.store.delete_instrument(symbol)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not removed:
            raise HTTPException(status_code=404, detail=f"Unknown instrument: {symbol}")
        return {"removed": True, "symbol": symbol.strip().upper()}

    @app.get("/api/watchlist/overview")
    def watchlist_overview(
        kind: str = Query(default="equity", pattern="^(index|equity)$"),
    ) -> dict[str, Any]:
        """Return aggregated watchlist comparison rows across all enabled instruments of `kind`."""
        return get_watchlist_overview_payload(app.state.store, kind=kind)

    @app.get("/api/indicators")
    def list_all_indicators() -> list[dict[str, Any]]:
        """Return all registered indicators and their display configurations."""
        return get_indicators_payload()

    @app.get("/api/indicators/{indicator_id}/series")
    def indicator_series(
        indicator_id: str,
        request: Request,
        instrument: str = Query(default=DEFAULT_INSTRUMENT_SYMBOL),
        range: str = Query(default="5y"),
    ) -> dict[str, Any]:
        """Return historical time series and stats for a specific indicator."""
        range_lower = range.lower()
        if range_lower not in RANGE_YEARS:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported range {range!r}. Allowed: {sorted(RANGE_YEARS.keys())}",
            )

        # Forward any extra query params as indicator params (e.g. base=pe_ttm&window_years=5)
        reserved = {"instrument", "range"}
        extra_params: dict[str, Any] = {}
        for k, v in request.query_params.items():
            if k in reserved:
                continue
            # Convert numeric strings when possible (e.g. window_years=5)
            extra_params[k] = int(v) if v.isdigit() else v

        try:
            return get_indicator_series_payload(
                store=app.state.store,
                indicator_id=indicator_id,
                instrument_symbol=instrument,
                range_key=range_lower,
                params=extra_params if extra_params else None,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/snapshot")
    def snapshot(instrument: str = Query(default=DEFAULT_INSTRUMENT_SYMBOL)) -> dict[str, Any]:
        """Return latest values across all registered indicators plus data status."""
        try:
            return get_snapshot_payload(
                store=app.state.store,
                instrument_symbol=instrument,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/sources")
    def list_sources(instrument: str = Query(default=DEFAULT_INSTRUMENT_SYMBOL)) -> dict[str, Any]:
        """Return configured data sources, priority order, and DB coverage."""
        try:
            return get_sources_payload(
                store=app.state.store,
                instrument_symbol=instrument,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.put("/api/sources/priority")
    def update_source_priority(body: SourcePriorityUpdateRequest) -> dict[str, Any]:
        """Update data source priority preset or custom order and recompute indicators."""
        try:
            app.state.store.set_source_priority(
                priority=body.priority,
                profile=body.profile,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        if body.recompute:
            symbols = list(
                dict.fromkeys(
                    [
                        *INSTRUMENTS.keys(),
                        *[
                            row["symbol"]
                            for row in app.state.store.list_instruments(enabled_only=True)
                        ],
                    ]
                )
            )
            for sym in symbols:
                run_compute_indicators(app.state.store, instrument_symbol=sym)

        try:
            return get_sources_payload(
                store=app.state.store,
                instrument_symbol=body.instrument,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/jobs/refresh")
    def trigger_refresh(
        instrument: str = Query(default=DEFAULT_INSTRUMENT_SYMBOL),
    ) -> dict[str, Any]:
        """Manually trigger a live data fetch and indicator recomputation."""
        result = run_daily_refresh(
            store=app.state.store,
            instrument_symbol=instrument,
        )
        return result

    @app.post("/api/jobs/backfill-history")
    def trigger_backfill_history(
        instrument: str = Query(default=DEFAULT_INSTRUMENT_SYMBOL),
        years: int = Query(default=20, ge=1, le=50),
        include_paid: bool = Query(default=False),
    ) -> dict[str, Any]:
        """Fetch historical valuation series from configured adapters (free-only by default)."""
        return run_backfill_history(
            store=app.state.store,
            instrument_symbol=instrument,
            years=years,
            include_paid=include_paid,
        )

    @app.post("/api/jobs/refresh-equities")
    def trigger_refresh_equities(
        body: EquityJobRequest | None = None,
    ) -> dict[str, Any]:
        """Batch-refresh US equity watchlist (async background thread by default)."""
        req = body or EquityJobRequest()
        target_syms = (
            [s.strip().upper() for s in req.symbols if s.strip()]
            if req.symbols
            else [
                row["symbol"]
                for row in app.state.store.list_instruments(kind="equity", enabled_only=True)
            ]
        )
        if not req.async_run or len(target_syms) <= 1:
            return run_equity_refresh(app.state.store, symbols=target_syms)

        job_id = app.state.store.start_job_run(
            "refresh_equities",
            {"symbols": target_syms, "total": len(target_syms), "completed": 0},
        )

        def _worker() -> None:
            try:
                run_equity_refresh(app.state.store, symbols=target_syms, job_id=job_id)
            except Exception as exc:
                logger.exception("Async refresh_equities job %s failed", job_id)
                app.state.store.finish_job_run(job_id, status="failed", error=str(exc))

        threading.Thread(target=_worker, daemon=True).start()
        return {"job_id": job_id, "status": "running", "total": len(target_syms)}

    @app.post("/api/jobs/backfill-equities")
    def trigger_backfill_equities(
        body: EquityJobRequest | None = None,
    ) -> dict[str, Any]:
        """Batch-backfill free historical P/E across US equity watchlist."""
        req = body or EquityJobRequest()
        target_syms = (
            [s.strip().upper() for s in req.symbols if s.strip()]
            if req.symbols
            else [
                row["symbol"]
                for row in app.state.store.list_instruments(kind="equity", enabled_only=True)
            ]
        )
        if not req.async_run or len(target_syms) <= 1:
            return run_backfill_equities(
                app.state.store, symbols=target_syms, years=req.years
            )

        job_id = app.state.store.start_job_run(
            "backfill_equities",
            {
                "symbols": target_syms,
                "years": req.years,
                "total": len(target_syms),
                "completed": 0,
            },
        )

        def _worker() -> None:
            try:
                run_backfill_equities(
                    app.state.store,
                    symbols=target_syms,
                    years=req.years,
                    job_id=job_id,
                )
            except Exception as exc:
                logger.exception("Async backfill_equities job %s failed", job_id)
                app.state.store.finish_job_run(job_id, status="failed", error=str(exc))

        threading.Thread(target=_worker, daemon=True).start()
        return {"job_id": job_id, "status": "running", "total": len(target_syms)}

    @app.get("/api/jobs/{job_id}")
    def get_job_status(job_id: int) -> dict[str, Any]:
        """Poll background job run status & progress details."""
        job = app.state.store.get_job_run(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
        return job

    @app.post("/api/import/csv")
    def import_csv_endpoint(body: CsvImportRequest) -> dict[str, Any]:
        """Import historical CSV text for a single instrument or multi-ticker batch CSV."""
        try:
            return run_csv_text_import(
                store=app.state.store,
                csv_text=body.csv_text,
                instrument_symbol=body.instrument,
                source=body.source,
                series_id=body.series_id,
                recompute=True,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Serve built frontend bundle when available (for single-command deployment)
    if frontend_dist_dir and frontend_dist_dir.exists():
        assets_dir = frontend_dist_dir / "assets"
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

        index_html = frontend_dist_dir / "index.html"
        if index_html.exists():

            @app.get("/", include_in_schema=False)
            def serve_index() -> FileResponse:
                return FileResponse(str(index_html))

    return app


app = create_app()
