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

SEC_USER_AGENT: str = os.environ.get(
    "VALUATION_SEC_USER_AGENT",
    "ValuationDashboard/0.2 (valuation-dashboard@localhost)",
)
MAX_EQUITY_PE: float = float(os.environ.get("VALUATION_MAX_EQUITY_PE", "300.0"))

INSTRUMENTS: dict[str, dict[str, str]] = {
    "NDX": {
        "symbol": "NDX",
        "name": "纳指 100",
        "en_name": "Nasdaq-100",
        "kind": "index",
        "etf_ticker": "QQQ",
        "yahoo_index": "^NDX",
        "siblis_ticker": "NDX",
        "currency": "USD",
        "description": "日频追踪纳指 100 (QQQ) 权重加权前瞻 PE、滚动 PE 及 5 年 / 10 年 / 20 年历史估值分位",
        "source_tag": "qqq+yfinance",
        "seed_file": "ndx_pe_history.csv",
    },
    "SPY": {
        "symbol": "SPY",
        "name": "标普 500",
        "en_name": "S&P 500",
        "kind": "index",
        "etf_ticker": "SPY",
        "yahoo_index": "^GSPC",
        "currency": "USD",
        "description": "日频追踪标普 500 (SPY) 权重加权前瞻 PE、滚动 PE 及 5 年 / 10 年 / 20 年历史估值分位",
        "source_tag": "spy+yfinance",
        "seed_file": "spy_pe_history.csv",
    },
}

DEFAULT_EQUITIES: list[dict[str, str]] = [
    {
        "symbol": "NVDA",
        "name": "英伟达",
        "en_name": "NVIDIA",
        "sector": "半导体与 AI",
        "group_name": "Top 10 非金融市值",
        "currency": "USD",
        "kind": "equity",
        "description": "全球 AI 计算与 GPU 龙头，日频追踪滚动 PE、前瞻 PE 及历史分位",
    },
    {
        "symbol": "AAPL",
        "name": "苹果",
        "en_name": "Apple",
        "sector": "消费电子",
        "group_name": "Top 10 非金融市值",
        "currency": "USD",
        "kind": "equity",
        "description": "全球消费电子与生态服务巨头，日频追踪滚动 PE、前瞻 PE 及历史分位",
    },
    {
        "symbol": "MSFT",
        "name": "微软",
        "en_name": "Microsoft",
        "sector": "云计算与软件",
        "group_name": "Top 10 非金融市值",
        "currency": "USD",
        "kind": "equity",
        "description": "企业级软件、Azure 云与 AI 平台龙头，日频追踪滚动 PE、前瞻 PE 及历史分位",
    },
    {
        "symbol": "AMZN",
        "name": "亚马逊",
        "en_name": "Amazon",
        "sector": "电商与云计算",
        "group_name": "Top 10 非金融市值",
        "currency": "USD",
        "kind": "equity",
        "description": "北美电商与 AWS 云计算领导者，日频追踪滚动 PE、前瞻 PE 及历史分位",
    },
    {
        "symbol": "GOOGL",
        "name": "谷歌",
        "en_name": "Alphabet",
        "sector": "互联网与 AI",
        "group_name": "Top 10 非金融市值",
        "currency": "USD",
        "kind": "equity",
        "description": "数字搜索、YouTube 与云及 AI 平台龙头，日频追踪滚动 PE、前瞻 PE 及历史分位",
    },
    {
        "symbol": "META",
        "name": "Meta",
        "en_name": "Meta Platforms",
        "sector": "社交媒体与 AI",
        "group_name": "Top 10 非金融市值",
        "currency": "USD",
        "kind": "equity",
        "description": "全球社交网络与数字广告巨头，日频追踪滚动 PE、前瞻 PE 及历史分位",
    },
    {
        "symbol": "AVGO",
        "name": "博通",
        "en_name": "Broadcom",
        "sector": "半导体与网络",
        "group_name": "Top 10 非金融市值",
        "currency": "USD",
        "kind": "equity",
        "description": "定制 AI 芯片、网络半导体与企业软件巨头，日频追踪滚动 PE、前瞻 PE 及历史分位",
    },
    {
        "symbol": "TSLA",
        "name": "特斯拉",
        "en_name": "Tesla",
        "sector": "电动车与能源",
        "group_name": "Top 10 非金融市值",
        "currency": "USD",
        "kind": "equity",
        "description": "电动汽车、储能与自动驾驶领军企业，日频追踪滚动 PE、前瞻 PE 及历史分位",
    },
    {
        "symbol": "LLY",
        "name": "礼来",
        "en_name": "Eli Lilly",
        "sector": "创新药",
        "group_name": "Top 10 非金融市值",
        "currency": "USD",
        "kind": "equity",
        "description": "全球代谢疾病与肿瘤创新药龙头，日频追踪滚动 PE、前瞻 PE 及历史分位",
    },
    {
        "symbol": "WMT",
        "name": "沃尔玛",
        "en_name": "Walmart",
        "sector": "零售与消费",
        "group_name": "Top 10 非金融市值",
        "currency": "USD",
        "kind": "equity",
        "description": "全球商超零售与全渠道消费龙头，日频追踪滚动 PE、前瞻 PE 及历史分位",
    },
]

