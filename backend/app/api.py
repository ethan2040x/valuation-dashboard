"""FastAPI application and REST API routes."""

from __future__ import annotations

import logging
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
    run_backfill_history,
    run_compute_indicators,
    run_daily_refresh,
)
from app.services import (
    RANGE_YEARS,
    get_indicator_series_payload,
    get_indicators_payload,
    get_instruments_payload,
    get_snapshot_payload,
    get_sources_payload,
)
from app.store import ValuationStore

logger = logging.getLogger(__name__)


class SourcePriorityUpdateRequest(BaseModel):
    profile: str | None = None
    priority: list[str] | None = None
    instrument: str = DEFAULT_INSTRUMENT_SYMBOL
    recompute: bool = True


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
    def list_all_instruments() -> list[dict[str, Any]]:
        """Return all configured instruments."""
        return get_instruments_payload(app.state.store)

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
                        *[row["symbol"] for row in app.state.store.list_instruments()],
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
