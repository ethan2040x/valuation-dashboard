"""Forward P/E (前瞻 PE) base indicator."""

from __future__ import annotations

from typing import Any

from app.indicators import register
from app.indicators.base import (
    ComputeDeps,
    Indicator,
    compute_base_pe_series,
)
from app.models import Display, FormulaInfo, IndicatorPoint, ReferenceLine, ThresholdBand


@register
class PEFwd(Indicator):
    id = "pe_fwd"
    name = "前瞻 PE"
    unit = "x"
    description = (
        "指数按成分股权重调和加权的未来 12 个月一致预期市盈率；个股按最新股价 ÷ 未来 12 个月一致预期 EPS 计算"
    )
    formula = FormulaInfo(
        expression="前瞻 PE = 1 ÷ Σ (wᵢ × 预期盈利收益率ᵢ)   ，其中 预期盈利收益率ᵢ = 未来 12 个月一致预期 EPSᵢ ÷ 股价ᵢ",
        variables=(
            ("wᵢ", "第 i 只成分股在当日指数持仓中的归一化权重（Σwᵢ = 1）"),
            ("一致预期 EPSᵢ", "分析师对个股未来 12 个月每股收益的一致预期（forwardEps）"),
            ("股价ᵢ", "成分股当日最新市场价格（currentPrice）"),
            ("盈利收益率 (E/P)", "市盈率 (P/E) 的倒数，表示每 1 美元股价对应的预期盈利"),
        ),
        why=(
            "为什么不直接对个股 PE 加权平均？亏损或微利公司的 PE 为负或趋近无穷大，"
            "直接平均会严重失真；转为盈利收益率（E/P）按权重相加后再取倒数（调和加权平均），"
            "既可自然计入亏损股，又严格匹配指数的真实持仓权重结构。"
        ),
        rules=(
            "亏损公司（预期 EPS ≤ 0）照常计入，盈利收益率为负或 0，拉高整体指数 PE",
            "缺少股价或预期 EPS 的成分股剔除，剩余有效成分股权重按比例归一",
            "缺数据成分股的权重合计超过 5% 时，当日数值标记为“不完整”",
        ),
    )
    equity_formula = FormulaInfo(
        expression="个股前瞻 PE = 当日股价 ÷ 未来 12 个月一致预期 EPS (Forward EPS)",
        variables=(
            ("当日股价", "最新市场价格"),
            ("Forward EPS", "分析师对公司未来 12 个月每股收益的一致预期（yfinance forwardEps）"),
        ),
        why=(
            "资本市场交易的是未来预期。对于高成长或业绩反转公司，前瞻 PE 比滚动 PE 更能及时反映当前定价是否透支未来盈利。"
        ),
        rules=(
            "预期亏损（Forward EPS ≤ 0）时标记为“亏损”",
            "免费源普遍缺乏历史一致预期 EPS，系统每日自动积累前瞻 PE，也可通过 CSV 导入补充历史分位",
        ),
    )
    depends_on = []
    raw_series_ids = ["pe_fwd"]
    uses_constituents = True
    uses_equity_snapshots = True
    params = {}
    display = Display(
        kind="metric_card",
        chartable=True,
        card_visible=True,
        precision=2,
        suffix="x",
        order=10,
        reference_lines=(
            ReferenceLine(kind="mean", label="区间均值"),
            ReferenceLine(kind="std_band", label="±1σ 区间", sigma=1.0),
        ),
        thresholds=(
            ThresholdBand(label="低估", color="#16a34a", max=22.0),
            ThresholdBand(label="适中", color="#d97706", max=28.0),
            ThresholdBand(label="偏高", color="#dc2626", max=None),
        ),
    )

    def compute(self, deps: ComputeDeps, **params: Any) -> list[IndicatorPoint]:
        return compute_base_pe_series(deps, series_id="pe_fwd", eps_field="eps_fwd")
