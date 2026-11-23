"""Static single-file HTML snapshot exporter.

Calls the exact same read service functions used by FastAPI routes, bundles the
resulting JSON payload, and embeds it into the single-file frontend HTML so the
exported file opens offline with zero backend dependency.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.config import (
    DEFAULT_INSTRUMENT_SYMBOL,
    EXPORT_OUTPUT_PATH,
    INSTRUMENTS,
    SINGLEFILE_DIST_DIR,
)
from app.services import (
    RANGE_YEARS,
    get_indicator_series_payload,
    get_indicators_payload,
    get_instruments_payload,
    get_snapshot_payload,
)
from app.store import ValuationStore

SNAPSHOT_PLACEHOLDER = "<!--VALUATION_SNAPSHOT_INJECT-->"


def _build_series_for_instrument(
    store: ValuationStore,
    indicators: list[dict[str, Any]],
    instrument_symbol: str,
) -> dict[str, dict[str, Any]]:
    series_map: dict[str, dict[str, Any]] = {}
    for ind in indicators:
        if not ind.get("display", {}).get("chartable", True):
            continue
        ind_id = ind["id"]
        series_map[ind_id] = {}
        for range_key in RANGE_YEARS.keys():
            series_map[ind_id][range_key] = get_indicator_series_payload(
                store=store,
                indicator_id=ind_id,
                instrument_symbol=instrument_symbol,
                range_key=range_key,
            )
    return series_map


def build_static_snapshot_bundle(
    store: ValuationStore,
    instrument_symbol: str = DEFAULT_INSTRUMENT_SYMBOL,
) -> dict[str, Any]:
    """Build the complete JSON data bundle using the same functions as the REST API."""
    indicators = get_indicators_payload()
    instruments = get_instruments_payload(store)
    primary_symbol = instrument_symbol.upper()
    snapshot = get_snapshot_payload(store, instrument_symbol=primary_symbol)
    series_map = _build_series_for_instrument(store, indicators, primary_symbol)

    snapshots_by_instrument: dict[str, Any] = {primary_symbol: snapshot}
    series_by_instrument: dict[str, dict[str, dict[str, Any]]] = {primary_symbol: series_map}

    for sym in INSTRUMENTS:
        if sym == primary_symbol:
            continue
        if store.get_instrument(sym) is None:
            continue
        snapshots_by_instrument[sym] = get_snapshot_payload(store, instrument_symbol=sym)
        series_by_instrument[sym] = _build_series_for_instrument(store, indicators, sym)

    return {
        "mode": "static",
        "instrument": primary_symbol,
        "instruments": instruments,
        "generated_at": snapshot["generated_at"],
        "indicators": indicators,
        "snapshot": snapshot,
        "series": series_map,
        "snapshots": snapshots_by_instrument,
        "series_by_instrument": series_by_instrument,
    }


def _safe_inline_json(payload: dict[str, Any]) -> str:
    """Serialize JSON safely for embedding inside an HTML `<script>` block."""
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    # Escape closing script tags and HTML comments
    return raw.replace("</", "<\\/").replace("<!--", "<\\!--")


def export_static_html(
    store: ValuationStore,
    instrument_symbol: str = DEFAULT_INSTRUMENT_SYMBOL,
    template_path: Path | None = None,
    output_path: Path = EXPORT_OUTPUT_PATH,
) -> Path:
    """Generate a self-contained single-file HTML snapshot dashboard."""
    tpl = template_path or (SINGLEFILE_DIST_DIR / "index.html")
    if not tpl.exists():
        raise FileNotFoundError(
            f"Single-file frontend build not found at {tpl}. "
            "Run `npm run build:static` in `frontend/` first."
        )

    bundle = build_static_snapshot_bundle(store, instrument_symbol=instrument_symbol)
    inject_script = f"<script>window.__VALUATION_SNAPSHOT__={_safe_inline_json(bundle)};</script>"

    html = tpl.read_text(encoding="utf-8")
    if SNAPSHOT_PLACEHOLDER in html:
        rendered = html.replace(SNAPSHOT_PLACEHOLDER, inject_script, 1)
    elif re.search(r"<head[^>]*>", html, flags=re.IGNORECASE):
        rendered = re.sub(
            r"(<head[^>]*>)",
            lambda m: f"{m.group(1)}\n{inject_script}",
            html,
            count=1,
            flags=re.IGNORECASE,
        )
    else:
        rendered = inject_script + "\n" + html

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(rendered, encoding="utf-8")
    return out
