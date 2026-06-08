"""Helpers for CozyLife switch and plug option DPIDs."""

from __future__ import annotations

from .const import PLUG_TIMER_SCHEDULE
from .coordinator import CozyLifeCoordinator
from .discovery import get_model_info


def supported_dpids(coordinator: CozyLifeCoordinator) -> set[str]:
    """Return discovered and catalog-listed DPIDs for this device."""
    dpids = set(coordinator.device.dpid or [])
    model_info = get_model_info(coordinator.device.pid)
    if model_info:
        dpids.update(model_info.dpids)
    return dpids


def supports_switch_options(coordinator: CozyLifeCoordinator) -> bool:
    """Return whether the device can expose shared switch option entities."""
    return coordinator.classification.supports_switch_entities


def supports_schedule_options(coordinator: CozyLifeCoordinator) -> bool:
    """Return whether the device can expose HA-backed schedule controls."""
    return (
        supports_switch_options(coordinator)
        and PLUG_TIMER_SCHEDULE in supported_dpids(coordinator)
    )
