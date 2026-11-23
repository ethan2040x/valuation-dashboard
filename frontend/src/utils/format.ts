import type { DisplayConfig, SourceTier, ThresholdBand } from "../types/valuation";

const SOURCE_LABELS: Record<string, string> = {
  computed: "实时计算",
  "qqq+yfinance": "QQQ + yfinance",
  "spy+yfinance": "SPY + yfinance",
  "equity+yfinance": "个股 yfinance 实时",
  siblis_free: "Siblis Research (免费)",
  multpl: "Multpl S&P 500 (免费)",
  shiller: "Shiller 历史库 (免费)",
  sec_edgar: "SEC EDGAR 财报 (免费)",
  yfinance_history: "Yahoo 财报历史 (免费)",
  csv_import: "CSV 导入历史",
  gurufocus_csv: "GuruFocus CSV",
  macromicro_csv: "MacroMicro CSV",
  bloomberg_csv: "Bloomberg CSV",
  wind_csv: "Wind / Choice CSV",
  siblis_paid: "Siblis Research (付费)",
  seed_csv: "内置 20 年基准种子",
};

const JOB_LABELS: Record<string, string> = {
  daily_refresh: "每日定时/手动刷新",
  qqq_holdings: "QQQ 成分股权重",
  spy_holdings: "SPY 成分股权重",
  yfinance: "yfinance 股价与 EPS",
  csv_import: "CSV 历史导入",
  compute_indicators: "指标计算引擎",
  backfill_history: "免费历史回填",
  multpl: "Multpl 历史抓取",
  shiller: "Shiller 历史抓取",
  siblis_free: "Siblis 免费历史抓取",
  siblis_paid: "Siblis 付费历史抓取",
  sec_edgar: "SEC EDGAR 财报抓取",
  yfinance_history: "Yahoo 财报历史抓取",
  refresh_equities: "自选公司批量刷新",
  backfill_equities: "自选公司批量回填",
};

const TIER_LABELS: Record<SourceTier, string> = {
  free: "免费",
  external_csv: "外部 CSV",
  paid: "付费",
};

export function formatSourceLabel(source: string | null | undefined): string {
  if (!source) return "未知来源";
  return SOURCE_LABELS[source] ?? source;
}

export function formatJobLabel(job: string): string {
  return JOB_LABELS[job] ?? job;
}

export function formatTierLabel(tier: SourceTier | string): string {
  return TIER_LABELS[tier as SourceTier] ?? tier;
}

export function formatValue(
  value: number | null | undefined,
  display?: Pick<DisplayConfig, "precision" | "suffix">
): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "—";
  }
  const precision = display?.precision ?? 2;
  const suffix = display?.suffix ?? "";
  return `${value.toFixed(precision)}${suffix}`;
}

export function formatDelta(
  delta: number | null | undefined,
  deltaPct: number | null | undefined,
  display?: Pick<DisplayConfig, "precision" | "suffix">
): string {
  if (delta === null || delta === undefined || Number.isNaN(delta)) {
    return "暂无日变化";
  }
  const precision = display?.precision ?? 2;
  const suffix = display?.suffix ?? "";
  const sign = delta > 0 ? "+" : "";
  const pctPart =
    deltaPct !== null && deltaPct !== undefined
      ? ` (${deltaPct > 0 ? "+" : ""}${deltaPct.toFixed(2)}%)`
      : "";
  return `${sign}${delta.toFixed(precision)}${suffix}${pctPart}`;
}

export function resolveThreshold(
  value: number | null | undefined,
  thresholds: ThresholdBand[]
): ThresholdBand | null {
  if (value === null || value === undefined || !thresholds.length) {
    return null;
  }
  for (const band of thresholds) {
    if (band.max === null || value <= band.max) {
      return band;
    }
  }
  return thresholds[thresholds.length - 1] ?? null;
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "尚未记录";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

export function formatPrice(
  value: number | null | undefined,
  currency = "USD"
): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "—";
  }
  const prefix = currency === "USD" || !currency ? "$" : `${currency} `;
  return `${prefix}${value.toFixed(2)}`;
}

export function formatMarketCap(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value) || value <= 0) {
    return "—";
  }
  if (value >= 1e12) return `$${(value / 1e12).toFixed(2)}T`;
  if (value >= 1e9) return `$${(value / 1e9).toFixed(0)}B`;
  if (value >= 1e6) return `$${(value / 1e6).toFixed(0)}M`;
  return `$${value.toFixed(0)}`;
}

export function resolvePercentileZone(
  pct: number | null | undefined
): ThresholdBand | null {
  if (pct === null || pct === undefined || Number.isNaN(pct)) {
    return null;
  }
  if (pct <= 30) {
    return { label: "低估区间", color: "#16a34a", max: 30 };
  }
  if (pct <= 70) {
    return { label: "适中区间", color: "#d97706", max: 70 };
  }
  return { label: "偏高区间", color: "#dc2626", max: null };
}
