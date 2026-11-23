import type {
  AddCompaniesRequest,
  AddCompaniesResponse,
  BackfillResult,
  BatchJobPayload,
  CsvImportRequest,
  CsvImportResponse,
  IndicatorMeta,
  InstrumentMeta,
  RangeKey,
  RawSeriesPayload,
  RefreshResult,
  SeriesPayload,
  SeriesPoint,
  SeriesStats,
  SnapshotPayload,
  SourcesPayload,
  StaticSiteBootstrapPayload,
  StaticSiteInstrumentBundle,
  StaticSnapshotBundle,
  ValuationDataProvider,
  WatchlistOverviewPayload,
} from "../types/valuation";

const FALLBACK_INSTRUMENTS: InstrumentMeta[] = [
  {
    symbol: "NDX",
    name: "纳指 100",
    en_name: "Nasdaq-100",
    kind: "index",
    etf_ticker: "QQQ",
    description:
      "日频追踪纳指 100 (QQQ) 权重加权前瞻 PE、滚动 PE 及 5 年 / 10 年 / 20 年历史估值分位",
  },
  {
    symbol: "SPY",
    name: "标普 500",
    en_name: "S&P 500",
    kind: "index",
    etf_ticker: "SPY",
    description:
      "日频追踪标普 500 (SPY) 权重加权前瞻 PE、滚动 PE 及 5 年 / 10 年 / 20 年历史估值分位",
  },
];

const RANGE_YEARS_MAP: Record<RangeKey, number | null> = {
  "1y": 1,
  "5y": 5,
  "10y": 10,
  "20y": 20,
  all: null,
};

function subtractYearsIso(isoDate: string, years: number): string {
  const parts = isoDate.split("-").map(Number);
  if (parts.length !== 3 || parts.some(Number.isNaN)) return isoDate;
  const [y, m, d] = parts;
  const targetYear = y - years;
  const maxDay = new Date(Date.UTC(targetYear, m, 0)).getUTCDate();
  const clampedDay = Math.min(d, maxDay);
  return `${String(targetYear).padStart(4, "0")}-${String(m).padStart(2, "0")}-${String(clampedDay).padStart(2, "0")}`;
}

function round4(n: number): number {
  return Math.round(n * 10000) / 10000;
}

function computeSeriesStats(vals: number[]): SeriesStats {
  if (vals.length === 0) {
    return {
      count: 0,
      mean: null,
      std: null,
      plus_1std: null,
      minus_1std: null,
      min: null,
      median: null,
      max: null,
    };
  }
  const mean = vals.reduce((acc, v) => acc + v, 0) / vals.length;
  const variance =
    vals.length > 1
      ? vals.reduce((acc, v) => acc + (v - mean) ** 2, 0) / vals.length
      : 0;
  const std = Math.sqrt(variance);
  const sorted = [...vals].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  const median =
    sorted.length % 2 === 1
      ? sorted[mid]
      : (sorted[mid - 1] + sorted[mid]) / 2;

  return {
    count: vals.length,
    mean: round4(mean),
    std: round4(std),
    plus_1std: round4(mean + std),
    minus_1std: round4(mean - std),
    min: round4(sorted[0]),
    median: round4(median),
    max: round4(sorted[sorted.length - 1]),
  };
}

export function sliceSeriesPayload(
  allPayload: SeriesPayload,
  range: RangeKey
): SeriesPayload {
  const years = RANGE_YEARS_MAP[range];
  const allPoints = allPayload.series;
  const filtered =
    years === null || allPoints.length === 0
      ? allPoints
      : (() => {
          const cutoff = subtractYearsIso(
            allPoints[allPoints.length - 1].date,
            years
          );
          return allPoints.filter((pt) => pt.date >= cutoff);
        })();

  const sources = Array.from(
    new Set(filtered.map((pt) => pt.source).filter(Boolean))
  ).sort();
  const vals = filtered
    .map((pt) => pt.value)
    .filter((v): v is number => v !== null && !Number.isNaN(v));

  return {
    ...allPayload,
    range,
    sources,
    stats: computeSeriesStats(vals),
    series: filtered,
  };
}

