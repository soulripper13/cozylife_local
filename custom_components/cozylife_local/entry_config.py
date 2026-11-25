"""Resolve device options and defaults from current and legacy entries."""

from collections.abc import Mapping
from typing import Any

from .const import DEFAULT_MAX_KELVIN, DEFAULT_MIN_KELVIN, LIGHT_KELVIN_RANGES

MIN_KELVIN_LIMIT = 1000
MAX_KELVIN_LIMIT = 10000


def default_kelvin_range(pid: str | None) -> tuple[int, int]:
    """Return the model's color temperature endpoints."""
    return LIGHT_KELVIN_RANGES.get(
        pid or "", (DEFAULT_MIN_KELVIN, DEFAULT_MAX_KELVIN)
    )


def valid_kelvin_range(minimum: Any, maximum: Any) -> bool:
    """Check that a range is usable for normalization and display."""
    return (
        type(minimum) is int
        and type(maximum) is int
        and MIN_KELVIN_LIMIT <= minimum < maximum <= MAX_KELVIN_LIMIT
    )


def entry_kelvin_range(
    pid: str | None, data: Mapping[str, Any], options: Mapping[str, Any]
) -> tuple[int, int]:
    """Prefer options, then legacy settings, then the model defaults."""
    default_min, default_max = default_kelvin_range(pid)
    legacy_min = data.get("min_kelvin", DEFAULT_MIN_KELVIN)
    legacy_max = data.get("max_kelvin", DEFAULT_MAX_KELVIN)
    if legacy_min == DEFAULT_MIN_KELVIN:
        legacy_min = default_min
    if legacy_max == DEFAULT_MAX_KELVIN:
        legacy_max = default_max

    minimum = options.get("min_kelvin", legacy_min)
    maximum = options.get("max_kelvin", legacy_max)
    if valid_kelvin_range(minimum, maximum):
        return minimum, maximum
    if valid_kelvin_range(legacy_min, legacy_max):
        return legacy_min, legacy_max
    return default_min, default_max
