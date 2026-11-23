import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createDataProvider } from "./data/provider";
import type {
  IndicatorMeta,
  InstrumentMeta,
  RangeKey,
  SeriesPayload,
  SnapshotPayload,
  SourcesPayload,
} from "./types/valuation";
import { formatDateTime } from "./utils/format";
import { DataStatusBar } from "./widgets/DataStatusBar";
import { IndicatorCardRow } from "./widgets/IndicatorCardRow";
import { PercentileBarSection } from "./widgets/PercentileBarSection";
import { SourcePriorityPanel } from "./widgets/SourcePriorityPanel";
import { TrendChart } from "./widgets/TrendChart";

const DEFAULT_INSTRUMENT = "NDX";

export const App: React.FC = () => {
  const provider = useMemo(() => createDataProvider(), []);
  const percentileRef = useRef<HTMLElement>(null);

  const [instruments, setInstruments] = useState<InstrumentMeta[]>([]);
  const [instrumentSymbol, setInstrumentSymbol] = useState<string>(DEFAULT_INSTRUMENT);
  const [indicators, setIndicators] = useState<IndicatorMeta[]>([]);
  const [snapshot, setSnapshot] = useState<SnapshotPayload | null>(null);
  const [sourcesPayload, setSourcesPayload] = useState<SourcesPayload | null>(null);
  const [selectedId, setSelectedId] = useState<string>("");
  const [range, setRange] = useState<RangeKey>("5y");
  const [seriesPayload, setSeriesPayload] = useState<SeriesPayload | null>(null);

  const [initialLoading, setInitialLoading] = useState(true);
  const [seriesLoading, setSeriesLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [sourcesUpdating, setSourcesUpdating] = useState(false);
  const [backfilling, setBackfilling] = useState(false);
  const [banner, setBanner] = useState<{ kind: "error" | "info"; text: string } | null>(
    null
  );

  const loadCore = useCallback(
    async (symbol: string) => {
      const [insts, inds, snap, srcs] = await Promise.all([
        provider.getInstruments(),
        provider.getIndicators(),
        provider.getSnapshot(symbol),
        provider.getSources(symbol),
      ]);
      setInstruments(insts);
      setIndicators(inds);
      setSnapshot(snap);
      setSourcesPayload(srcs);
      setSelectedId((prev) => {
        if (prev && inds.some((i) => i.id === prev && i.display.chartable)) {
          return prev;
        }
        const firstChartable = inds.find((i) => i.display.chartable);
        return firstChartable?.id ?? inds[0]?.id ?? "";
      });
    },
    [provider]
  );

  useEffect(() => {
    let cancelled = false;
    setInitialLoading(true);
    setBanner(null);
    loadCore(instrumentSymbol)
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
  }, [loadCore, instrumentSymbol]);

  const loadSeries = useCallback(
    async (indicatorId: string, symbol: string, rangeKey: RangeKey) => {
      if (!indicatorId) return;
      setSeriesLoading(true);
      try {
        const data = await provider.getSeries(indicatorId, symbol, rangeKey);
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
      void loadSeries(selectedId, instrumentSymbol, range);
    }
  }, [selectedId, instrumentSymbol, range, loadSeries]);

  const activeInstrument = useMemo(
    () =>
      instruments.find((i) => i.symbol === instrumentSymbol) ?? {
        symbol: instrumentSymbol,
        name: snapshot?.instrument.name ?? instrumentSymbol,
        en_name: instrumentSymbol === "SPY" ? "S&P 500" : "Nasdaq-100",
        etf_ticker: instrumentSymbol === "SPY" ? "SPY" : "QQQ",
        description:
          instrumentSymbol === "SPY"
            ? "日频追踪标普 500 (SPY) 权重加权前瞻 PE、滚动 PE 及 5 年 / 10 年 / 20 年历史估值分位"
            : "日频追踪纳指 100 (QQQ) 权重加权前瞻 PE、滚动 PE 及 5 年 / 10 年 / 20 年历史估值分位",
      },
    [instruments, instrumentSymbol, snapshot]
  );

  useEffect(() => {
    document.title = `${activeInstrument.name} 估值看板`;
  }, [activeInstrument.name]);

  const handleManualRefresh = async () => {
    if (!provider.triggerRefresh || refreshing) return;
    setRefreshing(true);
    setBanner(null);
    try {
      const res = await provider.triggerRefresh(instrumentSymbol);
      await loadCore(instrumentSymbol);
      if (selectedId) {
        await loadSeries(selectedId, instrumentSymbol, range);
      }
      if (res.status === "failed") {
        setBanner({
          kind: "error",
          text: `刷新失败（已保留上次成功值）：${res.error ?? "未知错误"}`,
        });
      } else {
        setBanner({
          kind: "info",
          text: `刷新完成（${activeInstrument.name} · 日期 ${res.as_of_date ?? "—"}，入库成分股 ${res.constituents_saved} 只）`,
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

  const handleApplyProfile = async (profileId: string) => {
    if (!provider.updateSourcePriority || sourcesUpdating) return;
    setSourcesUpdating(true);
    setBanner(null);
    try {
      const updated = await provider.updateSourcePriority({
        profile: profileId,
        instrument: instrumentSymbol,
      });
      await loadCore(instrumentSymbol);
      if (selectedId) {
        await loadSeries(selectedId, instrumentSymbol, range);
      }
      const label =
        updated.profiles.find((p) => p.id === updated.profile)?.label ?? updated.profile;
      setBanner({
        kind: "info",
        text: `已切换数据源优先级为「${label}」，全部指标已重新计算。`,
      });
    } catch (err) {
      setBanner({
        kind: "error",
        text: `切换数据源优先级失败：${err instanceof Error ? err.message : String(err)}`,
      });
    } finally {
      setSourcesUpdating(false);
    }
  };

  const handleMovePriority = async (
    sourceId: string,
    direction: "up" | "down"
  ) => {
    if (!provider.updateSourcePriority || !sourcesPayload || sourcesUpdating) return;
    const baseOrder = sourcesPayload.sources.map((s) => s.id);
    const idx = baseOrder.indexOf(sourceId);
    const swapIdx = direction === "up" ? idx - 1 : idx + 1;
    if (idx < 0 || swapIdx < 0 || swapIdx >= baseOrder.length) return;

    const nextOrder = [...baseOrder];
    [nextOrder[idx], nextOrder[swapIdx]] = [nextOrder[swapIdx], nextOrder[idx]];

    setSourcesUpdating(true);
    setBanner(null);
    try {
      await provider.updateSourcePriority({
        priority: nextOrder,
        instrument: instrumentSymbol,
      });
      await loadCore(instrumentSymbol);
      if (selectedId) {
        await loadSeries(selectedId, instrumentSymbol, range);
      }
      setBanner({
        kind: "info",
        text: "已更新数据源优先级顺序，全部指标已重新计算。",
      });
    } catch (err) {
      setBanner({
        kind: "error",
        text: `更新数据源优先级失败：${err instanceof Error ? err.message : String(err)}`,
      });
    } finally {
      setSourcesUpdating(false);
    }
  };

  const handleBackfillFree = async () => {
    if (!provider.triggerBackfillHistory || backfilling) return;
    setBackfilling(true);
    setBanner(null);
    try {
      const res = await provider.triggerBackfillHistory(instrumentSymbol, 20);
      await loadCore(instrumentSymbol);
      if (selectedId) {
        await loadSeries(selectedId, instrumentSymbol, range);
      }
      if (res.status === "failed" && res.total_saved === 0) {
        setBanner({
          kind: "error",
          text: `免费历史源拉取失败（已保留现有 20 年数据）。`,
        });
      } else {
        setBanner({
          kind: "info",
          text: `免费 20 年历史回填完成（${activeInstrument.name} · 共入库 ${res.total_saved} 条记录）。`,
        });
      }
    } catch (err) {
      setBanner({
        kind: "error",
        text: `拉取免费 20 年历史失败：${err instanceof Error ? err.message : String(err)}`,
      });
    } finally {
      setBackfilling(false);
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
            <h1 className="topbar__title">{activeInstrument.name} 估值看板</h1>
            <span className="instrument-pill">
              {activeInstrument.symbol} · {activeInstrument.en_name}
            </span>
          </div>
          <p className="topbar__desc">{activeInstrument.description}</p>
        </div>

        <div className="topbar__right">
          {instruments.length > 1 && (
            <div className="instrument-switcher" role="tablist" aria-label="切换指数">
              {instruments.map((inst) => (
                <button
                  key={inst.symbol}
                  type="button"
                  role="tab"
                  aria-selected={inst.symbol === instrumentSymbol}
                  className={`instrument-tab ${
                    inst.symbol === instrumentSymbol ? "instrument-tab--active" : ""
                  }`}
                  onClick={() => setInstrumentSymbol(inst.symbol)}
                >
                  <span>{inst.name}</span>
                  <span className="instrument-tab__sym">{inst.symbol}</span>
                </button>
              ))}
            </div>
          )}

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
              {refreshing
                ? `正在拉取 ${activeInstrument.etf_ticker} & yfinance…`
                : "手动刷新"}
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

          <SourcePriorityPanel
            sourcesPayload={sourcesPayload}
            isStatic={provider.mode === "static"}
            busy={sourcesUpdating}
            backfilling={backfilling}
            onApplyProfile={handleApplyProfile}
            onMovePriority={handleMovePriority}
            onBackfillFree={handleBackfillFree}
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