class ApiDataProvider implements ValuationDataProvider {
  readonly mode = "dynamic" as const;
  readonly snapshotGeneratedAt = null;
  private readonly baseUrl: string;

  constructor(baseUrl = "") {
    this.baseUrl = baseUrl;
  }

  private async fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
    const res = await fetch(`${this.baseUrl}${url}`, init);
    if (!res.ok) {
      const text = await res.text().catch(() => res.statusText);
      throw new Error(`HTTP ${res.status}: ${text}`);
    }
    return (await res.json()) as T;
  }

  async getInstruments(kind?: "index" | "equity"): Promise<InstrumentMeta[]> {
    try {
      const qs = kind ? `?kind=${encodeURIComponent(kind)}` : "";
      const list = await this.fetchJson<InstrumentMeta[]>(`/api/instruments${qs}`);
      if (list.length > 0) return list;
      return kind ? FALLBACK_INSTRUMENTS.filter((i) => i.kind === kind) : FALLBACK_INSTRUMENTS;
    } catch {
      return kind ? FALLBACK_INSTRUMENTS.filter((i) => i.kind === kind) : FALLBACK_INSTRUMENTS;
    }
  }

  getIndicators(): Promise<IndicatorMeta[]> {
    return this.fetchJson<IndicatorMeta[]>("/api/indicators");
  }

  getSnapshot(instrument: string): Promise<SnapshotPayload> {
    return this.fetchJson<SnapshotPayload>(
      `/api/snapshot?instrument=${encodeURIComponent(instrument)}`
    );
  }

  getSeries(
    indicatorId: string,
    instrument: string,
    range: RangeKey
  ): Promise<SeriesPayload> {
    return this.fetchJson<SeriesPayload>(
      `/api/indicators/${encodeURIComponent(indicatorId)}/series?instrument=${encodeURIComponent(instrument)}&range=${encodeURIComponent(range)}`
    );
  }

  getSources(instrument: string): Promise<SourcesPayload> {
    return this.fetchJson<SourcesPayload>(
      `/api/sources?instrument=${encodeURIComponent(instrument)}`
    );
  }

  getWatchlistOverview(
    kind: "index" | "equity" = "equity"
  ): Promise<WatchlistOverviewPayload> {
    return this.fetchJson<WatchlistOverviewPayload>(
      `/api/watchlist/overview?kind=${encodeURIComponent(kind)}`
    );
  }

  updateSourcePriority(payload: {
    profile?: string;
    priority?: string[];
    instrument: string;
  }): Promise<SourcesPayload> {
    return this.fetchJson<SourcesPayload>("/api/sources/priority", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...payload, recompute: true }),
    });
  }

  triggerRefresh(instrument: string): Promise<RefreshResult> {
    return this.fetchJson<RefreshResult>(
      `/api/jobs/refresh?instrument=${encodeURIComponent(instrument)}`,
      { method: "POST" }
    );
  }

  triggerBackfillHistory(
    instrument: string,
    years = 20
  ): Promise<BackfillResult> {
    return this.fetchJson<BackfillResult>(
      `/api/jobs/backfill-history?instrument=${encodeURIComponent(instrument)}&years=${encodeURIComponent(String(years))}&include_paid=false`,
      { method: "POST" }
    );
  }

  addInstruments(payload: AddCompaniesRequest): Promise<AddCompaniesResponse> {
    return this.fetchJson<AddCompaniesResponse>("/api/instruments", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  }

  deleteInstrument(
    symbol: string
  ): Promise<{ removed: boolean; symbol: string }> {
    return this.fetchJson<{ removed: boolean; symbol: string }>(
      `/api/instruments/${encodeURIComponent(symbol)}`,
      { method: "DELETE" }
    );
  }

  importCsv(payload: CsvImportRequest): Promise<CsvImportResponse> {
    return this.fetchJson<CsvImportResponse>("/api/import/csv", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  }

  triggerRefreshEquities(payload?: {
    symbols?: string[];
    async_run?: boolean;
  }): Promise<BatchJobPayload> {
    return this.fetchJson<BatchJobPayload>("/api/jobs/refresh-equities", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload ?? { async_run: true }),
    });
  }

  triggerBackfillEquities(payload?: {
    symbols?: string[];
    years?: number;
    async_run?: boolean;
  }): Promise<BatchJobPayload> {
    return this.fetchJson<BatchJobPayload>("/api/jobs/backfill-equities", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload ?? { years: 15, async_run: true }),
    });
  }

  getJobStatus(jobId: number): Promise<BatchJobPayload> {
    return this.fetchJson<BatchJobPayload>(`/api/jobs/${encodeURIComponent(String(jobId))}`);
  }
}

