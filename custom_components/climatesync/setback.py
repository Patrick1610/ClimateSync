"""Shared validation and calculation of absolute and relative idle targets."""

from __future__ import annotations

import math
from typing import Any

from .const import IDLE_MODE_DELTA


def finite_temperature(value: Any) -> float | None:
    """Accept only finite temperature readings and bounds."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def destination_limits(attributes: Any) -> tuple[float | None, float | None]:
    """Read the selected destination's advertised target limits."""
    return (
        finite_temperature(attributes.get("min_temp")),
        finite_temperature(attributes.get("max_temp")),
    )


def idle_target(
    mode: str, value: float, current: float | None, minimum: float | None
) -> float | None:
    """Return an absolute idle target; withdraw to minimum if a delta lacks input."""
    if mode == IDLE_MODE_DELTA:
        return current - value if current is not None else minimum
    return value
