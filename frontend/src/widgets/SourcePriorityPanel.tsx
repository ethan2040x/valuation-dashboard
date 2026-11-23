import React, { useState } from "react";
import type { SourceCatalogItem, SourcesPayload } from "../types/valuation";
import { formatTierLabel } from "../utils/format";

interface SourcePriorityPanelProps {
  sourcesPayload: SourcesPayload | null;
  isStatic: boolean;
  busy: boolean;
  backfilling: boolean;
  onApplyProfile?: (profileId: string) => void;
  onMovePriority?: (sourceId: string, direction: "up" | "down") => void;
  onBackfillFree?: () => void;
}

function formatCoverage(src: SourceCatalogItem): string {
  const entries = Object.entries(src.coverage);
  if (entries.length === 0) {
    if (!src.configured && src.requires_env) {
      return `需配置 ${src.requires_env}`;
    }
    return "暂无入库数据";
  }
  return entries
    .map(([sid, cov]) => {
      const label = sid === "pe_ttm" ? "滚动 PE" : sid === "pe_fwd" ? "前瞻 PE" : sid;
      return `${label}: ${cov.min_date} ~ ${cov.max_date} (${cov.count} 天)`;
    })
    .join(" · ");
}

export const SourcePriorityPanel: React.FC<SourcePriorityPanelProps> = ({
  sourcesPayload,
  isStatic,
  busy,
  backfilling,
  onApplyProfile,
  onMovePriority,
  onBackfillFree,
}) => {
  const [open, setOpen] = useState(false);

  if (!sourcesPayload || sourcesPayload.sources.length === 0) {
    return null;
  }

  const activeSources = sourcesPayload.sources.filter((s) => s.active_in_db);
  const currentProfileMeta = sourcesPayload.profiles.find(
    (p) => p.id === sourcesPayload.profile
  );
  const profileLabel =
    currentProfileMeta?.label ??
    (sourcesPayload.profile === "custom" ? "自定义优先级" : sourcesPayload.profile);

  return (
    <section className="panel source-panel" aria-label="数据源与优先级">
      <div className="source-panel__bar">
        <div className="source-panel__summary">
          <span className="source-panel__tag">数据源优先级</span>
          <span className="badge badge--info">{profileLabel}</span>
          <span className="source-panel__active-list">
            当前已入库：
            {activeSources.length > 0
              ? activeSources
                  .map((s) => `#${s.priority_rank} ${s.name}`)
                  .join(" > ")
              : "暂无"}
          </span>
        </div>

        <button
          type="button"
          className="formula-box__toggle"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
        >
          {open ? "收起数据源配置 ▴" : "查看 / 调整优先级 ▾"}
        </button>
      </div>

      {open && (
        <div className="source-panel__body">
          <div className="source-panel__controls">
            {!isStatic ? (
              <>
                <div className="source-presets" role="group" aria-label="优先级预设">
                  {sourcesPayload.profiles.map((prof) => (
                    <button
                      key={prof.id}
                      type="button"
                      disabled={busy || backfilling}
                      className={`preset-btn ${
                        sourcesPayload.profile === prof.id ? "preset-btn--active" : ""
                      }`}
                      title={prof.description}
                      onClick={() => onApplyProfile?.(prof.id)}
                    >
                      {prof.label}
                    </button>
                  ))}
                </div>
                {onBackfillFree && (
                  <button
                    type="button"
                    className="backfill-btn"
                    disabled={busy || backfilling}
                    onClick={onBackfillFree}
                  >
                    {backfilling ? "正在拉取免费 20 年历史…" : "拉取免费 20 年历史"}
                  </button>
                )}
              </>
            ) : (
              <span className="source-panel__readonly">
                静态快照只读展示当前优先级顺序与历史数据覆盖范围（同日多源按序号由高到低选取）。
              </span>
            )}
          </div>

          <div className="source-table-wrap">
            <table className="source-table">
              <thead>
                <tr>
                  <th>优先级</th>
                  <th>数据源</th>
                  <th>类型</th>
                  <th>当前标的覆盖范围（{sourcesPayload.instrument}）</th>
                  {!isStatic && <th>调整</th>}
                </tr>
              </thead>
              <tbody>
                {sourcesPayload.sources.map((src, idx) => {
                  const tierClass =
                    src.tier === "free"
                      ? "tier-pill--free"
                      : src.tier === "external_csv"
                        ? "tier-pill--csv"
                        : "tier-pill--paid";
                  return (
                    <tr
                      key={src.id}
                      className={src.active_in_db ? "source-row--active" : "source-row--idle"}
                    >
                      <td className="source-rank">#{src.priority_rank}</td>
                      <td>
                        <div className="source-name-cell">
                          <strong>{src.name}</strong>
                          <code className="source-id-code">{src.id}</code>
                        </div>
                        <div className="source-desc">{src.description}</div>
                      </td>
                      <td>
                        <span className={`tier-pill ${tierClass}`}>
                          {formatTierLabel(src.tier)}
                        </span>
                      </td>
                      <td className="source-cov">
                        <span
                          className={
                            src.active_in_db ? "source-cov--ok" : "source-cov--empty"
                          }
                        >
                          {formatCoverage(src)}
                        </span>
                      </td>
                      {!isStatic && (
                        <td className="source-actions">
                          <button
                            type="button"
                            className="move-btn"
                            disabled={busy || idx === 0}
                            title="上移优先级"
                            onClick={() => onMovePriority?.(src.id, "up")}
                          >
                            ↑
                          </button>
                          <button
                            type="button"
                            className="move-btn"
                            disabled={busy || idx === sourcesPayload.sources.length - 1}
                            title="下移优先级"
                            onClick={() => onMovePriority?.(src.id, "down")}
                          >
                            ↓
                          </button>
                        </td>
                      )}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </section>
  );
};