function hydrateSeriesPayload(raw: RawSeriesPayload): SeriesPayload {
  if (Array.isArray(raw.series)) {
    return raw as SeriesPayload;
  }
  const compact = raw.compact;
  if (!compact) {
    return {
      ...raw,
      series: [],
    };
  }
  const sourceTable = compact.s ?? [];
  const points: SeriesPoint[] = (compact.d ?? []).map((row) => ({
    date: row[0],
    value: row[1],
    source: sourceTable[row[2]] ?? "",
    is_incomplete: Boolean(row[3]),
  }));
  const { compact: _omitted, ...rest } = raw;
  return {
    ...rest,
    series: points,
  };
}

function resolveRawSeriesRange(
  byRange: Partial<Record<RangeKey, RawSeriesPayload>>,
  range: RangeKey
): SeriesPayload {
  const direct = byRange[range];
  if (direct) {
    return hydrateSeriesPayload(direct);
  }
  const allRaw = byRange["all"];
  if (allRaw) {
    return sliceSeriesPayload(hydrateSeriesPayload(allRaw), range);
  }
  const fallback = byRange["5y"] ?? Object.values(byRange)[0];
  if (!fallback) {
    throw new Error(`快照中缺少区间数据: ${range}`);
  }
  return hydrateSeriesPayload(fallback);
}

class SnapshotDataProvider implements ValuationDataProvider {
  readonly mode = "static" as const;
  readonly snapshotGeneratedAt: string | null;
  private readonly bundle: StaticSnapshotBundle;
  private readonly hydratedCache = new Map<string, SeriesPayload>();

  constructor(bundle: StaticSnapshotBundle) {
    this.bundle = bundle;
    this.snapshotGeneratedAt = bundle.generated_at;
  }

  async getInstruments(kind?: "index" | "equity"): Promise<InstrumentMeta[]> {
    const raw =
      this.bundle.instruments && this.bundle.instruments.length > 0
        ? this.bundle.instruments
        : FALLBACK_INSTRUMENTS.filter(
            (i) => i.symbol === this.bundle.instrument.toUpperCase()
          );
    const normalized = raw.map((inst) => ({
      ...inst,
      kind: inst.kind ?? "index",
    }));
    return kind ? normalized.filter((i) => i.kind === kind) : normalized;
  }

  async getIndicators(): Promise<IndicatorMeta[]> {
    return this.bundle.indicators;
  }

  async getSnapshot(instrument: string): Promise<SnapshotPayload> {
    const key = instrument.toUpperCase();
    if (this.bundle.snapshots && this.bundle.snapshots[key]) {
      return this.bundle.snapshots[key];
    }
    if (key === this.bundle.instrument.toUpperCase()) {
      return this.bundle.snapshot;
    }
    return this.bundle.snapshot;
  }

