import type {
  BackfillResult,
  IndicatorMeta,
  InstrumentMeta,
  RangeKey,
  RefreshResult,
  SeriesPayload,
  SeriesStats,
  SnapshotPayload,
  SourcesPayload,
  StaticSnapshotBundle,
  ValuationDataProvider,
} from "../types/valuation";

const FALLBACK_INSTRUMENTS: InstrumentMeta[] = [
  {
    symbol: "NDX",
    name: "纳指 100",
    en_name: "Nasdaq-100",
    etf_ticker: "QQQ",
    description:
      "日频追踪纳指 100 (QQQ) 权重加权前瞻 PE、滚动 PE 及 5 年 / 10 年 / 20 年历史估值分位",
  },
  {
    symbol: "SPY",
    name: "标普 500",
    en_name: "S&P 500",
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

  async getInstruments(): Promise<InstrumentMeta[]> {
    try {
      const list = await this.fetchJson<InstrumentMeta[]>("/api/instruments");
      return list.length > 0 ? list : FALLBACK_INSTRUMENTS;
    } catch {
      return FALLBACK_INSTRUMENTS;
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
}

class SnapshotDataProvider implements ValuationDataProvider {
  readonly mode = "static" as const;
  readonly snapshotGeneratedAt: string | null;
  private readonly bundle: StaticSnapshotBundle;

  constructor(bundle: StaticSnapshotBundle) {
    this.bundle = bundle;
    this.snapshotGeneratedAt = bundle.generated_at;
  }

  async getInstruments(): Promise<InstrumentMeta[]> {
    if (this.bundle.instruments && this.bundle.instruments.length > 0) {
      return this.bundle.instruments;
    }
    const sym = this.bundle.instrument.toUpperCase();
    return FALLBACK_INSTRUMENTS.filter((i) => i.symbol === sym);
  }

  async getIndicators(): Promise<IndicatorMeta[]> {
    return this.bundle.indicators;
  }

  async getSnapshot(instrument: string): Promise<SnapshotPayload> {
    const key = instrument.toUpperCase();
    if (this.bundle.snapshots && this.bundle.snapshots[key]) {
      return this.bundle.snapshots[key];
    }
    return this.bundle.snapshot;
  }

  async getSeries(
    indicatorId: string,
    instrument: string,
    range: RangeKey
  ): Promise<SeriesPayload> {
    const key = instrument.toUpperCase();
    const seriesMap =
      this.bundle.series_by_instrument?.[key] ?? this.bundle.series;
    const byRange = seriesMap[indicatorId];
    if (!byRange) {
      throw new Error(`快照中缺少指标序列: ${indicatorId}`);
    }
    const direct = byRange[range];
    if (direct) {
      return direct;
    }
    const allPayload = byRange["all"];
    if (allPayload) {
      return sliceSeriesPayload(allPayload, range);
    }
    const fallback = byRange["5y"] ?? Object.values(byRange)[0];
    if (!fallback) {
      throw new Error(`快照中缺少区间数据: ${indicatorId}/${range}`);
    }
    return fallback;
  }

  async getSources(instrument: string): Promise<SourcesPayload> {
    const key = instrument.toUpperCase();
    const payload =
      this.bundle.sources_by_instrument?.[key] ?? this.bundle.sources;
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
}

export function createDataProvider(): ValuationDataProvider {
  if (typeof window !== "undefined" && window.__VALUATION_SNAPSHOT__) {
    return new SnapshotDataProvider(window.__VALUATION_SNAPSHOT__);
  }
  return new ApiDataProvider();
}
