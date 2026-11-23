"""Trailing 12-Month P/E (滚动 PE) base indicator."""

from __future__ import annotations

from typing import Any

from app.indicators import register
from app.indicators.base import (
    ComputeDeps,
    Indicator,
    compute_weighted_pe,
    merge_with_raw_series,
)
from app.models import Display, FormulaInfo, IndicatorPoint, ReferenceLine, ThresholdBand


@register
class PETTM(Indicator):
    id = "pe_ttm"
    name = "滚动 PE"
    unit = "x"
    description = (
        "按目标指数 ETF（QQQ / SPY）成分股权重加权的过去 12 个月实际市盈率"
        "（1 ÷ Σ 权重 × 个股滚动盈利收益率，亏损股计入）"
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
    depends_on = []
    raw_series_ids = ["pe_ttm"]
    uses_constituents = True
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
        computed_points: list[IndicatorPoint] = []
        for date_str, quotes in sorted(deps.constituents.items()):
            value, is_incomplete, meta = compute_weighted_pe(quotes, eps_field="eps_ttm")
            if value is not None:
                computed_points.append(
                    IndicatorPoint(
                        date=date_str,
                        value=value,
                        source="computed",
                        is_incomplete=is_incomplete,
                        meta=meta,
                    )
                )

        return merge_with_raw_series(
            computed_points=computed_points,
            raw_rows=deps.raw_series("pe_ttm"),
            source_priority=deps.source_priority,
        )
