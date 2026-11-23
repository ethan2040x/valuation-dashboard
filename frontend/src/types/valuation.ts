export type RangeKey = "1y" | "5y" | "10y" | "20y" | "all";

export type SourceTier = "free" | "external_csv" | "paid";

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
  equity_formula?: FormulaInfo | null;
}

export interface SeriesPoint {
  date: string;
  value: number | null;
  source: string;
  is_incomplete: boolean;
  meta?: Record<string, unknown>;
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

export interface CompactSeriesData {
  s: string[];
  d: Array<
    [string, number | null, number] | [string, number | null, number, number]
  >;
}

export interface RawSeriesPayload extends Omit<SeriesPayload, "series"> {
  series?: SeriesPoint[];
  compact?: CompactSeriesData;
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

export interface EquityQuoteSummary {
  date: string;
  price: number | null;
  eps_ttm: number | null;
  eps_fwd: number | null;
  market_cap: number | null;
  source: string;
  fetched_at: string;
}

export interface SnapshotPayload {
  instrument: {
    id: number;
    symbol: string;
    name: string;
    kind?: "index" | "equity";
    en_name?: string;
    sector?: string;
    group_name?: string;
    currency?: string;
    etf_ticker?: string;
    description?: string;
  };
  generated_at: string;
  latest_quote?: EquityQuoteSummary | null;
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
  equities_saved?: number;
  indicators_computed: Record<string, number>;
}

export interface InstrumentMeta {
  id?: number | null;
  symbol: string;
  name: string;
  en_name: string;
  kind: "index" | "equity";
  sector?: string;
  group_name?: string;
  currency?: string;
  etf_ticker: string;
  description: string;
  enabled?: boolean;
}

export interface WatchlistItem {
  instrument: InstrumentMeta;
  price: number | null;
  price_date: string | null;
  market_cap: number | null;
  eps_ttm: number | null;
  eps_fwd: number | null;
  pe_ttm: SnapshotVariant | null;
  pe_fwd: SnapshotVariant | null;
  percentiles: {
    pe_ttm_5y: SnapshotVariant | null;
    pe_ttm_10y: SnapshotVariant | null;
    pe_fwd_5y: SnapshotVariant | null;
    pe_fwd_10y: SnapshotVariant | null;
  };
  history_start: string | null;
  history_end: string | null;
  history_days: number;
  history_years: number | null;
  sources: string[];
}

export interface WatchlistOverviewPayload {
  kind: "index" | "equity";
  generated_at: string;
  count: number;
  items: WatchlistItem[];
}

export interface AddCompaniesRequest {
  symbol?: string;
  symbols?: string[];
  name?: string;
  en_name?: string;
  sector?: string;
  group_name?: string;
  currency?: string;
  backfill?: boolean;
  refresh?: boolean;
}

export interface AddCompaniesResponse {
  instruments: InstrumentMeta[];
  job_id: number | null;
  status: string;
  backfill?: Record<string, unknown> | null;
  refresh?: Record<string, unknown> | null;
}

export interface CsvImportRequest {
  csv_text: string;
  instrument?: string | null;
  source?: string;
  series_id?: string | null;
}

export interface CsvImportResponse {
  status: string;
  imported_by_symbol: Record<string, Record<string, number>>;
  computed_by_symbol: Record<string, Record<string, number>>;
  total_rows: number;
}

export interface BatchJobPayload {
  job_id?: number;
  id?: number;
  job?: string;
  status: "running" | "success" | "partial" | "failed";
  error?: string | null;
  total?: number;
  details?: Record<string, unknown>;
}

export interface SourceCoverageEntry {
  min_date: string;
  max_date: string;
  count: number;
}

export interface SourceCatalogItem {
  id: string;
  name: string;
  tier: SourceTier;
  kind: string;
  default_enabled: boolean;
  requires_env: string | null;
  configured: boolean;
  active_in_db: boolean;
  priority_rank: number;
  description: string;
  coverage: Record<string, SourceCoverageEntry>;
}

export interface SourcePriorityProfile {
  id: string;
  label: string;
  description: string;
  priority: string[];
}

export interface SourcesPayload {
  instrument: string;
  profile: string;
  priority: string[];
  profiles: SourcePriorityProfile[];
  sources: SourceCatalogItem[];
}

export interface BackfillResult {
  status: string;
  instrument: string;
  start_date: string;
  include_paid: boolean;
  total_saved: number;
  sources: Record<
    string,
    {
      status: string;
      saved: Record<string, number>;
      error?: string;
    }
  >;
  indicators_computed: Record<string, number>;
}

export interface StaticSnapshotBundle {
  mode: "static";
  instrument: string;
  instruments?: InstrumentMeta[];
  generated_at: string;
  indicators: IndicatorMeta[];
  snapshot: SnapshotPayload;
  series: Record<string, Partial<Record<RangeKey, RawSeriesPayload>>>;
  sources?: SourcesPayload;
  snapshots?: Record<string, SnapshotPayload>;
  series_by_instrument?: Record<
    string,
    Record<string, Partial<Record<RangeKey, RawSeriesPayload>>>
  >;
  sources_by_instrument?: Record<string, SourcesPayload>;
  watchlist_overview?: WatchlistOverviewPayload;
}

export interface StaticSiteConfig {
  dataBase: string;
}

export interface StaticSiteBootstrapPayload {
  mode: "static_site";
  instrument: string;
  instruments: InstrumentMeta[];
  generated_at: string;
  indicators: IndicatorMeta[];
  index_snapshots: Record<string, SnapshotPayload>;
  index_sources: Record<string, SourcesPayload>;
}

export interface StaticSiteInstrumentBundle {
  symbol: string;
  kind: "index" | "equity";
  snapshot?: SnapshotPayload;
  sources?: SourcesPayload;
  series: Record<string, Partial<Record<RangeKey, RawSeriesPayload>>>;
}

export interface ValuationDataProvider {
  readonly mode: "dynamic" | "static";
  readonly snapshotGeneratedAt: string | null;
  getInstruments(kind?: "index" | "equity"): Promise<InstrumentMeta[]>;
  getIndicators(): Promise<IndicatorMeta[]>;
  getSnapshot(instrument: string): Promise<SnapshotPayload>;
  getSeries(
    indicatorId: string,
    instrument: string,
    range: RangeKey
  ): Promise<SeriesPayload>;
  getSources(instrument: string): Promise<SourcesPayload>;
  getWatchlistOverview(kind?: "index" | "equity"): Promise<WatchlistOverviewPayload>;
  updateSourcePriority?(payload: {
    profile?: string;
    priority?: string[];
    instrument: string;
  }): Promise<SourcesPayload>;
  triggerRefresh?(instrument: string): Promise<RefreshResult>;
  triggerBackfillHistory?(
    instrument: string,
    years?: number
  ): Promise<BackfillResult>;
  addInstruments?(payload: AddCompaniesRequest): Promise<AddCompaniesResponse>;
  deleteInstrument?(symbol: string): Promise<{ removed: boolean; symbol: string }>;
  importCsv?(payload: CsvImportRequest): Promise<CsvImportResponse>;
  triggerRefreshEquities?(payload?: {
    symbols?: string[];
    async_run?: boolean;
  }): Promise<BatchJobPayload>;
  triggerBackfillEquities?(payload?: {
    symbols?: string[];
    years?: number;
    async_run?: boolean;
  }): Promise<BatchJobPayload>;
  getJobStatus?(jobId: number): Promise<BatchJobPayload>;
}

declare global {
  interface Window {
    __VALUATION_SNAPSHOT__?: StaticSnapshotBundle;
    __VALUATION_SITE__?: StaticSiteConfig;
  }
}
