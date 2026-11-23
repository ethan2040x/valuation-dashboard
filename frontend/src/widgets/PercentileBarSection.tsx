import React from "react";
import type {
  IndicatorMeta,
  SnapshotPayload,
  SnapshotVariant,
} from "../types/valuation";
import {
  formatSourceLabel,
  formatValue,
  resolveThreshold,
} from "../utils/format";
import { FormulaExplainer } from "./FormulaExplainer";

interface PercentileBarSectionProps {
  indicators: IndicatorMeta[];
  snapshot: SnapshotPayload;
  sectionRef?: React.RefObject<HTMLElement>;
}

export const PercentileBarSection: React.FC<PercentileBarSectionProps> = ({
  indicators,
  snapshot,
  sectionRef,
}) => {
  const percentileIndicators = indicators.filter(
    (ind) => ind.display.kind === "percentile_bar"
  );

  if (percentileIndicators.length === 0) {
    return null;
  }

  return (
    <section
      ref={sectionRef}
      className="panel"
      aria-label="历史分位区间"
    >
      {percentileIndicators.map((ind) => {
        const entry = snapshot.indicators[ind.id];
        const variants = entry?.variants ?? [];

        // Group variants by `base` param (e.g. pe_fwd, pe_ttm)
        const groups = new Map<string, SnapshotVariant[]>();
        for (const v of variants) {
          const baseKey = String(v.params.base ?? "default");
          const list = groups.get(baseKey) ?? [];
          list.push(v);
          groups.set(baseKey, list);
        }

        return (
          <div key={ind.id} className="percentile-block">
            <div className="panel__header">
              <div>
                <h2 className="panel__title">
                  {ind.name}
                  <span className="panel__subtitle">{ind.description}</span>
                </h2>
              </div>
            </div>

            <FormulaExplainer formula={ind.formula} defaultOpen={false} />

            <div className="percentile-grid">
              {Array.from(groups.entries()).map(([baseKey, items]) => {
                const baseIndicator = indicators.find((i) => i.id === baseKey);
                const baseTitle = baseIndicator?.name ?? baseKey;
                const baseDisplay = baseIndicator?.display ?? {
                  precision: 2,
                  suffix: "x",
                };

                return (
                  <div key={baseKey} className="percentile-group">
                    <h3 className="percentile-group__title">{baseTitle}</h3>
                    <div className="percentile-bars">
                      {items.map((item, idx) => {
                        const winYears = item.params.window_years;
                        const meta = item.meta ?? {};
                        const insufficient =
                          item.value === null || Boolean(meta.insufficient_history);
                        const partialNote =
                          meta.partial_window && typeof meta.note === "string"
                            ? meta.note
                            : null;
                        const minVal =
                          typeof meta.min === "number" ? meta.min : null;
                        const medianVal =
                          typeof meta.median === "number" ? meta.median : null;
                        const maxVal =
                          typeof meta.max === "number" ? meta.max : null;
                        const currentBaseVal =
                          typeof meta.current_base_value === "number"
                            ? meta.current_base_value
                            : null;
                        const sources = Array.isArray(meta.sources)
                          ? (meta.sources as string[])
                          : item.source
                            ? [item.source]
                            : [];

                        const pct = item.value ?? 0;
                        const clampedPct = Math.max(0, Math.min(100, pct));
                        const band = resolveThreshold(
                          item.value,
                          ind.display.thresholds
                        );

                        return (
                          <div key={`${baseKey}-${winYears ?? idx}`} className="pbar-card">
                            <div className="pbar-card__top">
                              <div className="pbar-card__label">
                                <span>{winYears ? `近 ${winYears} 年` : "历史区间"}</span>
                                {partialNote && (
                                  <span className="badge badge--info">{partialNote}</span>
                                )}
                                {item.is_incomplete && (
                                  <span className="badge badge--warning">当日不完整</span>
                                )}
                              </div>
                              <div className="pbar-card__value">
                                {insufficient ? (
                                  <span className="pbar-muted">不显示</span>
                                ) : (
                                  <>
                                    <strong style={{ color: band?.color ?? "#0f172a" }}>
                                      {formatValue(item.value, ind.display)}
                                    </strong>
                                    {currentBaseVal !== null && (
                                      <span className="pbar-current-base">
                                        （当前 {formatValue(currentBaseVal, baseDisplay)}）
                                      </span>
                                    )}
                                  </>
                                )}
                              </div>
                            </div>

                            {insufficient ? (
                              <div className="pbar-empty">
                                实际历史不足 1 年，暂不显示分位
                              </div>
                            ) : (
                              <>
                                <div className="pbar-track-wrap">
                                  <div className="pbar-track">
                                    <div className="pbar-zone pbar-zone--low" />
                                    <div className="pbar-zone pbar-zone--mid" />
                                    <div className="pbar-zone pbar-zone--high" />
                                    <div className="pbar-median-tick" title="中位数位置 (50%)" />
                                    <div
                                      className="pbar-marker"
                                      style={{
                                        left: `${clampedPct}%`,
                                        borderColor: band?.color ?? "#2563eb",
                                      }}
                                      title={`当前分位：${formatValue(item.value, ind.display)}`}
                                    />
                                  </div>
                                </div>

                                <div className="pbar-scale">
                                  <span>
                                    最小 {formatValue(minVal, baseDisplay)}
                                  </span>
                                  <span>
                                    中位 {formatValue(medianVal, baseDisplay)}
                                  </span>
                                  <span>
                                    最大 {formatValue(maxVal, baseDisplay)}
                                  </span>
                                </div>
                              </>
                            )}

                            <div className="pbar-source">
                              历史数据来源：
                              {sources.length > 0
                                ? sources.map(formatSourceLabel).join(" + ")
                                : "暂无"}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        );
      })}
    </section>
  );
};
