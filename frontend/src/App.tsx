import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createDataProvider } from "./data/provider";
import type {
  IndicatorMeta,
  RangeKey,
  SeriesPayload,
  SnapshotPayload,
} from "./types/valuation";
import { formatDateTime } from "./utils/format";
import { DataStatusBar } from "./widgets/DataStatusBar";
import { IndicatorCardRow } from "./widgets/IndicatorCardRow";
import { PercentileBarSection } from "./widgets/PercentileBarSection";
import { TrendChart } from "./widgets/TrendChart";

const INSTRUMENT = "NDX";

export const App: React.FC = () => {
  const provider = useMemo(() => createDataProvider(), []);
  const percentileRef = useRef<HTMLElement>(null);

  const [indicators, setIndicators] = useState<IndicatorMeta[]>([]);
  const [snapshot, setSnapshot] = useState<SnapshotPayload | null>(null);
  const [selectedId, setSelectedId] = useState<string>("");
  const [range, setRange] = useState<RangeKey>("5y");
  const [seriesPayload, setSeriesPayload] = useState<SeriesPayload | null>(null);

  const [initialLoading, setInitialLoading] = useState(true);
  const [seriesLoading, setSeriesLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [banner, setBanner] = useState<{ kind: "error" | "info"; text: string } | null>(
    null
  );

  const loadCore = useCallback(async () => {
    const [inds, snap] = await Promise.all([
      provider.getIndicators(),
      provider.getSnapshot(INSTRUMENT),
    ]);
    setIndicators(inds);
    setSnapshot(snap);
    setSelectedId((prev) => {
      if (prev && inds.some((i) => i.id === prev && i.display.chartable)) {
        return prev;
      }
      const firstChartable = inds.find((i) => i.display.chartable);
      return firstChartable?.id ?? inds[0]?.id ?? "";
    });
  }, [provider]);

  useEffect(() => {
    let cancelled = false;
    setInitialLoading(true);
    loadCore()
      .catch((err) => {
        if (!cancelled) {
          setBanner({
            kind: "error",
            text: `加载看板数据失败：${err instanceof Error ? err.message : String(err)}`,
          });
        }
      })
      .finally(() => {
        if (!cancelled) setInitialLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [loadCore]);

  const loadSeries = useCallback(
    async (indicatorId: string, rangeKey: RangeKey) => {
      if (!indicatorId) return;
      setSeriesLoading(true);
      try {
        const data = await provider.getSeries(indicatorId, INSTRUMENT, rangeKey);
        setSeriesPayload(data);
      } catch (err) {
        setBanner({
          kind: "error",
          text: `加载时间序列失败：${err instanceof Error ? err.message : String(err)}`,
        });
      } finally {
        setSeriesLoading(false);
      }
    },
    [provider]
  );

  useEffect(() => {
    if (selectedId) {
      void loadSeries(selectedId, range);
    }
  }, [selectedId, range, loadSeries]);

  const handleManualRefresh = async () => {
    if (!provider.triggerRefresh || refreshing) return;
    setRefreshing(true);
    setBanner(null);
    try {
      const res = await provider.triggerRefresh(INSTRUMENT);
      await loadCore();
      if (selectedId) {
        await loadSeries(selectedId, range);
      }
      if (res.status === "failed") {
        setBanner({
          kind: "error",
          text: `刷新失败（已保留上次成功值）：${res.error ?? "未知错误"}`,
        });
      } else {
        setBanner({
          kind: "info",
          text: `刷新完成（日期 ${res.as_of_date ?? "—"}，入库成分股 ${res.constituents_saved} 只）`,
        });
      }
    } catch (err) {
      setBanner({
        kind: "error",
        text: `触发刷新失败：${err instanceof Error ? err.message : String(err)}`,
      });
    } finally {
      setRefreshing(false);
    }
  };

  const selectedIndicator = useMemo(
    () => indicators.find((i) => i.id === selectedId) ?? null,
    [indicators, selectedId]
  );

  return (
    <div className="page">
      <header className="topbar">
        <div className="topbar__left">
          <div className="topbar__title-row">
            <h1 className="topbar__title">纳指 100 估值看板</h1>
            <span className="instrument-pill">
              {snapshot?.instrument.symbol ?? INSTRUMENT} · Nasdaq-100
            </span>
          </div>
          <p className="topbar__desc">
            日频追踪纳指 100 指数权重加权前瞻 PE、滚动 PE 及 5 年 / 10 年历史估值分位
          </p>
        </div>

        <div className="topbar__right">
          {provider.mode === "static" ? (
            <div className="snapshot-stamp" title="离线静态快照，不依赖后端服务">
              <span className="snapshot-stamp__dot" />
              <span>
                静态快照生成时间：
                {formatDateTime(provider.snapshotGeneratedAt ?? snapshot?.generated_at)}
              </span>
            </div>
          ) : (
            <button
              type="button"
              className="refresh-btn"
              onClick={handleManualRefresh}
              disabled={refreshing || initialLoading}
            >
              <span className={refreshing ? "spin" : ""}>↻</span>
              {refreshing ? "正在拉取 QQQ & yfinance…" : "手动刷新"}
            </button>
          )}
        </div>
      </header>

      {banner && (
        <div
          className={`banner ${banner.kind === "error" ? "banner--error" : "banner--info"}`}
          role="status"
        >
          <span>{banner.text}</span>
          <button
            type="button"
            className="banner__close"
            onClick={() => setBanner(null)}
            aria-label="关闭提示"
          >
            ×
          </button>
        </div>
      )}

      {initialLoading ? (
        <div className="loading-shell">正在加载估值数据…</div>
      ) : snapshot ? (
        <main className="main-stack">
          <IndicatorCardRow
            indicators={indicators}
            snapshot={snapshot}
            selectedIndicatorId={selectedId}
            onSelectIndicator={(id) => setSelectedId(id)}
            onFocusPercentile={() =>
              percentileRef.current?.scrollIntoView({ behavior: "smooth" })
            }
          />

          {selectedIndicator && (
            <TrendChart
              indicator={selectedIndicator}
              seriesPayload={seriesPayload}
              range={range}
              loading={seriesLoading}
              onRangeChange={setRange}
            />
          )}

          <PercentileBarSection
            indicators={indicators}
            snapshot={snapshot}
            sectionRef={percentileRef}
          />

          <DataStatusBar status={snapshot.status} />
        </main>
      ) : (
        <div className="loading-shell">未能获取估值数据，请检查后端服务状态。</div>
      )}
    </div>
  );
};

export default App;
