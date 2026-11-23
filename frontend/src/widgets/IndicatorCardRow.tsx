import React from "react";
import type { IndicatorMeta, SnapshotPayload } from "../types/valuation";
import {
  formatDelta,
  formatSourceLabel,
  formatValue,
  resolvePercentileZone,
  resolveThreshold,
} from "../utils/format";

interface IndicatorCardRowProps {
  indicators: IndicatorMeta[];
  snapshot: SnapshotPayload;
  selectedIndicatorId: string;
  instrumentKind?: "index" | "equity";
  onSelectIndicator: (indicatorId: string) => void;
  onFocusPercentile?: () => void;
}

export const IndicatorCardRow: React.FC<IndicatorCardRowProps> = ({
  indicators,
  snapshot,
  selectedIndicatorId,
  instrumentKind = "index",
  onSelectIndicator,
  onFocusPercentile,
}) => {
  const visibleIndicators = indicators.filter((ind) => ind.display.card_visible !== false);
  const pctVariants = snapshot.indicators["pe_percentile"]?.variants ?? [];

  return (
    <section className="card-row" aria-label="核心估值指标">
      {visibleIndicators.map((ind) => {
        const entry = snapshot.indicators[ind.id];
        const latest = entry?.latest;
        const isChartable = ind.display.chartable;
        const isSelected = isChartable && selectedIndicatorId === ind.id;

        const isNegativeEps = Boolean(latest?.meta?.negative_eps);
        const isExtremePe = Boolean(latest?.meta?.extreme_pe);
        const rawExtremePe =
          typeof latest?.meta?.raw_pe === "number" ? latest.meta.raw_pe : null;
        const isInsufficient = Boolean(latest?.meta?.insufficient_history);

        let threshold = resolveThreshold(latest?.value, ind.display.thresholds);
        if (
          instrumentKind === "equity" &&
          ind.display.kind === "metric_card" &&
          (ind.id === "pe_ttm" || ind.id === "pe_fwd")
        ) {
          if (isNegativeEps || isExtremePe) {
            threshold = null;
          } else {
            const pct5 = pctVariants.find(
              (v) => v.params.base === ind.id && v.params.window_years === 5 && v.value !== null
            );
            const pct10 = pctVariants.find(
              (v) => v.params.base === ind.id && v.params.window_years === 10 && v.value !== null
            );
            threshold = resolvePercentileZone(pct5?.value ?? pct10?.value ?? null);
          }
        }

        const deltaClass =
          latest?.delta === null || latest?.delta === undefined
            ? "delta-neutral"
            : latest.delta > 0
              ? "delta-up"
              : latest.delta < 0
                ? "delta-down"
                : "delta-neutral";

        const partialNote =
          typeof latest?.meta?.note === "string" && !isInsufficient
            ? latest.meta.note
            : null;

        // Subtitle for parameterized indicators (e.g. default 5Y rolling PE percentile)
        let paramHint: string | null = null;
        if (ind.display.kind === "percentile_bar" && latest?.params) {
          const baseId = String(latest.params.base ?? "");
          const baseName =
            indicators.find((i) => i.id === baseId)?.name ?? baseId;
          const win = latest.params.window_years;
          if (baseName && win) {
            paramHint = `${baseName} · ${win}年`;
          }
        }

        const handleClick = () => {
          if (isChartable) {
            onSelectIndicator(ind.id);
          } else if (ind.display.kind === "percentile_bar" && onFocusPercentile) {
            onFocusPercentile();
          }
        };

        return (
          <article
            key={ind.id}
            className={`metric-card ${isSelected ? "metric-card--selected" : ""} ${isChartable ? "metric-card--clickable" : ""}`}
            onClick={handleClick}
            role={isChartable ? "button" : undefined}
            tabIndex={isChartable ? 0 : undefined}
            onKeyDown={(e) => {
              if (isChartable && (e.key === "Enter" || e.key === " ")) {
                e.preventDefault();
                handleClick();
              }
            }}
          >
            <div className="metric-card__header">
              <div className="metric-card__title-group">
                <span className="metric-card__name">{ind.name}</span>
                {paramHint && <span className="metric-card__subtag">{paramHint}</span>}
              </div>
              <div className="metric-card__badges">
                {latest?.is_incomplete && (
                  <span
                    className="badge badge--warning"
                    title="缺数据成分股权重合计超过 5%，当日数值标记为不完整"
                  >
                    不完整
                  </span>
                )}
                {isNegativeEps && (
                  <span className="badge badge--muted" title="最新一期每股收益 ≤ 0">
                    亏损
                  </span>
                )}
                {isExtremePe && (
                  <span
                    className="badge badge--warning"
                    title={
                      rawExtremePe !== null
                        ? `隐含市盈率约 ${rawExtremePe.toFixed(1)}x（超过 300x 阈值，未计入分位与均值）`
                        : "PE > 300x 极端值"
                    }
                  >
                    极端高估
                  </span>
                )}
                {threshold &&
                  (latest?.value !== null && latest?.value !== undefined) && (
                  <span
                    className="badge badge--zone"
                    style={{
                      color: threshold.color,
                      borderColor: `${threshold.color}40`,
                      backgroundColor: `${threshold.color}12`,
                    }}
                  >
                    {threshold.label}
                  </span>
                )}
              </div>
            </div>

            <div className="metric-card__body">
              {isInsufficient ? (
                <div className="metric-card__insufficient">历史不足 1 年</div>
              ) : isNegativeEps ? (
                <div className="metric-card__insufficient">亏损 (EPS ≤ 0)</div>
              ) : isExtremePe ? (
                <div
                  className="metric-card__value"
                  title={rawExtremePe !== null ? `原始值 ${rawExtremePe.toFixed(1)}x` : undefined}
                >
                  &gt;300x
                </div>
              ) : (
                <div className="metric-card__value">
                  {formatValue(latest?.value, ind.display)}
                </div>
              )}

              <div className={`metric-card__delta ${deltaClass}`}>
                {isInsufficient
                  ? "积累满 1 年后自动显示"
                  : isNegativeEps
                    ? "公司当前净亏损，PE 不具可比性"
                    : isExtremePe
                      ? rawExtremePe !== null
                        ? `微利期隐含 PE ≈ ${rawExtremePe.toFixed(1)}x`
                        : "微利期极端值"
                      : formatDelta(latest?.delta, latest?.delta_pct, ind.display)}
              </div>
            </div>

            <div className="metric-card__footer">
              <span className="metric-card__meta" title={ind.description}>
                日期：{latest?.date ?? "—"}
              </span>
              <span className="metric-card__source">
                {formatSourceLabel(latest?.source)}
              </span>
            </div>

            {partialNote && (
              <div className="metric-card__note">{partialNote}</div>
            )}
          </article>
        );
      })}
    </section>
  );
};