DATA_SOURCES: dict[str, dict[str, Any]] = {
    "computed": {
        "id": "computed",
        "name": "实时计算",
        "tier": "free",
        "kind": "live",
        "applies_to": "all",
        "default_enabled": True,
        "requires_env": None,
        "description": "基于 ETF 成分股权重或个股股价与 EPS 实时计算（完全免费）",
    },
    "qqq+yfinance": {
        "id": "qqq+yfinance",
        "name": "QQQ + yfinance",
        "tier": "free",
        "kind": "live_constituents",
        "applies_to": "index",
        "default_enabled": True,
        "requires_env": None,
        "description": "Invesco QQQ 持仓权重 + yfinance 成分股盈利与股价",
    },
    "spy+yfinance": {
        "id": "spy+yfinance",
        "name": "SPY + yfinance",
        "tier": "free",
        "kind": "live_constituents",
        "applies_to": "index",
        "default_enabled": True,
        "requires_env": None,
        "description": "Slickcharts 标普 500 持仓权重 + yfinance 成分股盈利与股价",
    },
    "equity+yfinance": {
        "id": "equity+yfinance",
        "name": "个股 yfinance 实时",
        "tier": "free",
        "kind": "live_equity",
        "applies_to": "equity",
        "default_enabled": True,
        "requires_env": None,
        "description": "通过 yfinance（优先复用当日指数成分股快照）获取个股股价与 EPS",
    },
    "siblis_free": {
        "id": "siblis_free",
        "name": "Siblis Research (免费层)",
        "tier": "free",
        "kind": "history_api",
        "applies_to": "index",
        "default_enabled": True,
        "requires_env": None,
        "description": "Siblis Research 免密钥公开历史 PE 接口（支持 NDX）",
    },
    "multpl": {
        "id": "multpl",
        "name": "Multpl S&P 500",
        "tier": "free",
        "kind": "history_web",
        "applies_to": "index",
        "default_enabled": True,
        "requires_env": None,
        "description": "Multpl 公开标普 500 百年月频 PE 历史，结合 ^GSPC 收盘价展开为日频",
    },
    "shiller": {
        "id": "shiller",
        "name": "Shiller 历史数据库",
        "tier": "free",
        "kind": "history_web",
        "applies_to": "index",
        "default_enabled": True,
        "requires_env": None,
        "description": "Robert Shiller 公开数据集（SP500 / Earnings），作为 Multpl 的免费备源",
    },
    "sec_edgar": {
        "id": "sec_edgar",
        "name": "SEC EDGAR 官方财报 (免费)",
        "tier": "free",
        "kind": "history_api",
        "applies_to": "equity",
        "default_enabled": True,
        "requires_env": None,
        "description": "SEC EDGAR CompanyFacts 官方 10-Q/10-K 稀释 EPS（含拆股对齐）结合 Yahoo 日收盘价，支持 10–15 年滚动 PE",
    },
    "yfinance_history": {
        "id": "yfinance_history",
        "name": "Yahoo 财报历史 (免费)",
        "tier": "free",
        "kind": "history_api",
        "applies_to": "equity",
        "default_enabled": True,
        "requires_env": None,
        "description": "yfinance 季度/年度利润表 EPS 结合历史收盘价合成滚动 PE（约 4–5 年兜底）",
    },
    "csv_import": {
        "id": "csv_import",
        "name": "自定义 CSV 导入",
        "tier": "external_csv",
        "kind": "csv",
        "applies_to": "all",
        "default_enabled": True,
        "requires_env": None,
        "description": "通过页面上传或 import-csv 导入的外部历史估值 CSV",
    },
    "gurufocus_csv": {
        "id": "gurufocus_csv",
        "name": "GuruFocus CSV",
        "tier": "external_csv",
        "kind": "csv",
        "applies_to": "all",
        "default_enabled": True,
        "requires_env": None,
        "description": "从 GuruFocus 导出的历史估值 CSV",
    },
    "macromicro_csv": {
        "id": "macromicro_csv",
        "name": "MacroMicro CSV",
        "tier": "external_csv",
        "kind": "csv",
        "applies_to": "all",
        "default_enabled": True,
        "requires_env": None,
        "description": "从财经 M 平方（MacroMicro）导出的历史估值 CSV",
    },
    "bloomberg_csv": {
        "id": "bloomberg_csv",
        "name": "Bloomberg CSV",
        "tier": "paid",
        "kind": "csv",
        "applies_to": "all",
        "default_enabled": True,
        "requires_env": None,
        "description": "从 Bloomberg 终端导出的专业历史估值序列",
    },
    "wind_csv": {
        "id": "wind_csv",
        "name": "Wind / Choice CSV",
        "tier": "paid",
        "kind": "csv",
        "applies_to": "all",
        "default_enabled": True,
        "requires_env": None,
        "description": "从 Wind 或东方财富 Choice 导出的历史估值序列",
    },
    "siblis_paid": {
        "id": "siblis_paid",
        "name": "Siblis Research (付费版)",
        "tier": "paid",
        "kind": "history_api",
        "applies_to": "index",
        "default_enabled": False,
        "requires_env": "SIBLIS_API_KEY",
        "description": "Siblis Research 完整历史付费 API（需配置 SIBLIS_API_KEY 并显式开启）",
    },
    "seed_csv": {
        "id": "seed_csv",
        "name": "内置 20 年基准种子",
        "tier": "free",
        "kind": "seed",
        "applies_to": "index",
        "default_enabled": True,
        "requires_env": None,
        "description": "随仓库分发的 2006 至今内置参考基准序列（零网络兜底）",
    },
}

SOURCE_PRIORITY_PROFILES: dict[str, dict[str, Any]] = {
    "free_first": {
        "id": "free_first",
        "label": "免费源优先（默认）",
        "description": "优先采用免费实时计算与免费公开历史源（Siblis / Multpl / Shiller / SEC EDGAR / Yahoo 财报），外部导入 CSV 补充缺口，内置 20 年种子兜底。",
        "priority": [
            "computed",
            "qqq+yfinance",
            "spy+yfinance",
            "equity+yfinance",
            "siblis_free",
            "multpl",
            "shiller",
            "sec_edgar",
            "yfinance_history",
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
        "description": "保留实时行情计算为最高优先级，历史区间优先采用 Bloomberg / Wind / Siblis 付费版 / GuruFocus 等外部数据，免费公开源与内置种子顺延。",
        "priority": [
            "computed",
            "qqq+yfinance",
            "spy+yfinance",
            "equity+yfinance",
            "bloomberg_csv",
            "wind_csv",
            "siblis_paid",
            "gurufocus_csv",
            "macromicro_csv",
            "csv_import",
            "siblis_free",
            "multpl",
            "shiller",
            "sec_edgar",
            "yfinance_history",
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
