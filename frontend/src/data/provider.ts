import type {
  IndicatorMeta,
  InstrumentMeta,
  RangeKey,
  RefreshResult,
  SeriesPayload,
  SnapshotPayload,
  StaticSnapshotBundle,
  ValuationDataProvider,
} from "../types/valuation";

const FALLBACK_INSTRUMENTS: InstrumentMeta[] = [
  {
    symbol: "NDX",
    name: "纳指 100",
    en_name: "Nasdaq-100",
    etf_ticker: "QQQ",
    description: "日频追踪纳指 100 (QQQ) 权重加权前瞻 PE、滚动 PE 及 5 年 / 10 年历史估值分位",
  },
  {
    symbol: "SPY",
    name: "标普 500",
    en_name: "S&P 500",
    etf_ticker: "SPY",
    description: "日频追踪标普 500 (SPY) 权重加权前瞻 PE、滚动 PE 及 5 年 / 10 年历史估值分位",
  },
];

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

  triggerRefresh(instrument: string): Promise<RefreshResult> {
    return this.fetchJson<RefreshResult>(
      `/api/jobs/refresh?instrument=${encodeURIComponent(instrument)}`,
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
    const payload = byRange[range] ?? byRange["5y"] ?? Object.values(byRange)[0];
    if (!payload) {
      throw new Error(`快照中缺少区间数据: ${indicatorId}/${range}`);
    }
    return payload;
  }
}

export function createDataProvider(): ValuationDataProvider {
  if (typeof window !== "undefined" && window.__VALUATION_SNAPSHOT__) {
    return new SnapshotDataProvider(window.__VALUATION_SNAPSHOT__);
  }
  return new ApiDataProvider();
}
