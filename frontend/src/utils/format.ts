import type { DisplayConfig, SourceTier, ThresholdBand } from "../types/valuation";

const SOURCE_LABELS: Record<string, string> = {
  computed: "成分股实时计算",
  "qqq+yfinance": "QQQ + yfinance",
  "spy+yfinance": "SPY + yfinance",
  siblis_free: "Siblis Research (免费)",
  multpl: "Multpl S&P 500 (免费)",
  shiller: "Shiller 历史库 (免费)",
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
  backfill_history: "20 年免费历史回填",
  multpl: "Multpl 历史抓取",
  shiller: "Shiller 历史抓取",
  siblis_free: "Siblis 免费历史抓取",
  siblis_paid: "Siblis 付费历史抓取",
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
