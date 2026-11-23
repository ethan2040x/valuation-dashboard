"""Static single-file HTML snapshot exporter.

Calls the exact same read service functions used by FastAPI routes, bundles the
resulting JSON payload, and embeds it into the single-file frontend HTML so the
exported file opens offline with zero backend dependency.
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from app.config import (
    DEFAULT_INSTRUMENT_SYMBOL,
    EXPORT_OUTPUT_PATH,
    SINGLEFILE_DIST_DIR,
    STATIC_DIST_DIR,
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
from app.store import ValuationStore

SNAPSHOT_PLACEHOLDER = "<!--VALUATION_SNAPSHOT_INJECT-->"


def _downsample_equity_series_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep recent 5y equity history daily and downsample >5y history to weekly for compact exports."""
    pts = payload.get("series") or []
    if not pts:
        return payload

    latest_ts = pd.Timestamp(pts[-1]["date"])
    cutoff = (latest_ts - pd.DateOffset(years=5)).date().isoformat()

    old_weekly: dict[tuple[int, int], dict[str, Any]] = {}
    recent_pts: list[dict[str, Any]] = []

    for pt in pts:
        cleaned = dict(pt)
        meta = cleaned.get("meta")
        if isinstance(meta, dict):
            trimmed = {mk: mv for mk, mv in meta.items() if mk != "raw_source"}
            if trimmed:
                cleaned["meta"] = trimmed
            else:
                cleaned.pop("meta", None)
        if pt["date"] >= cutoff:
            recent_pts.append(cleaned)
        else:
            iso = date.fromisoformat(pt["date"]).isocalendar()
            old_weekly[(iso[0], iso[1])] = cleaned

    combined = [*sorted(old_weekly.values(), key=lambda p: p["date"]), *recent_pts]
    return {**payload, "series": combined}


