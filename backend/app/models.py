"""Shared data models for indicators, display configuration, and API payloads."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class ThresholdBand:
    """Color threshold band for rendering indicator values or percentile bars."""

    label: str
    color: str
    max: float | None = None  # Upper bound (inclusive); None means +infinity

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReferenceLine:
    """Reference line definition for charts."""

    kind: Literal["mean", "std_band", "fixed"]
    label: str
    value: float | None = None
    sigma: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FormulaInfo:
    """Human-readable formula and methodology explanation owned by each indicator."""

    expression: str
    variables: tuple[tuple[str, str], ...] = ()
    why: str = ""
    rules: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "expression": self.expression,
            "variables": [{"symbol": sym, "meaning": desc} for sym, desc in self.variables],
            "why": self.why,
            "rules": list(self.rules),
        }


@dataclass(frozen=True)
class Display:
    """Declarative display configuration owned by each indicator.

    The frontend renders every indicator purely from this configuration so
    adding a new indicator requires zero API or frontend changes.
    """

    kind: Literal["metric_card", "percentile_bar", "line_chart"] = "metric_card"
    chartable: bool = True
    card_visible: bool = True
    precision: int = 2
    suffix: str = ""
    reference_lines: tuple[ReferenceLine, ...] = ()
    thresholds: tuple[ThresholdBand, ...] = ()
    order: int = 100
    default_params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "chartable": self.chartable,
            "card_visible": self.card_visible,
            "precision": self.precision,
            "suffix": self.suffix,
            "reference_lines": [r.to_dict() for r in self.reference_lines],
            "thresholds": [t.to_dict() for t in self.thresholds],
            "order": self.order,
            "default_params": dict(self.default_params),
        }


@dataclass
class ConstituentQuote:
    """Single constituent snapshot row."""

    ticker: str
    weight: float  # Fraction (e.g. 0.085 for 8.5%)
    price: float | None = None
    eps_ttm: float | None = None
    eps_fwd: float | None = None
    source: str = "qqq+yfinance"


@dataclass
class EquitySnapshot:
    """Single company daily price & EPS snapshot row."""

    price: float | None = None
    eps_ttm: float | None = None
    eps_fwd: float | None = None
    market_cap: float | None = None
    source: str = "equity+yfinance"


@dataclass
class IndicatorPoint:
    """Single computed indicator value on a date."""

    date: str  # YYYY-MM-DD
    value: float | None
    source: str = "computed"
    is_incomplete: bool = False
    meta: dict[str, Any] = field(default_factory=dict)
