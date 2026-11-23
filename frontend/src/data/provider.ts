import type {
  IndicatorMeta,
  RangeKey,
  RefreshResult,
  SeriesPayload,
  SnapshotPayload,
  StaticSnapshotBundle,
  ValuationDataProvider,
} from "../types/valuation";

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

  async getIndicators(): Promise<IndicatorMeta[]> {
    return this.bundle.indicators;
  }

  async getSnapshot(_instrument: string): Promise<SnapshotPayload> {
    return this.bundle.snapshot;
  }

  async getSeries(
    indicatorId: string,
    _instrument: string,
    range: RangeKey
  ): Promise<SeriesPayload> {
    const byRange = this.bundle.series[indicatorId];
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