  async getSeries(
    indicatorId: string,
    instrument: string,
    range: RangeKey
  ): Promise<SeriesPayload> {
    const key = instrument.toUpperCase();
    const cacheKey = `${key}:${indicatorId}:${range}`;
    const cached = this.hydratedCache.get(cacheKey);
    if (cached) {
      return cached;
    }

    const seriesMap =
      this.bundle.series_by_instrument?.[key] ??
      (key === this.bundle.instrument.toUpperCase() ? this.bundle.series : undefined);
    const byRange = seriesMap?.[indicatorId];
    if (!byRange) {
      throw new Error(`快照中缺少指标序列: ${indicatorId}`);
    }

    const allCacheKey = `${key}:${indicatorId}:all`;
    let allPayload = this.hydratedCache.get(allCacheKey);
    if (!allPayload && byRange["all"]) {
      allPayload = hydrateSeriesPayload(byRange["all"]);
      this.hydratedCache.set(allCacheKey, allPayload);
    }

    const result =
      byRange[range] !== undefined
        ? range === "all" && allPayload
          ? allPayload
          : hydrateSeriesPayload(byRange[range]!)
        : allPayload
          ? sliceSeriesPayload(allPayload, range)
          : resolveRawSeriesRange(byRange, range);

    this.hydratedCache.set(cacheKey, result);
    return result;
  }

  async getSources(instrument: string): Promise<SourcesPayload> {
    const key = instrument.toUpperCase();
    const payload =
      this.bundle.sources_by_instrument?.[key] ??
      (key === this.bundle.instrument.toUpperCase() ? this.bundle.sources : undefined);
    if (payload) {
      return payload;
    }
    return {
      instrument: key,
      profile: "free_first",
      priority: [],
      profiles: [],
      sources: [],
    };
  }

  async getWatchlistOverview(
    kind: "index" | "equity" = "equity"
  ): Promise<WatchlistOverviewPayload> {
    if (this.bundle.watchlist_overview && this.bundle.watchlist_overview.kind === kind) {
      return this.bundle.watchlist_overview;
    }
    return {
      kind,
      generated_at: this.bundle.generated_at,
      count: 0,
      items: [],
    };
  }
}

class StaticSiteDataProvider implements ValuationDataProvider {
  readonly mode = "static" as const;
  private _snapshotGeneratedAt: string | null = null;
  private readonly dataBase: string;
  private bootstrapPromise: Promise<StaticSiteBootstrapPayload> | null = null;
  private watchlistPromise: Promise<WatchlistOverviewPayload> | null = null;
  private readonly instrumentPromises = new Map<
    string,
    Promise<StaticSiteInstrumentBundle>
  >();
  private readonly hydratedCache = new Map<string, SeriesPayload>();

  constructor(dataBase = "./data") {
    this.dataBase = dataBase.replace(/\/+$/, "");
  }

  get snapshotGeneratedAt(): string | null {
    return this._snapshotGeneratedAt;
  }

  private async fetchJson<T>(relPath: string): Promise<T> {
    const res = await fetch(`${this.dataBase}/${relPath.replace(/^\/+/, "")}`);
    if (!res.ok) {
      throw new Error(`HTTP ${res.status} 加载静态数据失败: ${relPath}`);
    }
    return (await res.json()) as T;
  }

  private loadBootstrap(): Promise<StaticSiteBootstrapPayload> {
    if (!this.bootstrapPromise) {
      this.bootstrapPromise = this.fetchJson<StaticSiteBootstrapPayload>(
        "bootstrap.json"
      ).then((data) => {
        this._snapshotGeneratedAt = data.generated_at ?? null;
        return data;
      });
    }
    return this.bootstrapPromise;
  }

  private loadInstrumentBundle(
    symbol: string
  ): Promise<StaticSiteInstrumentBundle> {
    const key = symbol.toUpperCase();
    let pending = this.instrumentPromises.get(key);
    if (!pending) {
      pending = this.fetchJson<StaticSiteInstrumentBundle>(
        `instruments/${encodeURIComponent(key)}.json`
      );
      this.instrumentPromises.set(key, pending);
    }
    return pending;
  }

