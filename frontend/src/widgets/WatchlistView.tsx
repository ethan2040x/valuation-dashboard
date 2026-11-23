import React, { useMemo, useState } from "react";
import type {
  SnapshotVariant,
  WatchlistItem,
  WatchlistOverviewPayload,
} from "../types/valuation";
import {
  formatMarketCap,
  formatPrice,
  formatSourceLabel,
  resolvePercentileZone,
} from "../utils/format";

export interface BatchProgressState {
  label: string;
  completed: number;
  total: number;
  currentSymbol?: string;
}

interface WatchlistViewProps {
  overview: WatchlistOverviewPayload | null;
  selectedSymbol: string;
  isStatic: boolean;
  loading: boolean;
  batchProgress: BatchProgressState | null;
  onSelectCompany: (symbol: string) => void;
  onOpenAddModal: () => void;
  onOpenCsvModal: (defaultTarget?: string) => void;
  onRefreshAll: () => void;
  onBackfillAll: () => void;
  onDeleteCompany: (symbol: string) => void;
}

type SortKey =
  | "default"
  | "pct_5y_asc"
  | "pct_5y_desc"
  | "pe_ttm_asc"
  | "pe_fwd_asc"
  | "mktcap_desc";

function pickPrimaryPercentile(
  item: WatchlistItem,
  horizon: "5y" | "10y"
): SnapshotVariant | null {
  const ttm =
    horizon === "5y" ? item.percentiles.pe_ttm_5y : item.percentiles.pe_ttm_10y;
  const fwd =
    horizon === "5y" ? item.percentiles.pe_fwd_5y : item.percentiles.pe_fwd_10y;
  if (ttm && ttm.value !== null) return ttm;
  if (fwd && fwd.value !== null) return fwd;
  return ttm ?? fwd ?? null;
}

function renderPeCell(entry: SnapshotVariant | null) {
  if (!entry) return <span className="wl-muted">—</span>;
  if (entry.meta?.negative_eps) {
    return <span className="badge badge--muted">亏损</span>;
  }
  if (entry.meta?.extreme_pe) {
    const raw =
      typeof entry.meta.raw_pe === "number"
        ? `约 ${entry.meta.raw_pe.toFixed(1)}x`
        : undefined;
    return (
      <span className="badge badge--warning" title={raw}>
        &gt;300x
      </span>
    );
  }
  if (entry.value === null || entry.value === undefined) {
    return <span className="wl-muted">—</span>;
  }

  const delta = entry.delta;
  const deltaClass =
    delta === null || delta === undefined || delta === 0
      ? "delta-neutral"
      : delta > 0
        ? "delta-up"
        : "delta-down";
  const sign = delta !== null && delta !== undefined && delta > 0 ? "+" : "";

  return (
    <div className="wl-pe-cell">
      <strong className="wl-num">{entry.value.toFixed(2)}x</strong>
      {delta !== null && delta !== undefined && (
        <span className={`wl-delta ${deltaClass}`}>
          {sign}
          {delta.toFixed(2)}x
        </span>
      )}
    </div>
  );
}

