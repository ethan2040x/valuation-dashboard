"""Historical P/E Percentile (PE 历史分位) derived indicator."""

from __future__ import annotations

from typing import Any

from app.indicators import register
from app.indicators.base import ComputeDeps, Indicator, rolling_percentile
from app.models import Display, FormulaInfo, IndicatorPoint, ThresholdBand


@register
class PEPercentile(Indicator):
    id = "pe_percentile"
    name = "PE 历史分位"
    unit = "%"
    description = (
        "当前 PE 在过去 5 年、10 年、20 年历史交易日中的分位占比"
        "（低于或等于当前值的交易日占比；不足 1 年不显示，不满窗口标注实际年数）"
    )
    formula = FormulaInfo(
        expression="历史分位 = (窗口内 PE ≤ 当前 PE 的交易日数 ÷ 窗口内有效交易日总数) × 100%",
        variables=(
            ("统计窗口", "回溯近 5 年、10 年或 20 年自然区间 [t − N年, t] 内的所有有效交易日"),
            ("低位 / 中位 / 高位", "0%–30% 低位区间（绿）、30%–70% 中位区间（橙）、70%–100% 高位区间（红）"),
        ),
        why=(
            "单一 PE 倍数很难直接判断贵或便宜，历史分位用来定位当前估值在该标的自身历史中的相对位置。"
            "例如 25% 分位代表过去窗口中仅有 25% 的交易日估值低于或等于今天、约 75% 的时间比今天更贵。"
        ),
        rules=(
            "实际可用历史不足 1 年时不显示分位，避免样本过短产生误导",
            "超过 1 年但不满目标窗口（5 年、10 年或 20 年）时照常显示，并标注“基于 X 年数据”",
            "对前瞻 PE 与滚动 PE 各自计算 5 年、10 年与 20 年三档，并在分位卡底部标明历史数据来源",
        ),
    )
    depends_on = ["pe_ttm", "pe_fwd"]
    params = {
        "base": ["pe_fwd", "pe_ttm"],
        "window_years": [5, 10, 20],
    }
    display = Display(
        kind="percentile_bar",
        chartable=False,
        card_visible=True,
        precision=1,
        suffix="%",
        order=30,
        default_params={"base": "pe_ttm", "window_years": 5},
        thresholds=(
            ThresholdBand(label="低位区间", color="#16a34a", max=30.0),
            ThresholdBand(label="中位区间", color="#d97706", max=70.0),
            ThresholdBand(label="高位区间", color="#dc2626", max=None),
        ),
    )

    def compute(
        self,
        deps: ComputeDeps,
        base: str = "pe_ttm",
        window_years: int = 5,
        **_: Any,
    ) -> list[IndicatorPoint]:
        base_series = deps.get(base, [])
        return rolling_percentile(base_series, window_years=int(window_years))
