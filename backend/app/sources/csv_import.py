"""CSV historical data importer into raw_series."""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from app.sources.base import parse_float
from app.store import ValuationStore


def _normalize_date(raw: str) -> str:
    text = raw.strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    raise ValueError(f"Unsupported date format: {raw!r}")


def import_csv_file(
    store: ValuationStore,
    csv_path: str | Path,
    instrument_symbol: str = "NDX",
    default_source: str = "csv_import",
) -> dict[str, int]:
    """Import a historical CSV file into `raw_series`.

    Supported CSV formats:
    1. Wide format: `date,pe_ttm,pe_fwd[,source]` (any non-date/source column is treated as a `series_id`)
    2. Long format: `date,series_id,value[,source]`

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
        if "date" not in lower_fields:
            raise ValueError(f"CSV file must have a 'date' column: {path}")

        date_col = lower_fields["date"]
        source_col = lower_fields.get("source")
        is_long_format = "series_id" in lower_fields and "value" in lower_fields

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
                sid = (row.get(lower_fields["series_id"]) or "").strip()
                val = parse_float(row.get(lower_fields["value"]))
                if sid and val is not None:
                    buckets.setdefault((sid, row_source), []).append((date_iso, val))
            else:
                for col in fields:
                    col_lower = col.lower()
                    if col_lower in {"date", "source"}:
                        continue
                    val = parse_float(row.get(col))
                    if val is not None:
                        buckets.setdefault((col_lower, row_source), []).append((date_iso, val))

    counts: dict[str, int] = {}
    for (series_id, source), rows in buckets.items():
        saved = store.save_raw_series(
            instrument_id=instrument_id,
            series_id=series_id,
            rows=rows,
            source=source,
        )
        counts[series_id] = counts.get(series_id, 0) + saved

    return counts