  async getInstruments(kind?: "index" | "equity"): Promise<InstrumentMeta[]> {
    const boot = await this.loadBootstrap();
    const normalized = (boot.instruments ?? []).map((inst) => ({
      ...inst,
      kind: inst.kind ?? "index",
    }));
    return kind ? normalized.filter((i) => i.kind === kind) : normalized;
  }

  async getIndicators(): Promise<IndicatorMeta[]> {
    const boot = await this.loadBootstrap();
    return boot.indicators;
  }

  async getSnapshot(instrument: string): Promise<SnapshotPayload> {
    const key = instrument.toUpperCase();
    const boot = await this.loadBootstrap();
    if (boot.index_snapshots && boot.index_snapshots[key]) {
      return boot.index_snapshots[key];
    }
    const instBundle = await this.loadInstrumentBundle(key);
    if (instBundle.snapshot) {
      return instBundle.snapshot;
    }
    throw new Error(`静态站点缺少 ${key} 快照数据`);
  }

  async getSources(instrument: string): Promise<SourcesPayload> {
    const key = instrument.toUpperCase();
    const boot = await this.loadBootstrap();
    if (boot.index_sources && boot.index_sources[key]) {
      return boot.index_sources[key];
    }
    const instBundle = await this.loadInstrumentBundle(key);
    if (instBundle.sources) {
      return instBundle.sources;
    }
    return {
      instrument: key,
      profile: "free_first",
      priority: [],
      profiles: [],
      sources: [],
    };
  }

  async getSeries(
    indicatorId: string,
    instrument: string,
    range: RangeKey
  ): Promise<SeriesPayload> {
    const key = instrument.toUpperCase();
    const cacheKey = `${key}:${indicatorId}:${range}`;
    const cached = this.hydratedCache.get(cacheKey);
    if (cached) {
      return cached;
    }

    const instBundle = await this.loadInstrumentBundle(key);
    const byRange = instBundle.series?.[indicatorId];
    if (!byRange) {
      throw new Error(`静态数据中缺少指标序列: ${key}/${indicatorId}`);
    }

    const allCacheKey = `${key}:${indicatorId}:all`;
    let allPayload = this.hydratedCache.get(allCacheKey);
    if (!allPayload && byRange["all"]) {
      allPayload = hydrateSeriesPayload(byRange["all"]);
      this.hydratedCache.set(allCacheKey, allPayload);
    }

    const result =
      byRange[range] !== undefined
        ? range === "all" && allPayload
          ? allPayload
          : hydrateSeriesPayload(byRange[range]!)
        : allPayload
          ? sliceSeriesPayload(allPayload, range)
          : resolveRawSeriesRange(byRange, range);

    this.hydratedCache.set(cacheKey, result);
    return result;
  }

  async getWatchlistOverview(
    kind: "index" | "equity" = "equity"
  ): Promise<WatchlistOverviewPayload> {
    if (kind !== "equity") {
      const boot = await this.loadBootstrap();
      return {
        kind,
        generated_at: boot.generated_at,
        count: 0,
        items: [],
      };
    }
    if (!this.watchlistPromise) {
      this.watchlistPromise = this.fetchJson<WatchlistOverviewPayload>(
        "watchlist.json"
      );
    }
    return this.watchlistPromise;
  }
}

export function createDataProvider(): ValuationDataProvider {
  if (typeof window !== "undefined") {
    if (window.__VALUATION_SNAPSHOT__) {
      return new SnapshotDataProvider(window.__VALUATION_SNAPSHOT__);
    }
    if (window.__VALUATION_SITE__) {
      return new StaticSiteDataProvider(window.__VALUATION_SITE__.dataBase);
    }
  }
  return new ApiDataProvider();
}
