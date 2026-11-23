"""Application configuration."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

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
        "yahoo_index": "^NDX",
        "siblis_ticker": "NDX",
        "description": "日频追踪纳指 100 (QQQ) 权重加权前瞻 PE、滚动 PE 及 5 年 / 10 年 / 20 年历史估值分位",
        "source_tag": "qqq+yfinance",
        "seed_file": "ndx_pe_history.csv",
    },
    "SPY": {
        "symbol": "SPY",
        "name": "标普 500",
        "en_name": "S&P 500",
        "etf_ticker": "SPY",
        "yahoo_index": "^GSPC",
        "siblis_ticker": "SPX",
        "description": "日频追踪标普 500 (SPY) 权重加权前瞻 PE、滚动 PE 及 5 年 / 10 年 / 20 年历史估值分位",
        "source_tag": "spy+yfinance",
        "seed_file": "spy_pe_history.csv",
    },
}

DATA_SOURCES: dict[str, dict[str, Any]] = {
    "computed": {
        "id": "computed",
        "name": "成分股实时计算",
        "tier": "free",
        "kind": "live",
        "default_enabled": True,
        "requires_env": None,
        "description": "基于 ETF 官方成分股权重与 yfinance 个股股价/EPS 实时计算（完全免费）",
    },
    "qqq+yfinance": {
        "id": "qqq+yfinance",
        "name": "QQQ + yfinance",
        "tier": "free",
        "kind": "live_constituents",
        "default_enabled": True,
        "requires_env": None,
        "description": "Invesco QQQ 持仓权重 + yfinance 成分股盈利与股价",
    },
    "spy+yfinance": {
        "id": "spy+yfinance",
        "name": "SPY + yfinance",
        "tier": "free",
        "kind": "live_constituents",
        "default_enabled": True,
        "requires_env": None,
        "description": "Slickcharts 标普 500 持仓权重 + yfinance 成分股盈利与股价",
    },
    "siblis_free": {
        "id": "siblis_free",
        "name": "Siblis Research (免费层)",
        "tier": "free",
        "kind": "history_api",
        "default_enabled": True,
        "requires_env": None,
        "description": "Siblis Research 免密钥公开历史 PE 接口（支持 NDX 与 SPX）",
    },
    "multpl": {
        "id": "multpl",
        "name": "Multpl S&P 500",
        "tier": "free",
        "kind": "history_web",
        "default_enabled": True,
        "requires_env": None,
        "description": "Multpl 公开标普 500 百年月频 PE 历史，结合 ^GSPC 收盘价展开为日频",
    },
    "shiller": {
        "id": "shiller",
        "name": "Shiller 历史数据库",
        "tier": "free",
        "kind": "history_web",
        "default_enabled": True,
        "requires_env": None,
        "description": "Robert Shiller 公开数据集（SP500 / Earnings），作为 Multpl 的免费备源",
    },
    "csv_import": {
        "id": "csv_import",
        "name": "自定义 CSV 导入",
        "tier": "external_csv",
        "kind": "csv",
        "default_enabled": True,
        "requires_env": None,
        "description": "通过 import-csv 导入的外部历史估值 CSV",
    },
    "gurufocus_csv": {
        "id": "gurufocus_csv",
        "name": "GuruFocus CSV",
        "tier": "external_csv",
        "kind": "csv",
        "default_enabled": True,
        "requires_env": None,
        "description": "从 GuruFocus 导出的历史估值 CSV",
    },
    "macromicro_csv": {
        "id": "macromicro_csv",
        "name": "MacroMicro CSV",
        "tier": "external_csv",
        "kind": "csv",
        "default_enabled": True,
        "requires_env": None,
        "description": "从财经 M 平方（MacroMicro）导出的历史估值 CSV",
    },
    "bloomberg_csv": {
        "id": "bloomberg_csv",
        "name": "Bloomberg CSV",
        "tier": "paid",
        "kind": "csv",
        "default_enabled": True,
        "requires_env": None,
        "description": "从 Bloomberg 终端导出的专业历史估值序列",
    },
    "wind_csv": {
        "id": "wind_csv",
        "name": "Wind / Choice CSV",
        "tier": "paid",
        "kind": "csv",
        "default_enabled": True,
        "requires_env": None,
        "description": "从 Wind 或东方财富 Choice 导出的历史估值序列",
    },
    "siblis_paid": {
        "id": "siblis_paid",
        "name": "Siblis Research (付费版)",
        "tier": "paid",
        "kind": "history_api",
        "default_enabled": False,
        "requires_env": "SIBLIS_API_KEY",
        "description": "Siblis Research 完整历史付费 API（需配置 SIBLIS_API_KEY 并显式开启）",
    },
    "seed_csv": {
        "id": "seed_csv",
        "name": "内置 20 年基准种子",
        "tier": "free",
        "kind": "seed",
        "default_enabled": True,
        "requires_env": None,
        "description": "随仓库分发的 2006 至今内置参考基准序列（零网络兜底）",
    },
}

SOURCE_PRIORITY_PROFILES: dict[str, dict[str, Any]] = {
    "free_first": {
        "id": "free_first",
        "label": "免费源优先（默认）",
        "description": "优先采用免费实时计算与免费公开历史源（Siblis 免费层 / Multpl / Shiller），外部导入 CSV 补充缺口，内置 20 年种子兜底。",
        "priority": [
            "computed",
            "qqq+yfinance",
            "spy+yfinance",
            "siblis_free",
            "multpl",
            "shiller",
            "csv_import",
            "gurufocus_csv",
            "macromicro_csv",
            "bloomberg_csv",
            "wind_csv",
            "siblis_paid",
            "seed_csv",
        ],
    },
    "external_first": {
        "id": "external_first",
        "label": "外部/付费源优先",
        "description": "保留实时成分股计算为最高优先级，历史区间优先采用 Bloomberg / Wind / Siblis 付费版 / GuruFocus 等外部数据，免费公开源与内置种子顺延。",
        "priority": [
            "computed",
            "qqq+yfinance",
            "spy+yfinance",
            "bloomberg_csv",
            "wind_csv",
            "siblis_paid",
            "gurufocus_csv",
            "macromicro_csv",
            "csv_import",
            "siblis_free",
            "multpl",
            "shiller",
            "seed_csv",
        ],
    },
}

_raw_profile = os.environ.get("VALUATION_SOURCE_PROFILE", "free_first").strip().lower()
DEFAULT_SOURCE_PROFILE: str = (
    _raw_profile if _raw_profile in SOURCE_PRIORITY_PROFILES else "free_first"
)
SIBLIS_API_KEY: str | None = os.environ.get("SIBLIS_API_KEY", "").strip() or None


def resolve_default_source_priority() -> tuple[str, list[str]]:
    """Return `(profile_id, priority_list)` from environment or default `free_first`."""
    env_priority = os.environ.get("VALUATION_SOURCE_PRIORITY", "").strip()
    if env_priority:
        parsed = list(
            dict.fromkeys(item.strip() for item in env_priority.split(",") if item.strip())
        )
        if parsed:
            return "custom", parsed
    profile = SOURCE_PRIORITY_PROFILES[DEFAULT_SOURCE_PROFILE]
    return DEFAULT_SOURCE_PROFILE, list(profile["priority"])


# Higher priority comes earlier in the list.
# When the same series has multiple sources on the same date, the first matching source wins.
SOURCE_PRIORITY: list[str] = resolve_default_source_priority()[1]

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
