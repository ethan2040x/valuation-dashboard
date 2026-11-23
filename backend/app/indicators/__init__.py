"""Indicator registry and automatic module discovery.

Adding a new indicator only requires dropping a `.py` file into
`backend/app/indicators/` with a class decorated by `@register`.
"""

from __future__ import annotations

import importlib
import pkgutil
from typing import TypeVar

from app.indicators.base import ComputeDeps, Indicator

T = TypeVar("T", bound=type[Indicator])

REGISTRY: dict[str, Indicator] = {}
_DISCOVERED = False


def register(cls: T) -> T:
    """Register an Indicator subclass into the global indicator registry."""
    instance = cls()
    if not instance.id:
        raise ValueError(f"Indicator class {cls.__name__} must define a non-empty `id`")
    REGISTRY[instance.id] = instance
    return cls


def unregister(indicator_id: str) -> None:
    """Remove an indicator from the registry (useful in tests)."""
    REGISTRY.pop(indicator_id, None)


def discover_indicators(force: bool = False) -> dict[str, Indicator]:
    """Auto-import all indicator modules in `app.indicators`."""
    global _DISCOVERED
    if _DISCOVERED and not force:
        return REGISTRY

    for module_info in pkgutil.iter_modules(__path__):
        if module_info.name.startswith("_") or module_info.name == "base":
            continue
        full_name = f"{__name__}.{module_info.name}"
        if force and full_name in importlib.sys.modules:
            importlib.reload(importlib.sys.modules[full_name])
        else:
            importlib.import_module(full_name)

    _DISCOVERED = True
    return REGISTRY


def get_indicator(indicator_id: str) -> Indicator | None:
    discover_indicators()
    return REGISTRY.get(indicator_id)


def list_indicators() -> list[Indicator]:
    discover_indicators()
    return sorted(REGISTRY.values(), key=lambda ind: (ind.display.order, ind.id))


__all__ = [
    "ComputeDeps",
    "Indicator",
    "REGISTRY",
    "discover_indicators",
    "get_indicator",
    "list_indicators",
    "register",
    "unregister",
]