def encode_compact_series_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Encode verbose `series` point dicts into compact columnar `[date, value, src_idx]` rows."""
    pts = payload.get("series") or []
    source_table: list[str] = []
    source_idx: dict[str, int] = {}
    rows: list[list[Any]] = []

    for pt in pts:
        src = str(pt.get("source") or "")
        idx = source_idx.get(src)
        if idx is None:
            idx = len(source_table)
            source_table.append(src)
            source_idx[src] = idx
        val = pt.get("value")
        row: list[Any] = [pt["date"], val, idx]
        if pt.get("is_incomplete"):
            row.append(1)
        rows.append(row)

    encoded = {k: v for k, v in payload.items() if k != "series"}
    encoded["compact"] = {"s": source_table, "d": rows}
    return encoded


def _build_series_for_instrument(
    store: ValuationStore,
    indicators: list[dict[str, Any]],
    instrument_symbol: str,
    compact_ranges: bool = False,
    instrument_kind: str = "index",
    compact_points: bool = False,
) -> dict[str, dict[str, Any]]:
    series_map: dict[str, dict[str, Any]] = {}
    range_keys = ("all",) if compact_ranges else tuple(RANGE_YEARS.keys())
    for ind in indicators:
        if not ind.get("display", {}).get("chartable", True):
            continue
        ind_id = ind["id"]
        series_map[ind_id] = {}
        for range_key in range_keys:
            payload = get_indicator_series_payload(
                store=store,
                indicator_id=ind_id,
                instrument_symbol=instrument_symbol,
                range_key=range_key,
            )
            if compact_ranges and instrument_kind == "equity":
                payload = _downsample_equity_series_payload(payload)
            if compact_points:
                payload = encode_compact_series_payload(payload)
            series_map[ind_id][range_key] = payload
    return series_map


def build_static_snapshot_bundle(
    store: ValuationStore,
    instrument_symbol: str = DEFAULT_INSTRUMENT_SYMBOL,
    compact_ranges: bool = False,
    include_equities: bool = True,
    symbols: list[str] | None = None,
    compact_points: bool = False,
    deduplicate_primary: bool = False,
) -> dict[str, Any]:
    """Build the complete JSON data bundle using the same functions as the REST API."""
    indicators = get_indicators_payload()
    all_instruments = get_instruments_payload(store)
    primary_symbol = instrument_symbol.upper()
    sym_filter = (
        {s.strip().upper() for s in symbols if s.strip()} | {primary_symbol}
        if symbols
        else None
    )

    instruments = [
        inst
        for inst in all_instruments
        if (include_equities or inst.get("kind") != "equity" or inst["symbol"] == primary_symbol)
        and (sym_filter is None or inst["symbol"] in sym_filter)
    ]

    primary_inst = next(
        (inst for inst in instruments if inst["symbol"] == primary_symbol), None
    )
    primary_kind = primary_inst.get("kind", "index") if primary_inst else "index"

    snapshot = get_snapshot_payload(store, instrument_symbol=primary_symbol)
    series_map = _build_series_for_instrument(
        store,
        indicators,
        primary_symbol,
        compact_ranges=compact_ranges,
        instrument_kind=primary_kind,
        compact_points=compact_points,
    )
    sources = get_sources_payload(store, instrument_symbol=primary_symbol)

    snapshots_by_instrument: dict[str, Any] = {} if deduplicate_primary else {primary_symbol: snapshot}
    series_by_instrument: dict[str, dict[str, dict[str, Any]]] = (
        {} if deduplicate_primary else {primary_symbol: series_map}
    )
    sources_by_instrument: dict[str, Any] = {} if deduplicate_primary else {primary_symbol: sources}

    for inst in instruments:
        sym = inst["symbol"]
        if sym == primary_symbol:
            continue
        if store.get_instrument(sym) is None:
            continue
        snapshots_by_instrument[sym] = get_snapshot_payload(store, instrument_symbol=sym)
        series_by_instrument[sym] = _build_series_for_instrument(
            store,
            indicators,
            sym,
            compact_ranges=compact_ranges,
            instrument_kind=inst.get("kind", "index"),
            compact_points=compact_points,
        )
        sources_by_instrument[sym] = get_sources_payload(store, instrument_symbol=sym)

    if include_equities:
        watchlist_overview = get_watchlist_overview_payload(store, kind="equity")
        if sym_filter is not None:
            filtered_items = [
                item
                for item in watchlist_overview["items"]
                if item["instrument"]["symbol"] in sym_filter
            ]
            watchlist_overview["items"] = filtered_items
            watchlist_overview["count"] = len(filtered_items)
    else:
        watchlist_overview = {
            "kind": "equity",
            "generated_at": snapshot["generated_at"],
            "count": 0,
            "items": [],
        }

    return {
        "mode": "static",
        "instrument": primary_symbol,
        "instruments": instruments,
        "generated_at": snapshot["generated_at"],
        "indicators": indicators,
        "snapshot": snapshot,
        "series": series_map,
        "sources": sources,
        "snapshots": snapshots_by_instrument,
        "series_by_instrument": series_by_instrument,
        "sources_by_instrument": sources_by_instrument,
        "watchlist_overview": watchlist_overview,
    }


def _safe_inline_json(payload: dict[str, Any]) -> str:
    """Serialize JSON safely for embedding inside an HTML `<script>` block."""
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    # Escape closing script tags and HTML comments
    return raw.replace("</", "<\\/").replace("<!--", "<\\!--")


def _inject_head_script(html: str, inject_script: str) -> str:
    if SNAPSHOT_PLACEHOLDER in html:
        return html.replace(SNAPSHOT_PLACEHOLDER, inject_script, 1)
    if re.search(r"<head[^>]*>", html, flags=re.IGNORECASE):
        return re.sub(
            r"(<head[^>]*>)",
            lambda m: f"{m.group(1)}\n{inject_script}",
            html,
            count=1,
            flags=re.IGNORECASE,
        )
    return inject_script + "\n" + html


def _write_compact_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )


def export_static_html(
    store: ValuationStore,
    instrument_symbol: str = DEFAULT_INSTRUMENT_SYMBOL,
    template_path: Path | None = None,
    output_path: Path = EXPORT_OUTPUT_PATH,
    include_equities: bool = True,
    symbols: list[str] | None = None,
) -> Path:
    """Generate a self-contained single-file HTML snapshot dashboard."""
    tpl = template_path or (SINGLEFILE_DIST_DIR / "index.html")
    if not tpl.exists():
        raise FileNotFoundError(
            f"Single-file frontend build not found at {tpl}. "
            "Run `npm run build:static` in `frontend/` first."
        )

    bundle = build_static_snapshot_bundle(
        store,
        instrument_symbol=instrument_symbol,
        compact_ranges=True,
        include_equities=include_equities,
        symbols=symbols,
        compact_points=True,
        deduplicate_primary=True,
    )
    inject_script = f"<script>window.__VALUATION_SNAPSHOT__={_safe_inline_json(bundle)};</script>"
    rendered = _inject_head_script(tpl.read_text(encoding="utf-8"), inject_script)

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(rendered, encoding="utf-8")
    return out


def export_static_site(
    store: ValuationStore,
    output_dir: Path,
    frontend_dist_dir: Path | None = None,
    instrument_symbol: str = DEFAULT_INSTRUMENT_SYMBOL,
    include_equities: bool = True,
    symbols: list[str] | None = None,
) -> Path:
    """Export split, lazy-loaded static site (cacheable assets + compact JSON data files)."""
    dist_dir = Path(frontend_dist_dir or STATIC_DIST_DIR)
    index_tpl = dist_dir / "index.html"
    if not index_tpl.exists():
        raise FileNotFoundError(
            f"Frontend build not found at {index_tpl}. "
            "Run `npm run build` in `frontend/` first."
        )

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Copy built frontend static assets (e.g. hashed JS/CSS chunks)
    for item in dist_dir.iterdir():
        dest = out_dir / item.name
        if item.name == "index.html":
            continue
        if item.is_dir():
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(item, dest)
        else:
            shutil.copy2(item, dest)

    inject_script = '<script>window.__VALUATION_SITE__={"dataBase":"./data"};</script>'
    rendered_html = _inject_head_script(
        index_tpl.read_text(encoding="utf-8"), inject_script
    )
    (out_dir / "index.html").write_text(rendered_html, encoding="utf-8")

    indicators = get_indicators_payload()
    all_instruments = get_instruments_payload(store)
    primary_symbol = instrument_symbol.upper()
    sym_filter = (
        {s.strip().upper() for s in symbols if s.strip()} | {primary_symbol}
        if symbols
        else None
    )
    instruments = [
        inst
        for inst in all_instruments
        if (include_equities or inst.get("kind") != "equity" or inst["symbol"] == primary_symbol)
        and (sym_filter is None or inst["symbol"] in sym_filter)
    ]

    data_dir = out_dir / "data"
    instruments_dir = data_dir / "instruments"
    if instruments_dir.exists():
        shutil.rmtree(instruments_dir)
    instruments_dir.mkdir(parents=True, exist_ok=True)

    index_snapshots: dict[str, Any] = {}
    index_sources: dict[str, Any] = {}

    for inst in instruments:
        sym = inst["symbol"]
        kind = inst.get("kind", "index")
        if store.get_instrument(sym) is None:
            continue

        snap = get_snapshot_payload(store, instrument_symbol=sym)
        srcs = get_sources_payload(store, instrument_symbol=sym)
        series_map = _build_series_for_instrument(
            store,
            indicators,
            sym,
            compact_ranges=True,
            instrument_kind=kind,
            compact_points=True,
        )

        if kind == "index":
            index_snapshots[sym] = snap
            index_sources[sym] = srcs
            _write_compact_json(
                instruments_dir / f"{sym}.json",
                {
                    "symbol": sym,
                    "kind": "index",
                    "series": series_map,
                },
            )
        else:
            _write_compact_json(
                instruments_dir / f"{sym}.json",
                {
                    "symbol": sym,
                    "kind": "equity",
                    "snapshot": snap,
                    "sources": srcs,
                    "series": series_map,
                },
            )

    primary_snap = index_snapshots.get(primary_symbol) or get_snapshot_payload(
        store, instrument_symbol=primary_symbol
    )
    bootstrap_payload = {
        "mode": "static_site",
        "instrument": primary_symbol,
        "instruments": instruments,
        "generated_at": primary_snap["generated_at"],
        "indicators": indicators,
        "index_snapshots": index_snapshots,
        "index_sources": index_sources,
    }
    _write_compact_json(data_dir / "bootstrap.json", bootstrap_payload)

    if include_equities:
        watchlist_overview = get_watchlist_overview_payload(store, kind="equity")
        if sym_filter is not None:
            filtered_items = [
                item
                for item in watchlist_overview["items"]
                if item["instrument"]["symbol"] in sym_filter
            ]
            watchlist_overview["items"] = filtered_items
            watchlist_overview["count"] = len(filtered_items)
    else:
        watchlist_overview = {
            "kind": "equity",
            "generated_at": primary_snap["generated_at"],
            "count": 0,
            "items": [],
        }
    _write_compact_json(data_dir / "watchlist.json", watchlist_overview)

    return out_dir
