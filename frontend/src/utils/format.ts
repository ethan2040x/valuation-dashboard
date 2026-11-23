import type { DisplayConfig, ThresholdBand } from "../types/valuation";

const SOURCE_LABELS: Record<string, string> = {
  computed: "成分股实时计算",
  "qqq+yfinance": "QQQ + yfinance",
  "spy+yfinance": "SPY + yfinance",
  csv_import: "CSV 导入历史",
  seed_csv: "历史基准 CSV",
};

const JOB_LABELS: Record<string, string> = {
  daily_refresh: "每日定时/手动刷新",
  qqq_holdings: "QQQ 成分股权重",
  spy_holdings: "SPY 成分股权重",
  yfinance: "yfinance 股价与 EPS",
  csv_import: "CSV 历史导入",
  compute_indicators: "指标计算引擎",
};

export function formatSourceLabel(source: string | null | undefined): string {
  if (!source) return "未知来源";
  return SOURCE_LABELS[source] ?? source;
}

export function formatJobLabel(job: string): string {
  return JOB_LABELS[job] ?? job;
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
