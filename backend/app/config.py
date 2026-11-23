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

INSTRUMENTS: dict[str, dict[str, str]] = {
    "NDX": {
        "symbol": "NDX",
        "name": "纳指 100",
        "en_name": "Nasdaq-100",
        "etf_ticker": "QQQ",
        "description": "日频追踪纳指 100 (QQQ) 权重加权前瞻 PE、滚动 PE 及 5 年 / 10 年历史估值分位",
        "source_tag": "qqq+yfinance",
        "seed_file": "ndx_pe_history.csv",
    },
    "SPY": {
        "symbol": "SPY",
        "name": "标普 500",
        "en_name": "S&P 500",
        "etf_ticker": "SPY",
        "description": "日频追踪标普 500 (SPY) 权重加权前瞻 PE、滚动 PE 及 5 年 / 10 年历史估值分位",
        "source_tag": "spy+yfinance",
        "seed_file": "spy_pe_history.csv",
    },
}

# Higher priority comes earlier in the list.
# When the same series has multiple sources on the same date, the first matching source wins.
SOURCE_PRIORITY: list[str] = [
    "computed",
    "qqq+yfinance",
    "spy+yfinance",
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
