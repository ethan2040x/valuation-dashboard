import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createDataProvider } from "./data/provider";
import type {
  AddCompaniesRequest,
  CsvImportRequest,
  IndicatorMeta,
  InstrumentMeta,
  RangeKey,
  SeriesPayload,
  SnapshotPayload,
  SourcesPayload,
  WatchlistOverviewPayload,
} from "./types/valuation";
import { formatDateTime, formatMarketCap, formatPrice } from "./utils/format";
import { AddCompanyModal } from "./widgets/AddCompanyModal";
import { CsvImportModal } from "./widgets/CsvImportModal";
import { DataStatusBar } from "./widgets/DataStatusBar";
import { IndicatorCardRow } from "./widgets/IndicatorCardRow";
import { PercentileBarSection } from "./widgets/PercentileBarSection";
import { SourcePriorityPanel } from "./widgets/SourcePriorityPanel";
import { TrendChart } from "./widgets/TrendChart";
import { WatchlistView, type BatchProgressState } from "./widgets/WatchlistView";

const DEFAULT_INDEX = "NDX";

export const App: React.FC = () => {
  const provider = useMemo(() => createDataProvider(), []);
  const percentileRef = useRef<HTMLElement>(null);
  const detailRef = useRef<HTMLDivElement>(null);
  const pollTimerRef = useRef<number | null>(null);

  const [activeTab, setActiveTab] = useState<"indices" | "equities">("indices");
  const [instruments, setInstruments] = useState<InstrumentMeta[]>([]);
  const [selectedIndexSymbol, setSelectedIndexSymbol] = useState<string>(DEFAULT_INDEX);
  const [selectedEquitySymbol, setSelectedEquitySymbol] = useState<string>("");

  const [indicators, setIndicators] = useState<IndicatorMeta[]>([]);
  const [snapshot, setSnapshot] = useState<SnapshotPayload | null>(null);
  const [sourcesPayload, setSourcesPayload] = useState<SourcesPayload | null>(null);
  const [watchlistOverview, setWatchlistOverview] =
    useState<WatchlistOverviewPayload | null>(null);

  const [selectedId, setSelectedId] = useState<string>("");
  const [range, setRange] = useState<RangeKey>("5y");
  const [seriesPayload, setSeriesPayload] = useState<SeriesPayload | null>(null);

  const [initialLoading, setInitialLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [watchlistLoading, setWatchlistLoading] = useState(false);
  const [seriesLoading, setSeriesLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [sourcesUpdating, setSourcesUpdating] = useState(false);
  const [backfilling, setBackfilling] = useState(false);

  const [addModalOpen, setAddModalOpen] = useState(false);
  const [csvModalState, setCsvModalState] = useState<{
    open: boolean;
    defaultTarget: string;
  }>({ open: false, defaultTarget: "AUTO" });
  const [modalBusy, setModalBusy] = useState(false);
  const [batchProgress, setBatchProgress] = useState<BatchProgressState | null>(null);

  const [banner, setBanner] = useState<{ kind: "error" | "info"; text: string } | null>(
    null
  );

  const indexInstruments = useMemo(
    () => instruments.filter((i) => i.kind !== "equity"),
    [instruments]
  );
  const equityInstruments = useMemo(
    () => instruments.filter((i) => i.kind === "equity"),
    [instruments]
  );

  const activeSymbol =
    activeTab === "indices" ? selectedIndexSymbol : selectedEquitySymbol;

  const stopPolling = useCallback(() => {
    if (pollTimerRef.current !== null) {
      window.clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }, []);

  useEffect(() => {
    return () => stopPolling();
  }, [stopPolling]);

  const loadBootstrap = useCallback(async () => {
    const [insts, inds, overview] = await Promise.all([
      provider.getInstruments(),
      provider.getIndicators(),
      provider.getWatchlistOverview("equity"),
    ]);
    setInstruments(insts);
    setIndicators(inds);
    setWatchlistOverview(overview);

    const firstEq = insts.find((i) => i.kind === "equity")?.symbol ?? "";
    setSelectedEquitySymbol((prev) => {
      if (prev && insts.some((i) => i.symbol === prev && i.kind === "equity")) {
        return prev;
      }
      return firstEq;
    });
    setSelectedId((prev) => {
      if (prev && inds.some((i) => i.id === prev && i.display.chartable)) {
        return prev;
      }
      const firstChartable = inds.find((i) => i.display.chartable);
      return firstChartable?.id ?? inds[0]?.id ?? "";
    });
  }, [provider]);

  const loadDetail = useCallback(
    async (symbol: string) => {
      if (!symbol) {
        setSnapshot(null);
        setSourcesPayload(null);
        setSeriesPayload(null);
        return;
      }
      setDetailLoading(true);
      try {
        const [snap, srcs] = await Promise.all([
          provider.getSnapshot(symbol),
          provider.getSources(symbol),
        ]);
        setSnapshot(snap);
        setSourcesPayload(srcs);
      } finally {
        setDetailLoading(false);
      }
    },
    [provider]
  );

  const reloadWatchlist = useCallback(async () => {
    setWatchlistLoading(true);
    try {
      const [insts, overview] = await Promise.all([
        provider.getInstruments(),
        provider.getWatchlistOverview("equity"),
      ]);
      setInstruments(insts);
      setWatchlistOverview(overview);
      const firstEq = insts.find((i) => i.kind === "equity")?.symbol ?? "";
      setSelectedEquitySymbol((prev) =>
        prev && insts.some((i) => i.symbol === prev && i.kind === "equity")
          ? prev
          : firstEq
      );
    } finally {
      setWatchlistLoading(false);
    }
  }, [provider]);

  useEffect(() => {
    let cancelled = false;
    setInitialLoading(true);
    setBanner(null);
    loadBootstrap()
      .catch((err) => {
        if (!cancelled) {
          setBanner({
            kind: "error",
            text: `加载看板基础数据失败：${err instanceof Error ? err.message : String(err)}`,
          });
        }
      })
      .finally(() => {
        if (!cancelled) setInitialLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [loadBootstrap]);

  useEffect(() => {
    if (initialLoading) return;
    let cancelled = false;
    loadDetail(activeSymbol).catch((err) => {
      if (!cancelled) {
        setBanner({
          kind: "error",
          text: `加载 ${activeSymbol} 估值详情失败：${err instanceof Error ? err.message : String(err)}`,
        });
      }
    });
    return () => {
      cancelled = true;
    };
  }, [activeSymbol, initialLoading, loadDetail]);

  const loadSeries = useCallback(
    async (indicatorId: string, symbol: string, rangeKey: RangeKey) => {
      if (!indicatorId || !symbol) {
        setSeriesPayload(null);
        return;
      }
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
    if (!initialLoading && selectedId && activeSymbol) {
      void loadSeries(selectedId, activeSymbol, range);
    } else if (!activeSymbol) {
      setSeriesPayload(null);
    }
  }, [initialLoading, selectedId, activeSymbol, range, loadSeries]);

  const activeInstrument = useMemo(() => {
    const found = instruments.find((i) => i.symbol === activeSymbol);
    if (found) return found;
    if (activeTab === "equities") {
      return {
        symbol: activeSymbol || "EQUITY",
        name: snapshot?.instrument.name ?? (activeSymbol || "美股自选"),
        en_name: snapshot?.instrument.en_name ?? "",
        kind: "equity" as const,
        sector: snapshot?.instrument.sector ?? "",
        group_name: snapshot?.instrument.group_name ?? "",
        currency: snapshot?.instrument.currency ?? "USD",
        etf_ticker: "",
        description:
          snapshot?.instrument.description ??
          "追踪美股自选公司滚动 PE、前瞻 PE 及历史估值分位",
      };
    }
    return {
      symbol: activeSymbol,
      name: snapshot?.instrument.name ?? activeSymbol,
      en_name: activeSymbol === "SPY" ? "S&P 500" : "Nasdaq-100",
      kind: "index" as const,
      etf_ticker: activeSymbol === "SPY" ? "SPY" : "QQQ",
      description:
        activeSymbol === "SPY"
          ? "日频追踪标普 500 (SPY) 权重加权前瞻 PE、滚动 PE 及 5 年 / 10 年 / 20 年历史估值分位"
          : "日频追踪纳指 100 (QQQ) 权重加权前瞻 PE、滚动 PE 及 5 年 / 10 年 / 20 年历史估值分位",
    };
  }, [instruments, activeSymbol, activeTab, snapshot]);

  useEffect(() => {
    if (activeTab === "equities") {
      document.title = activeSymbol
        ? `${activeInstrument.name} (${activeSymbol}) · 美股估值看板`
        : "美股自选公司估值看板";
    } else {
      document.title = `${activeInstrument.name} 估值看板`;
    }
  }, [activeTab, activeInstrument.name, activeSymbol]);

  const startBatchPolling = useCallback(
    (jobId: number, label: string, initialTotal: number) => {
      if (!provider.getJobStatus) return;
      stopPolling();
      setBatchProgress({ label, completed: 0, total: initialTotal });

      const tick = async () => {
        try {
          const job = await provider.getJobStatus!(jobId);
          const details = job.details ?? {};
          const completed =
            typeof details.completed === "number" ? details.completed : 0;
          const total =
            typeof details.total === "number" ? details.total : initialTotal;
          const currentSymbol =
            typeof details.current_symbol === "string"
              ? details.current_symbol
              : undefined;

          if (job.status === "running") {
            setBatchProgress({ label, completed, total, currentSymbol });
            return;
          }

          stopPolling();
          setBatchProgress(null);
          await reloadWatchlist();
          if (activeSymbol) {
            await loadDetail(activeSymbol);
            if (selectedId) {
              await loadSeries(selectedId, activeSymbol, range);
            }
          }

          if (job.status === "failed") {
            setBanner({
              kind: "error",
              text: `${label}失败：${job.error ?? "请检查网络或数据源状态"}`,
            });
          } else {
            const succeeded =
              typeof details.succeeded === "number"
                ? details.succeeded
                : typeof details.saved === "number"
                  ? details.saved
                  : total;
            setBanner({
              kind: "info",
              text: `${label}完成（成功 ${succeeded} / ${total} 家公司）。`,
            });
          }
        } catch (err) {
          stopPolling();
          setBatchProgress(null);
          setBanner({
            kind: "error",
            text: `查询任务进度失败：${err instanceof Error ? err.message : String(err)}`,
          });
        }
      };

      void tick();
      pollTimerRef.current = window.setInterval(() => {
        void tick();
      }, 1200);
    },
    [
      provider,
      stopPolling,
      reloadWatchlist,
      activeSymbol,
      loadDetail,
      selectedId,
      loadSeries,
      range,
    ]
  );

  const handleManualRefresh = async () => {
    if (!provider.triggerRefresh || !activeSymbol || refreshing) return;
    setRefreshing(true);
    setBanner(null);
    try {
      const res = await provider.triggerRefresh(activeSymbol);
      await loadDetail(activeSymbol);
      if (activeTab === "equities") {
        await reloadWatchlist();
      }
      if (selectedId) {
        await loadSeries(selectedId, activeSymbol, range);
      }
      if (res.status === "failed") {
        setBanner({
          kind: "error",
          text: `刷新失败（已保留上次成功值）：${res.error ?? "未知错误"}`,
        });
      } else if (activeInstrument.kind === "equity") {
        setBanner({
          kind: "info",
          text: `刷新完成（${activeInstrument.name} ${activeSymbol} · 日期 ${res.as_of_date ?? "—"}）。`,
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
    if (!provider.updateSourcePriority || !activeSymbol || sourcesUpdating) return;
    setSourcesUpdating(true);
    setBanner(null);
    try {
      const updated = await provider.updateSourcePriority({
        profile: profileId,
        instrument: activeSymbol,
      });
      await loadDetail(activeSymbol);
      if (activeTab === "equities") {
        await reloadWatchlist();
      }
      if (selectedId) {
        await loadSeries(selectedId, activeSymbol, range);
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
    if (
      !provider.updateSourcePriority ||
      !sourcesPayload ||
      !activeSymbol ||
      sourcesUpdating
    )
      return;
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
        instrument: activeSymbol,
      });
      await loadDetail(activeSymbol);
      if (activeTab === "equities") {
        await reloadWatchlist();
      }
      if (selectedId) {
        await loadSeries(selectedId, activeSymbol, range);
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
    if (!provider.triggerBackfillHistory || !activeSymbol || backfilling) return;
    const targetYears = activeInstrument.kind === "equity" ? 15 : 20;
    setBackfilling(true);
    setBanner(null);
    try {
      const res = await provider.triggerBackfillHistory(activeSymbol, targetYears);
      await loadDetail(activeSymbol);
      if (activeTab === "equities") {
        await reloadWatchlist();
      }
      if (selectedId) {
        await loadSeries(selectedId, activeSymbol, range);
      }
      if (res.status === "failed" && res.total_saved === 0) {
        setBanner({
          kind: "error",
          text: `免费历史源拉取失败（已保留现有数据）。`,
        });
      } else {
        setBanner({
          kind: "info",
          text: `免费 ${targetYears} 年历史回填完成（${activeInstrument.name} · 共入库 ${res.total_saved} 条记录）。`,
        });
      }
    } catch (err) {
      setBanner({
        kind: "error",
        text: `拉取免费 ${targetYears} 年历史失败：${err instanceof Error ? err.message : String(err)}`,
      });
    } finally {
      setBackfilling(false);
    }
  };

  const handleAddCompanies = async (payload: AddCompaniesRequest) => {
    if (!provider.addInstruments) return;
    setModalBusy(true);
    setBanner(null);
    try {
      const res = await provider.addInstruments(payload);
      await reloadWatchlist();
      const addedFirst = res.instruments.find((i) => i.kind === "equity")?.symbol;
      if (addedFirst) {
        setSelectedEquitySymbol(addedFirst);
        setActiveTab("equities");
      }
      if (res.job_id) {
        startBatchPolling(
          res.job_id,
          "批量添加并回填历史",
          res.instruments.length
        );
      } else {
        if (addedFirst) {
          await loadDetail(addedFirst);
          if (selectedId) {
            await loadSeries(selectedId, addedFirst, range);
          }
        }
        setBanner({
          kind: "info",
          text: `已添加 ${res.instruments.map((i) => `${i.name} (${i.symbol})`).join("、")}。`,
        });
      }
    } finally {
      setModalBusy(false);
    }
  };

  const handleDeleteCompany = async (symbol: string) => {
    if (!provider.deleteInstrument) return;
    setBanner(null);
    try {
      await provider.deleteInstrument(symbol);
      await reloadWatchlist();
      setBanner({
        kind: "info",
        text: `已从自选池移除 ${symbol}。`,
      });
    } catch (err) {
      setBanner({
        kind: "error",
        text: `移除 ${symbol} 失败：${err instanceof Error ? err.message : String(err)}`,
      });
    }
  };

  const handleImportCsv = async (payload: CsvImportRequest) => {
    if (!provider.importCsv) {
      throw new Error("当前离线模式不支持导入 CSV");
    }
    setModalBusy(true);
    try {
      const res = await provider.importCsv(payload);
      await reloadWatchlist();
      const firstSym = Object.keys(res.imported_by_symbol)[0];
      const refreshSym =
        activeSymbol && res.imported_by_symbol[activeSymbol]
          ? activeSymbol
          : firstSym;
      if (refreshSym) {
        await loadDetail(refreshSym);
        if (selectedId) {
          await loadSeries(selectedId, refreshSym, range);
        }
      }
      setBanner({
        kind: "info",
        text: `CSV 导入完成：共 ${res.total_rows} 条记录（涉及 ${Object.keys(res.imported_by_symbol).join("、")}），指标已重算。`,
      });
      return res;
    } finally {
      setModalBusy(false);
    }
  };

  const handleRefreshAllEquities = async () => {
    if (!provider.triggerRefreshEquities || batchProgress) return;
    setBanner(null);
    try {
      const res = await provider.triggerRefreshEquities({ async_run: true });
      if (res.job_id && res.status === "running") {
        startBatchPolling(
          res.job_id,
          "自选公司批量刷新",
          res.total ?? equityInstruments.length
        );
      } else {
        await reloadWatchlist();
        if (activeSymbol) await loadDetail(activeSymbol);
        setBanner({ kind: "info", text: "自选公司刷新完成。" });
      }
    } catch (err) {
      setBanner({
        kind: "error",
        text: `批量刷新失败：${err instanceof Error ? err.message : String(err)}`,
      });
    }
  };

  const handleBackfillAllEquities = async () => {
    if (!provider.triggerBackfillEquities || batchProgress) return;
    setBanner(null);
    try {
      const res = await provider.triggerBackfillEquities({
        years: 15,
        async_run: true,
      });
      if (res.job_id && res.status === "running") {
        startBatchPolling(
          res.job_id,
          "自选公司 15 年免费历史回填",
          res.total ?? equityInstruments.length
        );
      } else {
        await reloadWatchlist();
        if (activeSymbol) await loadDetail(activeSymbol);
        setBanner({ kind: "info", text: "自选公司历史回填完成。" });
      }
    } catch (err) {
      setBanner({
        kind: "error",
        text: `批量回填失败：${err instanceof Error ? err.message : String(err)}`,
      });
    }
  };

  const selectedIndicator = useMemo(
    () => indicators.find((i) => i.id === selectedId) ?? null,
    [indicators, selectedId]
  );

  const showDetailSection =
    activeTab === "indices" || (activeTab === "equities" && Boolean(activeSymbol));

  return (
    <div className="page">
      <header className="topbar">
        <div className="topbar__left">
          <div className="topbar__title-row">
            <h1 className="topbar__title">
              {activeTab === "indices"
                ? `${activeInstrument.name} 估值看板`
                : "美股估值看板"}
            </h1>
            {activeSymbol && (
              <span className="instrument-pill">
                {activeInstrument.symbol}
                {activeInstrument.en_name ? ` · ${activeInstrument.en_name}` : ""}
              </span>
            )}
          </div>
          <p className="topbar__desc">
            {activeTab === "indices"
              ? activeInstrument.description
              : "对比 Top 10 及自选美股的滚动 PE、前瞻 PE 与 SEC EDGAR 15 年历史估值分位"}
          </p>
        </div>

        <div className="topbar__right">
          <div className="view-tabs" role="tablist" aria-label="主视图切换">
            <button
              type="button"
              role="tab"
              aria-selected={activeTab === "indices"}
              className={`view-tab ${activeTab === "indices" ? "view-tab--active" : ""}`}
              onClick={() => setActiveTab("indices")}
            >
              大盘指数
              <span className="view-tab__count">{indexInstruments.length}</span>
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={activeTab === "equities"}
              className={`view-tab ${activeTab === "equities" ? "view-tab--active" : ""}`}
              onClick={() => setActiveTab("equities")}
            >
              自选公司
              <span className="view-tab__count">{equityInstruments.length}</span>
            </button>
          </div>

          {activeTab === "indices" && indexInstruments.length > 1 && (
            <div className="instrument-switcher" role="tablist" aria-label="切换指数">
              {indexInstruments.map((inst) => (
                <button
                  key={inst.symbol}
                  type="button"
                  role="tab"
                  aria-selected={inst.symbol === selectedIndexSymbol}
                  className={`instrument-tab ${
                    inst.symbol === selectedIndexSymbol
                      ? "instrument-tab--active"
                      : ""
                  }`}
                  onClick={() => setSelectedIndexSymbol(inst.symbol)}
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
            activeTab === "indices" && (
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
            )
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
      ) : (
        <main className="main-stack">
          {activeTab === "equities" && (
            <WatchlistView
              overview={watchlistOverview}
              selectedSymbol={selectedEquitySymbol}
              isStatic={provider.mode === "static"}
              loading={watchlistLoading}
              batchProgress={batchProgress}
              onSelectCompany={(sym) => {
                setSelectedEquitySymbol(sym);
                setTimeout(() => {
                  detailRef.current?.scrollIntoView({
                    behavior: "smooth",
                    block: "start",
                  });
                }, 40);
              }}
              onOpenAddModal={() => setAddModalOpen(true)}
              onOpenCsvModal={(target = "AUTO") =>
                setCsvModalState({ open: true, defaultTarget: target })
              }
              onRefreshAll={handleRefreshAllEquities}
              onBackfillAll={handleBackfillAllEquities}
              onDeleteCompany={handleDeleteCompany}
            />
          )}

          {showDetailSection && (
            <div ref={detailRef} className="detail-stack">
              {activeTab === "equities" && activeSymbol && (
                <section className="company-context-bar" aria-label="当前公司操作栏">
                  <div className="company-context-bar__left">
                    <span className="wl-ticker wl-ticker--lg">{activeSymbol}</span>
                    <div className="company-context-bar__titles">
                      <div className="company-context-bar__name-row">
                        <strong>{activeInstrument.name}</strong>
                        {activeInstrument.en_name &&
                          activeInstrument.en_name !== activeInstrument.name && (
                            <span className="company-context-bar__en">
                              {activeInstrument.en_name}
                            </span>
                          )}
                        {activeInstrument.sector && (
                          <span className="badge badge--info">
                            {activeInstrument.sector}
                          </span>
                        )}
                      </div>
                      <div className="company-context-bar__quote">
                        <span>
                          最新价：
                          <strong>
                            {formatPrice(
                              snapshot?.latest_quote?.price,
                              activeInstrument.currency
                            )}
                          </strong>
                        </span>
                        {snapshot?.latest_quote?.market_cap ? (
                          <span>
                            市值：
                            <strong>
                              {formatMarketCap(snapshot.latest_quote.market_cap)}
                            </strong>
                          </span>
                        ) : null}
                        {snapshot?.latest_quote?.date && (
                          <span>行情日期：{snapshot.latest_quote.date}</span>
                        )}
                      </div>
                    </div>
                  </div>

                  {provider.mode === "dynamic" && (
                    <div className="company-context-bar__actions">
                      <button
                        type="button"
                        className="wl-btn wl-btn--primary"
                        onClick={handleManualRefresh}
                        disabled={refreshing || detailLoading}
                      >
                        <span className={refreshing ? "spin" : ""}>↻</span>
                        {refreshing ? "正在刷新…" : "刷新该公司"}
                      </button>
                      <button
                        type="button"
                        className="wl-btn wl-btn--teal"
                        onClick={handleBackfillFree}
                        disabled={backfilling || detailLoading}
                      >
                        {backfilling ? "正在回填 15Y…" : "免费回填 15Y"}
                      </button>
                      <button
                        type="button"
                        className="wl-btn"
                        onClick={() =>
                          setCsvModalState({
                            open: true,
                            defaultTarget: activeSymbol,
                          })
                        }
                      >
                        导入 CSV
                      </button>
                    </div>
                  )}
                </section>
              )}

              {snapshot ? (
                <>
                  <IndicatorCardRow
                    indicators={indicators}
                    snapshot={snapshot}
                    selectedIndicatorId={selectedId}
                    instrumentKind={activeInstrument.kind ?? "index"}
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
                      instrumentKind={activeInstrument.kind ?? "index"}
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
                    instrumentKind={activeInstrument.kind ?? "index"}
                    onApplyProfile={handleApplyProfile}
                    onMovePriority={handleMovePriority}
                    onBackfillFree={handleBackfillFree}
                  />

                  <DataStatusBar status={snapshot.status} />
                </>
              ) : (
                <div className="loading-shell">
                  {detailLoading
                    ? `正在加载 ${activeSymbol} 估值数据…`
                    : "未能获取估值数据，请检查后端服务状态。"}
                </div>
              )}
            </div>
          )}
        </main>
      )}

      <AddCompanyModal
        open={addModalOpen}
        busy={modalBusy}
        onClose={() => setAddModalOpen(false)}
        onSubmit={handleAddCompanies}
      />

      <CsvImportModal
        open={csvModalState.open}
        busy={modalBusy}
        instruments={instruments}
        defaultInstrument={csvModalState.defaultTarget}
        onClose={() => setCsvModalState((s) => ({ ...s, open: false }))}
        onSubmit={handleImportCsv}
      />
    </div>
  );
};

export default App;
