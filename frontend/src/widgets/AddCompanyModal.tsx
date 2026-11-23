import React, { useEffect, useState } from "react";
import type { AddCompaniesRequest } from "../types/valuation";

interface AddCompanyModalProps {
  open: boolean;
  busy: boolean;
  onClose: () => void;
  onSubmit: (payload: AddCompaniesRequest) => Promise<void>;
}

export const AddCompanyModal: React.FC<AddCompanyModalProps> = ({
  open,
  busy,
  onClose,
  onSubmit,
}) => {
  const [tickersInput, setTickersInput] = useState("");
  const [name, setName] = useState("");
  const [sector, setSector] = useState("");
  const [groupName, setGroupName] = useState("自选股");
  const [autoBackfill, setAutoBackfill] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setError(null);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !busy) onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => {
      document.body.style.overflow = prevOverflow;
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [open, busy, onClose]);

  if (!open) return null;

  const tokens = tickersInput
    .split(/[\s,;]+/)
    .map((s) => s.trim().toUpperCase())
    .filter(Boolean);
  const isSingle = tokens.length <= 1;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (tokens.length === 0) {
      setError("请输入至少一个美股代码（例如 AMD 或 ORCL, CRM, NFLX）");
      return;
    }
    setError(null);
    try {
      await onSubmit({
        symbols: tokens,
        name: isSingle && name.trim() ? name.trim() : undefined,
        sector: sector.trim() || undefined,
        group_name: groupName.trim() || "自选股",
        backfill: autoBackfill,
        refresh: autoBackfill,
      });
      setTickersInput("");
      setName("");
      setSector("");
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <div
      className="modal-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label="添加美股公司"
      onClick={() => {
        if (!busy) onClose();
      }}
    >
      <div className="modal-card" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h3 className="modal-title">添加美股自选公司</h3>
          <button
            type="button"
            className="modal-close"
            disabled={busy}
            onClick={onClose}
            aria-label="关闭"
          >
            ×
          </button>
        </div>

        <form onSubmit={handleSubmit} className="modal-body">
          <label className="form-field">
            <span className="form-label">
              股票代码（支持逗号或空格分隔批量添加）
            </span>
            <input
              type="text"
              className="form-input"
              placeholder="例如：AMD 或 ORCL, CRM, NFLX"
              value={tickersInput}
              onChange={(e) => setTickersInput(e.target.value)}
              disabled={busy}
              autoFocus
            />
          </label>

          {isSingle && (
            <label className="form-field">
              <span className="form-label">中文简称（可选，留空自动获取）</span>
              <input
                type="text"
                className="form-input"
                placeholder="例如：超威半导体"
                value={name}
                onChange={(e) => setName(e.target.value)}
                disabled={busy}
              />
            </label>
          )}

          <div className="form-row-2">
            <label className="form-field">
              <span className="form-label">行业标签（可选）</span>
              <input
                type="text"
                className="form-input"
                placeholder="例如：半导体"
                value={sector}
                onChange={(e) => setSector(e.target.value)}
                disabled={busy}
              />
            </label>
            <label className="form-field">
              <span className="form-label">分组</span>
              <input
                type="text"
                className="form-input"
                value={groupName}
                onChange={(e) => setGroupName(e.target.value)}
                disabled={busy}
              />
            </label>
          </div>

          <label className="form-checkbox">
            <input
              type="checkbox"
              checked={autoBackfill}
              onChange={(e) => setAutoBackfill(e.target.checked)}
              disabled={busy}
            />
            <span>
              立即免费回填 15 年 SEC EDGAR / Yahoo 历史滚动 PE 并拉取最新行情
            </span>
          </label>

          {error && <div className="modal-error">{error}</div>}

          <p className="form-hint">
            提示：本看板以 PE 为核心估值指标，银行、保险、综合金融等金融股 PE
            易失真，常规更适用 PB / ROE，添加后请谨慎参考。
          </p>

          <div className="modal-footer">
            <button
              type="button"
              className="wl-btn"
              disabled={busy}
              onClick={onClose}
            >
              取消
            </button>
            <button
              type="submit"
              className="wl-btn wl-btn--primary"
              disabled={busy}
            >
              {busy
                ? "正在提交…"
                : tokens.length > 1
                  ? `批量添加 ${tokens.length} 家公司`
                  : "确认添加"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};
