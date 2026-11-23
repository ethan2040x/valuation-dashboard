"""Application configuration."""

from __future__ import annotations

import os
from pathlib import Path

def _detect_project_root() -> Path:
    candidate = Path(__file__).resolve().parents[2]
    if (candidate / "data").exists() or (candidate / "frontend").exists():
        return candidate
    return Path.cwd()


PROJECT_ROOT = _detect_project_root()
DATA_DIR = Path(os.environ.get("VALUATION_DATA_DIR", str(PROJECT_ROOT / "data")))
SEED_DIR = DATA_DIR / "seed"
DB_PATH = Path(os.environ.get("VALUATION_DB_PATH", str(DATA_DIR / "valuation.db")))
STATIC_DIST_DIR = Path(
    os.environ.get("VALUATION_FRONTEND_DIST", str(PROJECT_ROOT / "frontend" / "dist"))
)
SINGLEFILE_DIST_DIR = Path(
    os.environ.get(
        "VALUATION_SINGLEFILE_DIST", str(PROJECT_ROOT / "frontend" / "dist-singlefile")
    )
)
EXPORT_OUTPUT_PATH = Path(
    os.environ.get(
        "VALUATION_EXPORT_PATH",
        str(PROJECT_ROOT / "dist" / "ndx-valuation-snapshot.html"),
    )
)

DEFAULT_INSTRUMENT_SYMBOL = "NDX"
DEFAULT_INSTRUMENT_NAME = "纳指 100"

# Higher priority comes earlier in the list.
# When the same series has multiple sources on the same date, the first matching source wins.
SOURCE_PRIORITY: list[str] = [
    "computed",
    "qqq+yfinance",
    "csv_import",
    "seed_csv",
]

# Missing constituent weight threshold above which a daily calculated PE is flagged "不完整".
INCOMPLETE_WEIGHT_THRESHOLD: float = 0.05

# Daily scheduler defaults (US Eastern market close + buffer: 18:00 America/New_York, Mon-Fri)
SCHEDULE_ENABLED: bool = os.environ.get("VALUATION_SCHEDULE_ENABLED", "true").lower() in {
    "1",
    "true",
    "yes",
}
SCHEDULE_CRON_HOUR: int = int(os.environ.get("VALUATION_SCHEDULE_HOUR", "18"))
SCHEDULE_CRON_MINUTE: int = int(os.environ.get("VALUATION_SCHEDULE_MINUTE", "30"))
SCHEDULE_TIMEZONE: str = os.environ.get("VALUATION_SCHEDULE_TZ", "America/New_York")

# Live fetch settings
YFINANCE_MAX_WORKERS: int = int(os.environ.get("VALUATION_YF_WORKERS", "8"))
HTTP_TIMEOUT_SECONDS: float = float(os.environ.get("VALUATION_HTTP_TIMEOUT", "20"))
