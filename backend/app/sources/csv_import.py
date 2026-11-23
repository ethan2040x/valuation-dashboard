"""CSV historical data importer into raw_series."""

from __future__ import annotations

import csv
import re
from datetime import datetime
from pathlib import Path

from app.sources.base import parse_float
from app.store import ValuationStore

_DATE_COLUMN_ALIASES = {
    "date",
    "trading_day (eod)",
    "trading_day",
    "trade_date",
    "日期",
    "time",
    "timestamp",
}

_IGNORED_METADATA_COLUMNS = {
    "ticker",
    "symbol",
    "index_name",
    "name",
    "index",
}

_METRIC_COLUMN_ALIASES: dict[str, str] = {
    "pe_ttm": "pe_ttm",
    "pe_trailing": "pe_ttm",
    "trailing_pe": "pe_ttm",
    "trailing pe": "pe_ttm",
    "pe ratio": "pe_ttm",
    "p/e": "pe_ttm",
    "ttm pe": "pe_ttm",
    "ttm_pe": "pe_ttm",
    "滚动pe": "pe_ttm",
    "滚动市盈率": "pe_ttm",
    "pe_fwd": "pe_fwd",
    "pe_forward": "pe_fwd",
    "forward_pe": "pe_fwd",
    "forward pe": "pe_fwd",
    "fwd pe": "pe_fwd",
    "fwd_pe": "pe_fwd",
    "forward p/e": "pe_fwd",
    "前瞻pe": "pe_fwd",
    "前瞻市盈率": "pe_fwd",
}


def _normalize_date(raw: str) -> str:
    text = re.sub(r"[†*]+", "", raw).strip()
    for fmt in (
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%m/%d/%Y",
        "%Y-%m-%d %H:%M:%S",
        "%b %d, %Y",
        "%B %d, %Y",
        "%Y-%m",
        "%b %Y",
        "%B %Y",
    ):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    raise ValueError(f"Unsupported date format: {raw!r}")


def _parse_csv_float(raw: str | None) -> float | None:
    if raw is None:
        return None
    cleaned = re.sub(r"Est\.", "", raw, flags=re.IGNORECASE)
    cleaned = re.sub(r"[†*]+", "", cleaned)
    cleaned = re.sub(r"(?<=\d)\s*[xX]$", "", cleaned.strip())
    return parse_float(cleaned)


def import_csv_file(
    store: ValuationStore,
    csv_path: str | Path,
    instrument_symbol: str = "NDX",
    default_source: str = "csv_import",
    series_id: str | None = None,
) -> dict[str, int]:
    """Import a historical CSV file into `raw_series`.

    Supported CSV formats:
    1. Wide format: `date,pe_ttm,pe_fwd[,source]` (supports aliases such as Siblis
       `trading_day (EOD),pe_trailing` / `pe_forward`, Chinese headers, etc.)
    2. Long format: `date,series_id,value[,source]`
    3. Two-column generic format: `Date,Value` when `series_id` is supplied (e.g.
       Multpl or MacroMicro CSV export with `--series-id pe_ttm` / `pe_fwd`).

    Returns mapping of `{series_id: imported_row_count}`.
    """
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"CSV file not found: {path}")

    instrument = store.get_or_create_instrument(instrument_symbol)
    instrument_id = int(instrument["id"])

    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError(f"CSV file has no header: {path}")

        fields = [name.strip() for name in reader.fieldnames if name]
        lower_fields = {name.lower(): name for name in fields}
        date_col = next(
            (lower_fields[alias] for alias in _DATE_COLUMN_ALIASES if alias in lower_fields),
            None,
        )
        if not date_col:
            raise ValueError(f"CSV file must have a 'date' column: {path}")

        source_col = lower_fields.get("source")
        is_long_format = "series_id" in lower_fields and "value" in lower_fields

        metric_cols = [
            col
            for col in fields
            if col != date_col
            and col.lower() != "source"
            and col.lower() not in _IGNORED_METADATA_COLUMNS
        ]
        single_metric_col = len(metric_cols) == 1

        # Group rows by (series_id, source) -> list[(date, value)]
        buckets: dict[tuple[str, str], list[tuple[str, float]]] = {}

        for row in reader:
            raw_date = (row.get(date_col) or "").strip()
            if not raw_date:
                continue
            date_iso = _normalize_date(raw_date)
            row_source = (
                (row.get(source_col) or "").strip() if source_col else ""
            ) or default_source

            if is_long_format:
                sid = (row.get(lower_fields["series_id"]) or "").strip() or (series_id or "")
                val = _parse_csv_float(row.get(lower_fields["value"]))
                if sid and val is not None:
                    buckets.setdefault((sid, row_source), []).append((date_iso, val))
            else:
                for col in metric_cols:
                    col_lower = col.lower()
                    val = _parse_csv_float(row.get(col))
                    if val is None:
                        continue
                    if series_id and (single_metric_col or col_lower == "value"):
                        target_sid = series_id.strip()
                    else:
                        target_sid = _METRIC_COLUMN_ALIASES.get(col_lower, col_lower)
                    buckets.setdefault((target_sid, row_source), []).append((date_iso, val))

    counts: dict[str, int] = {}
    for (sid, source), rows in buckets.items():
        saved = store.save_raw_series(
            instrument_id=instrument_id,
            series_id=sid,
            rows=rows,
            source=source,
        )
        counts[sid] = counts.get(sid, 0) + saved

    return counts
