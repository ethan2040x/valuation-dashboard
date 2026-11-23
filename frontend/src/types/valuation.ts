export type RangeKey = "1y" | "5y" | "10y" | "all";

export interface ThresholdBand {
  label: string;
  color: string;
  max: number | null;
}

export interface ReferenceLine {
  kind: "mean" | "std_band" | "fixed";
  label: string;
  value?: number | null;
  sigma?: number | null;
}

export interface DisplayConfig {
  kind: "metric_card" | "percentile_bar" | "line_chart";
  chartable: boolean;
  card_visible: boolean;
  precision: number;
  suffix: string;
  reference_lines: ReferenceLine[];
  thresholds: ThresholdBand[];
  order: number;
  default_params: Record<string, string | number>;
}

export interface FormulaVariable {
  symbol: string;
  meaning: string;
}

export interface FormulaInfo {
  expression: string;
  variables: FormulaVariable[];
  why: string;
  rules: string[];
}

export interface IndicatorMeta {
  id: string;
  name: string;
  unit: string;
  description: string;
  kind: "base" | "derived";
  depends_on: string[];
  params: Record<string, Array<string | number>>;
  display: DisplayConfig;
  formula?: FormulaInfo | null;
}

export interface SeriesPoint {
  date: string;
  value: number | null;
  source: string;
  is_incomplete: boolean;
  meta: Record<string, unknown>;
}

export interface SeriesStats {
  count: number;
  mean: number | null;
  std: number | null;
  plus_1std: number | null;
  minus_1std: number | null;
  min: number | null;
  median: number | null;
  max: number | null;
}

export interface SeriesPayload {
  indicator_id: string;
  name: string;
  unit: string;
  instrument: string;
  range: RangeKey;
  params: Record<string, string | number>;
  sources: string[];
  stats: SeriesStats;
  series: SeriesPoint[];
}

export interface SnapshotVariant {
  params: Record<string, string | number>;
  date: string | null;
  value: number | null;
  prev_date: string | null;
  prev_value: number | null;
  delta: number | null;
  delta_pct: number | null;
  source: string | null;
  is_incomplete: boolean;
  meta: Record<string, unknown>;
}

export interface SnapshotIndicatorEntry {
  id: string;
  name: string;
  unit: string;
  latest: SnapshotVariant;
  variants: SnapshotVariant[];
}

export interface JobRunStatus {
  id: number;
  job: string;
  started_at: string;
  finished_at: string | null;
  status: "running" | "success" | "partial" | "failed";
  error: string | null;
  details: Record<string, unknown>;
  last_success_at: string | null;
}

export interface SnapshotPayload {
  instrument: {
    id: number;
    symbol: string;
    name: string;
  };
  generated_at: string;
  indicators: Record<string, SnapshotIndicatorEntry>;
  status: {
    last_updated_at: string | null;
    latest_data_date: string | null;
    missing_days_1y: number;
    total_trading_days: number;
    jobs: JobRunStatus[];
  };
}

export interface RefreshResult {
  status: string;
  error: string | null;
  as_of_date: string | null;
  constituents_saved: number;
  indicators_computed: Record<string, number>;
}

export interface StaticSnapshotBundle {
  mode: "static";
  instrument: string;
  generated_at: string;
  indicators: IndicatorMeta[];
  snapshot: SnapshotPayload;
  series: Record<string, Record<RangeKey, SeriesPayload>>;
}

export interface ValuationDataProvider {
  readonly mode: "dynamic" | "static";
  readonly snapshotGeneratedAt: string | null;
  getIndicators(): Promise<IndicatorMeta[]>;
  getSnapshot(instrument: string): Promise<SnapshotPayload>;
  getSeries(
    indicatorId: string,
    instrument: string,
    range: RangeKey
  ): Promise<SeriesPayload>;
  triggerRefresh?(instrument: string): Promise<RefreshResult>;
}

declare global {
  interface Window {
    __VALUATION_SNAPSHOT__?: StaticSnapshotBundle;
  }
}
