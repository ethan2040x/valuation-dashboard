"""Trailing 12-Month P/E (滚动 PE) base indicator."""

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
class PETTM(Indicator):
    id = "pe_ttm"
    name = "滚动 PE"
    unit = "x"
    description = (
        "指数按成分股权重调和加权的过去 12 个月实际市盈率；个股按最新股价 ÷ 过去 12 个月稀释 EPS 计算"
    )
    formula = FormulaInfo(
        expression="滚动 PE = 1 ÷ Σ (wᵢ × 滚动盈利收益率ᵢ)   ，其中 滚动盈利收益率ᵢ = 过去 12 个月实际 EPSᵢ ÷ 股价ᵢ",
        variables=(
            ("wᵢ", "第 i 只成分股在当日指数持仓中的归一化权重（Σwᵢ = 1）"),
            ("实际 EPSᵢ", "过去 4 个季度已披露的实际每股收益之和（trailingEps）"),
            ("股价ᵢ", "成分股当日最新市场价格（currentPrice）"),
            ("盈利收益率 (E/P)", "市盈率 (P/E) 的倒数，表示每 1 美元股价对应的过去 12 个月实际盈利"),
        ),
        why=(
            "滚动 PE 衡量市场对已实现盈利的定价倍数。美股主流指数存在权重上限或流通市值加权差异，"
            "直接用“总市值 ÷ 总利润”会偏离指数真实构成；采用指数持仓权重对盈利收益率加权后再取倒数，"
            "既还原真实权重，又避免亏损股或极小利润导致单股 PE 畸变。"
        ),
        rules=(
            "亏损公司（实际 EPS ≤ 0）计入负盈利收益率，真实反映亏损拖累",
            "缺数据或异常脏数据（如未复权拆股导致隐含 PE < 1x）按缺失剔除并归一剩余权重",
            "缺数据成分股的权重合计超过 5% 时，当日数值标记为“不完整”",
        ),
    )
    equity_formula = FormulaInfo(
        expression="个股滚动 PE = 当日收盘价 ÷ 过去 12 个月滚动 EPS (TTM EPS)",
        variables=(
            ("当日收盘价", "最新市场价格或历史复权（仅拆股）收盘价"),
            ("TTM EPS", "实时来自 yfinance trailingEps；历史序列由 SEC EDGAR 官方 10-Q/10-K 连续 4 季稀释 EPS（含拆股对齐）计算"),
        ),
        why=(
            "单只公司的滚动 PE 直接反映市场当前愿意为过去 12 个月已实现每股盈利支付多少倍价格。"
            "结合公司自身 5 年 / 10 年历史分位，可规避不同行业绝对估值中枢差异带来的误判。"
        ),
        rules=(
            "公司处于亏损状态（TTM EPS ≤ 0）时标记为“亏损”，不展示无意义的负 PE",
            "微利盈亏平衡期导致 PE > 300x 的极端毛刺会过滤并标注，防止拉扁多年走势图",
            "支持通过 SEC EDGAR 自动回填 10–15 年历史（Yahoo 财报兜底 4–5 年）及外部 CSV 覆盖",
            "银行、保险及综合金融等金融股受杠杆、准备金与投资公允价值损益影响，PE 易失真，默认 Top 10 已剔除金融股（常规更适用 PB / ROE）",
        ),
    )
    depends_on = []
    raw_series_ids = ["pe_ttm"]
    uses_constituents = True
    uses_equity_snapshots = True
    params = {}
    display = Display(
        kind="metric_card",
        chartable=True,
        card_visible=True,
        precision=2,
        suffix="x",
        order=20,
        reference_lines=(
            ReferenceLine(kind="mean", label="区间均值"),
            ReferenceLine(kind="std_band", label="±1σ 区间", sigma=1.0),
        ),
        thresholds=(
            ThresholdBand(label="低估", color="#16a34a", max=25.0),
            ThresholdBand(label="适中", color="#d97706", max=33.0),
            ThresholdBand(label="偏高", color="#dc2626", max=None),
        ),
    )

    def compute(self, deps: ComputeDeps, **params: Any) -> list[IndicatorPoint]:
        return compute_base_pe_series(deps, series_id="pe_ttm", eps_field="eps_ttm")