export const WatchlistView: React.FC<WatchlistViewProps> = ({
  overview,
  selectedSymbol,
  isStatic,
  loading,
  batchProgress,
  onSelectCompany,
  onOpenAddModal,
  onOpenCsvModal,
  onRefreshAll,
  onBackfillAll,
  onDeleteCompany,
}) => {
  const [search, setSearch] = useState("");
  const [groupFilter, setGroupFilter] = useState<string>("ALL");
  const [sortKey, setSortKey] = useState<SortKey>("default");
  const [noticeExpanded, setNoticeExpanded] = useState(false);

  const items = overview?.items ?? [];

  const filterTags = useMemo(() => {
    const tags = new Set<string>();
    for (const item of items) {
      if (item.instrument.sector) tags.add(item.instrument.sector);
      else if (item.instrument.group_name) tags.add(item.instrument.group_name);
    }
    return ["ALL", ...Array.from(tags)];
  }, [items]);

  const filteredItems = useMemo(() => {
    const kw = search.trim().toLowerCase();
    const list = items.filter((item) => {
      if (groupFilter !== "ALL") {
        const matchesSector = item.instrument.sector === groupFilter;
        const matchesGroup = item.instrument.group_name === groupFilter;
        if (!matchesSector && !matchesGroup) return false;
      }
      if (!kw) return true;
      return (
        item.instrument.symbol.toLowerCase().includes(kw) ||
        item.instrument.name.toLowerCase().includes(kw) ||
        item.instrument.en_name.toLowerCase().includes(kw) ||
        (item.instrument.sector ?? "").toLowerCase().includes(kw)
      );
    });

    if (sortKey === "default") return list;

    const numOrInf = (v: number | null | undefined, asc: boolean) =>
      v === null || v === undefined ? (asc ? Infinity : -Infinity) : v;

    return [...list].sort((a, b) => {
      if (sortKey === "pct_5y_asc") {
        return (
          numOrInf(pickPrimaryPercentile(a, "5y")?.value, true) -
          numOrInf(pickPrimaryPercentile(b, "5y")?.value, true)
        );
      }
      if (sortKey === "pct_5y_desc") {
        return (
          numOrInf(pickPrimaryPercentile(b, "5y")?.value, false) -
          numOrInf(pickPrimaryPercentile(a, "5y")?.value, false)
        );
      }
      if (sortKey === "pe_ttm_asc") {
        return (
          numOrInf(a.pe_ttm?.value, true) - numOrInf(b.pe_ttm?.value, true)
        );
      }
      if (sortKey === "pe_fwd_asc") {
        return (
          numOrInf(a.pe_fwd?.value, true) - numOrInf(b.pe_fwd?.value, true)
        );
      }
      return (
        numOrInf(b.market_cap, false) - numOrInf(a.market_cap, false)
      );
    });
  }, [items, search, groupFilter, sortKey]);

  const busyBatch = batchProgress !== null;

  const confirmDelete = (item: WatchlistItem) => {
    const sym = item.instrument.symbol;
    if (
      window.confirm(
        `确定从自选池移除 ${item.instrument.name} (${sym}) 并删除其本地估值历史吗？`
      )
    ) {
      onDeleteCompany(sym);
    }
  };

  const renderCompanyIdentity = (item: WatchlistItem) => (
    <div className="wl-company">
      <span className="wl-ticker">{item.instrument.symbol}</span>
      <div className="wl-company__names">
        <span className="wl-company__cn">{item.instrument.name}</span>
        <span className="wl-company__sub">
          {item.instrument.en_name &&
          item.instrument.en_name !== item.instrument.name
            ? `${item.instrument.en_name} · `
            : ""}
          {item.instrument.sector || "美股"}
          {item.market_cap ? ` · ${formatMarketCap(item.market_cap)}` : ""}
        </span>
      </div>
    </div>
  );

  const renderPct5Cell = (item: WatchlistItem) => {
    const pct5 = pickPrimaryPercentile(item, "5y");
    const zone5 = resolvePercentileZone(pct5?.value);
    const clampedPct5 =
      pct5?.value !== null && pct5?.value !== undefined
        ? Math.max(0, Math.min(100, pct5.value))
        : null;

    if (
      pct5?.value !== null &&
      pct5?.value !== undefined &&
      clampedPct5 !== null
    ) {
      return (
        <div className="wl-pct-cell">
          <div className="wl-mini-track">
            <div
              className="wl-mini-fill"
              style={{
                width: `${clampedPct5}%`,
                backgroundColor: zone5?.color ?? "#2563eb",
              }}
            />
          </div>
          <span
            className="wl-pct-val"
            style={{ color: zone5?.color ?? "#0f172a" }}
          >
            {pct5.value.toFixed(1)}%
          </span>
          {Boolean(pct5.meta?.partial_window) && (
            <span
              className="wl-partial-tag"
              title={String(pct5.meta?.note ?? "")}
            >
              {String(pct5.meta?.history_years ?? "")}Y
            </span>
          )}
        </div>
      );
    }

    return (
      <span className="wl-muted">
        {item.history_days > 0 ? "不足 1 年" : "待回填"}
      </span>
    );
  };

  const renderPct10Value = (item: WatchlistItem) => {
    const pct10 = pickPrimaryPercentile(item, "10y");
    if (pct10?.value !== null && pct10?.value !== undefined) {
      return (
        <span
          className="wl-num"
          style={{
            color: resolvePercentileZone(pct10.value)?.color ?? "#0f172a",
          }}
        >
          {pct10.value.toFixed(1)}%
        </span>
      );
    }
    return <span className="wl-muted">—</span>;
  };

  return (
    <section className="panel wl-panel" aria-label="美股自选公司估值总览">
      <div className="wl-header">
        <div>
          <h2 className="panel__title">
            <span className="wl-title-row">
              <span>美股自选公司估值总览</span>
              {loading && items.length > 0 && (
                <span
                  className="wl-spinner wl-spinner--sm"
                  title="正在刷新…"
                  aria-label="正在刷新"
                />
              )}
            </span>
            <span className="panel__subtitle">
              {loading && items.length === 0
                ? "正在按需拉取自选公司列表与历史估值分位…"
                : `共追踪 ${items.length} 家公司 · 点击任意行查看下方完整走势图与分位详情`}
            </span>
          </h2>
        </div>

        {!isStatic && (
          <div className="wl-actions">
            <button
              type="button"
              className="wl-btn wl-btn--primary"
              disabled={busyBatch}
              onClick={onOpenAddModal}
            >
              + 添加公司
            </button>
            <button
              type="button"
              className="wl-btn"
              disabled={busyBatch}
              onClick={() => onOpenCsvModal("AUTO")}
            >
              导入 CSV
            </button>
            <button
              type="button"
              className="wl-btn"
              disabled={busyBatch || items.length === 0}
              onClick={onRefreshAll}
            >
              批量刷新最新
            </button>
            <button
              type="button"
              className="wl-btn wl-btn--teal"
              disabled={busyBatch || items.length === 0}
              onClick={onBackfillAll}
            >
              免费回填 15Y 历史
            </button>
          </div>
        )}
      </div>

      <div
        className={`wl-notice ${noticeExpanded ? "wl-notice--expanded" : ""}`}
        role="note"
      >
        <div className="wl-notice__head">
          <span className="wl-notice__tag">口径提示</span>
          <span className="wl-notice__summary">
            内置美股 Top 10 非金融市值（已剔除金融股，PE 易失真）
          </span>
          <button
            type="button"
            className="wl-notice__toggle"
            aria-expanded={noticeExpanded}
            onClick={() => setNoticeExpanded((v) => !v)}
          >
            {noticeExpanded ? "收起 ▴" : "详情 ▾"}
          </button>
        </div>
        <p className="wl-notice__text">
          默认内置 <strong>美股 Top 10 非金融市值</strong>公司（已剔除伯克希尔{" "}
          <code>BRK.B</code>、摩根大通 <code>JPM</code>{" "}
          等金融股，由非金融市值顺位递补如沃尔玛 <code>WMT</code>
          ）。银行、保险及综合金融企业受高杠杆、准备金计提与投资公允价值浮盈浮亏影响，
          <strong>市盈率 (PE) 易失真</strong>，常规估值更适合结合{" "}
          <strong>PB（市净率）与 ROE</strong>；
          {!isStatic
            ? "如需追踪金融股仍可通过「+ 添加公司」手动加入，但参考其 PE 分位时需谨慎。"
            : "分析金融股时建议搭配 PB / ROE 等指标谨慎参考。"}
        </p>
      </div>

      {batchProgress && (
        <div className="wl-progress" role="status">
          <div className="wl-progress__text">
            <span className="spin">↻</span>
            <span>
              {batchProgress.label}：已完成 {batchProgress.completed} /{" "}
              {batchProgress.total}
              {batchProgress.currentSymbol
                ? `（当前：${batchProgress.currentSymbol}）`
                : ""}
            </span>
          </div>
          <div className="wl-progress__track">
            <div
              className="wl-progress__fill"
              style={{
                width: `${batchProgress.total > 0 ? Math.min(100, Math.round((batchProgress.completed / batchProgress.total) * 100)) : 10}%`,
              }}
            />
          </div>
        </div>
      )}

      <div className="wl-toolbar">
        <input
          type="search"
          className="wl-search"
          placeholder="搜索代码、公司名称或行业（如 NVDA / 苹果 / 半导体）…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />

        <div className="wl-toolbar__right">
          <div className="wl-filter-pills" role="group" aria-label="行业筛选">
            {filterTags.slice(0, 7).map((tag) => (
              <button
                key={tag}
                type="button"
                className={`wl-pill ${groupFilter === tag ? "wl-pill--active" : ""}`}
                onClick={() => setGroupFilter(tag)}
              >
                {tag === "ALL" ? "全部" : tag}
              </button>
            ))}
          </div>

          <select
            className="wl-sort-select"
            aria-label="排序方式"
            value={sortKey}
            onChange={(e) => setSortKey(e.target.value as SortKey)}
          >
            <option value="default">排序：默认顺序</option>
            <option value="pct_5y_asc">5Y 分位：由低到高</option>
            <option value="pct_5y_desc">5Y 分位：由高到低</option>
            <option value="pe_ttm_asc">滚动 PE：由低到高</option>
            <option value="pe_fwd_asc">前瞻 PE：由低到高</option>
            <option value="mktcap_desc">市值：由高到低</option>
          </select>
        </div>
      </div>

      <div className="wl-table-wrap">
        <table className="wl-table">
          <thead>
            <tr>
              <th>公司</th>
              <th className="wl-th-num">最新价</th>
              <th className="wl-th-num">滚动 PE</th>
              <th className="wl-th-num">前瞻 PE</th>
              <th>5Y 历史分位</th>
              <th className="wl-th-num">10Y 分位</th>
              <th>历史数据跨度</th>
              {!isStatic && <th className="wl-th-act">操作</th>}
            </tr>
          </thead>
          <tbody>
            {loading && items.length === 0 ? (
              <>
                <tr>
                  <td colSpan={isStatic ? 7 : 8} className="wl-loading-banner-cell">
                    <div className="wl-loading-banner" role="status">
                      <span className="wl-spinner" aria-hidden="true" />
                      <span>正在按需加载自选公司估值数据…</span>
                    </div>
                  </td>
                </tr>
                {Array.from({ length: 6 }).map((_, idx) => (
                  <tr key={`skeleton-${idx}`} className="wl-skeleton-row" aria-hidden="true">
                    <td>
                      <div className="wl-company">
                        <span className="skeleton-bar skeleton-bar--ticker" />
                        <div className="wl-company__names">
                          <span className="skeleton-bar skeleton-bar--name" />
                          <span className="skeleton-bar skeleton-bar--sub" />
                        </div>
                      </div>
                    </td>
                    <td><span className="skeleton-bar skeleton-bar--num" /></td>
                    <td><span className="skeleton-bar skeleton-bar--num" /></td>
                    <td><span className="skeleton-bar skeleton-bar--num" /></td>
                    <td><span className="skeleton-bar skeleton-bar--track" /></td>
                    <td><span className="skeleton-bar skeleton-bar--num" /></td>
                    <td><span className="skeleton-bar skeleton-bar--span" /></td>
                    {!isStatic && <td><span className="skeleton-bar skeleton-bar--act" /></td>}
                  </tr>
                ))}
              </>
            ) : filteredItems.length === 0 ? (
              <tr>
                <td colSpan={isStatic ? 7 : 8} className="wl-empty-cell">
                  暂无匹配的公司（可点击右上角「+ 添加公司」或清空筛选条件）
                </td>
              </tr>
            ) : (
              filteredItems.map((item) => {
                const sym = item.instrument.symbol;
                const isSelected = sym === selectedSymbol;

                return (
                  <tr
                    key={sym}
                    className={`wl-row ${isSelected ? "wl-row--selected" : ""}`}
                    onClick={() => onSelectCompany(sym)}
                  >
                    <td>{renderCompanyIdentity(item)}</td>

                    <td className="wl-td-num">
                      <span className="wl-num">
                        {formatPrice(item.price, item.instrument.currency)}
                      </span>
                    </td>

                    <td className="wl-td-num">{renderPeCell(item.pe_ttm)}</td>

                    <td className="wl-td-num">{renderPeCell(item.pe_fwd)}</td>

                    <td>{renderPct5Cell(item)}</td>

                    <td className="wl-td-num">{renderPct10Value(item)}</td>

                    <td>
                      {item.history_start && item.history_end ? (
                        <div className="wl-hist-cell">
                          <span className="wl-hist-span">
                            {item.history_start.slice(0, 7)} ~{" "}
                            {item.history_end.slice(0, 7)}
                            {item.history_years !== null
                              ? ` (${item.history_years}年)`
                              : ""}
                          </span>
                          <span className="wl-hist-src">
                            {item.sources
                              .slice(0, 2)
                              .map(formatSourceLabel)
                              .join(" · ")}
                          </span>
                        </div>
                      ) : (
                        <span className="wl-muted">暂无历史</span>
                      )}
                    </td>

                    {!isStatic && (
                      <td className="wl-td-act">
                        <button
                          type="button"
                          className="wl-del-btn"
                          title={`移除 ${sym}`}
                          onClick={(e) => {
                            e.stopPropagation();
                            confirmDelete(item);
                          }}
                        >
                          移除
                        </button>
                      </td>
                    )}
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      <div className="wl-card-list" role="list" aria-label="美股自选公司列表">
        {loading && items.length === 0 ? (
          <>
            <div className="wl-card-loading">
              <div className="wl-loading-banner" role="status">
                <span className="wl-spinner" aria-hidden="true" />
                <span>正在按需加载自选公司估值数据…</span>
              </div>
            </div>
            {Array.from({ length: 4 }).map((_, idx) => (
              <div
                key={`card-skeleton-${idx}`}
                className="wl-card wl-card--skeleton"
                aria-hidden="true"
              >
                <div className="wl-card__top">
                  <div className="wl-company">
                    <span className="skeleton-bar skeleton-bar--ticker" />
                    <div className="wl-company__names">
                      <span className="skeleton-bar skeleton-bar--name" />
                      <span className="skeleton-bar skeleton-bar--sub" />
                    </div>
                  </div>
                  <span className="skeleton-bar skeleton-bar--num" />
                </div>
                <div className="wl-card__metrics">
                  <div className="wl-card__metric">
                    <span className="skeleton-bar skeleton-bar--num" />
                  </div>
                  <div className="wl-card__metric">
                    <span className="skeleton-bar skeleton-bar--num" />
                  </div>
                  <div className="wl-card__metric">
                    <span className="skeleton-bar skeleton-bar--num" />
                  </div>
                </div>
                <div className="wl-card__pct-row">
                  <span className="skeleton-bar skeleton-bar--track" />
                </div>
                <div className="wl-card__foot">
                  <span className="skeleton-bar skeleton-bar--span" />
                </div>
              </div>
            ))}
          </>
        ) : filteredItems.length === 0 ? (
          <div className="wl-card-empty">
            暂无匹配的公司（可点击「+ 添加公司」或清空筛选条件）
          </div>
        ) : (
          filteredItems.map((item) => {
            const sym = item.instrument.symbol;
            const isSelected = sym === selectedSymbol;

            return (
              <article
                key={sym}
                role="listitem"
                tabIndex={0}
                aria-current={isSelected ? "true" : undefined}
                className={`wl-card ${isSelected ? "wl-card--selected" : ""}`}
                onClick={() => onSelectCompany(sym)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    onSelectCompany(sym);
                  }
                }}
              >
                <div className="wl-card__top">
                  {renderCompanyIdentity(item)}
                  <div className="wl-card__top-right">
                    <span className="wl-card__price wl-num">
                      {formatPrice(item.price, item.instrument.currency)}
                    </span>
                    {!isStatic && (
                      <button
                        type="button"
                        className="wl-del-btn"
                        title={`移除 ${sym}`}
                        onClick={(e) => {
                          e.stopPropagation();
                          confirmDelete(item);
                        }}
                        onKeyDown={(e) => e.stopPropagation()}
                      >
                        移除
                      </button>
                    )}
                  </div>
                </div>

                <div className="wl-card__metrics">
                  <div className="wl-card__metric">
                    <span className="wl-card__metric-label">滚动 PE</span>
                    <div className="wl-card__metric-val">
                      {renderPeCell(item.pe_ttm)}
                    </div>
                  </div>
                  <div className="wl-card__metric">
                    <span className="wl-card__metric-label">前瞻 PE</span>
                    <div className="wl-card__metric-val">
                      {renderPeCell(item.pe_fwd)}
                    </div>
                  </div>
                  <div className="wl-card__metric">
                    <span className="wl-card__metric-label">10Y 分位</span>
                    <div className="wl-card__metric-val">
                      {renderPct10Value(item)}
                    </div>
                  </div>
                </div>

                <div className="wl-card__pct-row">
                  <span className="wl-card__pct-label">5Y 历史分位</span>
                  <div className="wl-card__pct-bar">
                    {renderPct5Cell(item)}
                  </div>
                </div>

                <div className="wl-card__foot">
                  <div className="wl-card__hist">
                    {item.history_start && item.history_end ? (
                      <>
                        <span className="wl-hist-span">
                          {item.history_start.slice(0, 7)} ~{" "}
                          {item.history_end.slice(0, 7)}
                          {item.history_years !== null
                            ? ` (${item.history_years}年)`
                            : ""}
                        </span>
                        {item.sources.length > 0 && (
                          <span className="wl-hist-src">
                            {" · "}
                            {item.sources
                              .slice(0, 2)
                              .map(formatSourceLabel)
                              .join(" · ")}
                          </span>
                        )}
                      </>
                    ) : (
                      <span className="wl-muted">暂无历史</span>
                    )}
                  </div>
                  <span className="wl-card__hint">
                    {isSelected ? "当前详情 ↓" : "查看详情 ›"}
                  </span>
                </div>
              </article>
            );
          })
        )}
      </div>
    </section>
  );
};
