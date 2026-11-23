import React from "react";
import type { IndicatorMeta, SnapshotPayload } from "../types/valuation";
import {
  formatDelta,
  formatSourceLabel,
  formatValue,
  resolveThreshold,
} from "../utils/format";

interface IndicatorCardRowProps {
  indicators: IndicatorMeta[];
  snapshot: SnapshotPayload;
  selectedIndicatorId: string;
  onSelectIndicator: (indicatorId: string) => void;
  onFocusPercentile?: () => void;
}

export const IndicatorCardRow: React.FC<IndicatorCardRowProps> = ({
  indicators,
  snapshot,
  selectedIndicatorId,
  onSelectIndicator,
  onFocusPercentile,
}) => {
  const visibleIndicators = indicators.filter((ind) => ind.display.card_visible !== false);

  return (
    <section className="card-row" aria-label="核心估值指标">
      {visibleIndicators.map((ind) => {
        const entry = snapshot.indicators[ind.id];
        const latest = entry?.latest;
        const isChartable = ind.display.chartable;
        const isSelected = isChartable && selectedIndicatorId === ind.id;
        const threshold = resolveThreshold(latest?.value, ind.display.thresholds);
        const deltaClass =
          latest?.delta === null || latest?.delta === undefined
            ? "delta-neutral"
            : latest.delta > 0
              ? "delta-up"
              : latest.delta < 0
                ? "delta-down"
                : "delta-neutral";

        const isInsufficient = Boolean(latest?.meta?.insufficient_history);
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
                {threshold && latest?.value !== null && latest?.value !== undefined && (
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
              ) : (
                <div className="metric-card__value">
                  {formatValue(latest?.value, ind.display)}
                </div>
              )}

              <div className={`metric-card__delta ${deltaClass}`}>
                {isInsufficient
                  ? "积累满 1 年后自动显示"
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
