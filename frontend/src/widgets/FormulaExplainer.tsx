import React, { useState } from "react";
import type { FormulaInfo } from "../types/valuation";

interface FormulaExplainerProps {
  formula: FormulaInfo | null | undefined;
  defaultOpen?: boolean;
}

export const FormulaExplainer: React.FC<FormulaExplainerProps> = ({
  formula,
  defaultOpen = false,
}) => {
  const [open, setOpen] = useState(defaultOpen);

  if (!formula) {
    return null;
  }

  return (
    <div className="formula-box">
      <div className="formula-box__bar">
        <div className="formula-box__expr-wrap">
          <span className="formula-box__tag">计算公式</span>
          <code className="formula-box__expr">{formula.expression}</code>
        </div>
        <button
          type="button"
          className="formula-box__toggle"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
        >
          {open ? "收起解释 ▴" : "展开公式解释 ▾"}
        </button>
      </div>

      {open && (
        <div className="formula-box__body">
          {formula.variables.length > 0 && (
            <div className="formula-vars">
              {formula.variables.map((v) => (
                <div key={v.symbol} className="formula-var">
                  <span className="formula-var__sym">{v.symbol}</span>
                  <span className="formula-var__desc">{v.meaning}</span>
                </div>
              ))}
            </div>
          )}

          {formula.why && (
            <div className="formula-why">
              <span className="formula-section-label">为什么这样算：</span>
              <span>{formula.why}</span>
            </div>
          )}

          {formula.rules.length > 0 && (
            <ul className="formula-rules">
              {formula.rules.map((rule, idx) => (
                <li key={idx}>{rule}</li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
};
