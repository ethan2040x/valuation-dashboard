import React, { useEffect, useState } from "react";
import type {
  CsvImportRequest,
  CsvImportResponse,
  InstrumentMeta,
} from "../types/valuation";

interface CsvImportModalProps {
  open: boolean;
  busy: boolean;
  instruments: InstrumentMeta[];
  defaultInstrument: string;
  onClose: () => void;
  onSubmit: (payload: CsvImportRequest) => Promise<CsvImportResponse>;
}

const SOURCE_OPTIONS = [
  { id: "csv_import", label: "自定义 CSV (csv_import)" },
  { id: "gurufocus_csv", label: "GuruFocus CSV" },
  { id: "macromicro_csv", label: "MacroMicro CSV" },
  { id: "bloomberg_csv", label: "Bloomberg CSV" },
  { id: "wind_csv", label: "Wind / Choice CSV" },
];

export const CsvImportModal: React.FC<CsvImportModalProps> = ({
  open,
  busy,
  instruments,
  defaultInstrument,
  onClose,
  onSubmit,
}) => {
  const [target, setTarget] = useState(defaultInstrument || "AUTO");
  const [source, setSource] = useState("csv_import");
  const [seriesId, setSeriesId] = useState<string>("");
  const [csvText, setCsvText] = useState("");
  const [fileName, setFileName] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<CsvImportResponse | null>(null);

  useEffect(() => {
    if (!open) return;
    setTarget(defaultInstrument || "AUTO");
    setError(null);
    setResult(null);
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
  }, [open, busy, defaultInstrument, onClose]);

  if (!open) return null;

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setFileName(file.name);
    const reader = new FileReader();
    reader.onload = () => {
      setCsvText(typeof reader.result === "string" ? reader.result : "");
    };
    reader.readAsText(file, "utf-8");
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!csvText.trim()) {
      setError("请选择 CSV 文件或粘贴 CSV 内容");
      return;
    }
    setError(null);
    try {
      const res = await onSubmit({
        csv_text: csvText,
        instrument: target === "AUTO" ? null : target,
        source,
        series_id: seriesId || null,
      });
      setResult(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <div
      className="modal-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label="导入历史 CSV"
      onClick={() => {
        if (!busy) onClose();
      }}
    >
      <div
        className="modal-card modal-card--wide"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-header">
          <h3 className="modal-title">导入历史估值 CSV（单股 / 多公司批量）</h3>
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
          <div className="form-row-3">
            <label className="form-field">
              <span className="form-label">导入目标</span>
              <select
                className="form-input"
                value={target}
                onChange={(e) => setTarget(e.target.value)}
                disabled={busy}
              >
                <option value="AUTO">
                  自动识别 CSV 中的 ticker 列（多公司批量）
                </option>
                {instruments.map((inst) => (
                  <option key={inst.symbol} value={inst.symbol}>
                    {inst.symbol} · {inst.name}
                  </option>
                ))}
              </select>
            </label>

            <label className="form-field">
              <span className="form-label">来源标签</span>
              <select
                className="form-input"
                value={source}
                onChange={(e) => setSource(e.target.value)}
                disabled={busy}
              >
                {SOURCE_OPTIONS.map((opt) => (
                  <option key={opt.id} value={opt.id}>
                    {opt.label}
                  </option>
                ))}
              </select>
            </label>

            <label className="form-field">
              <span className="form-label">两列表指标映射</span>
              <select
                className="form-input"
                value={seriesId}
                onChange={(e) => setSeriesId(e.target.value)}
                disabled={busy}
              >
                <option value="">自动识别列头（pe_ttm / pe_fwd）</option>
                <option value="pe_ttm">强制映射到 滚动 PE (pe_ttm)</option>
                <option value="pe_fwd">强制映射到 前瞻 PE (pe_fwd)</option>
              </select>
            </label>
          </div>

          <div className="form-field">
            <span className="form-label">选择本地 CSV 文件或直接粘贴内容</span>
            <div className="csv-file-row">
              <input
                type="file"
                accept=".csv,text/csv"
                onChange={handleFileChange}
                disabled={busy}
              />
              {fileName && <span className="csv-file-name">{fileName}</span>}
            </div>
            <textarea
              className="form-textarea"
              rows={7}
              placeholder={
                "单公司示例：\ndate,pe_ttm,pe_fwd\n2024-01-02,32.5,26.1\n\n多公司批量示例（目标选“自动识别 ticker 列”）：\nticker,date,pe_ttm,pe_fwd\nNVDA,2024-01-02,55.2,35.8\nAAPL,2024-01-02,28.4,26.1"
              }
              value={csvText}
              onChange={(e) => setCsvText(e.target.value)}
              disabled={busy}
            />
          </div>

          {error && <div className="modal-error">{error}</div>}

          {result && (
            <div className="modal-success">
              <strong>
                导入成功（共 {result.total_rows} 条记录，已重新计算全部衍生分位）：
              </strong>
              <ul className="csv-result-list">
                {Object.entries(result.imported_by_symbol).map(([sym, map]) => (
                  <li key={sym}>
                    <code>{sym}</code>：
                    {Object.entries(map)
                      .map(([sid, cnt]) => `${sid} ${cnt} 条`)
                      .join("，")}
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div className="modal-footer">
            <button
              type="button"
              className="wl-btn"
              disabled={busy}
              onClick={onClose}
            >
              {result ? "完成" : "取消"}
            </button>
            <button
              type="submit"
              className="wl-btn wl-btn--primary"
              disabled={busy}
            >
              {busy ? "正在导入并重算…" : "开始导入"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};
