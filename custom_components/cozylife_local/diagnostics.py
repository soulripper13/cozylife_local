"""Diagnostics for CozyLife Local."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from .const import DOMAIN

_TO_REDACT = {"ip_address", "device_id"}


def _redact_entry(data: dict[str, Any]) -> dict[str, Any]:
    """Keep diagnostics useful without exposing local identity data."""
    return {
        key: "**REDACTED**" if key in _TO_REDACT else value
        for key, value in data.items()
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict[str, Any]:
    """Return redacted configuration plus the current local transport state."""
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    device = getattr(coordinator, "device", None)
    return {
        "entry": _redact_entry(dict(entry.data)),
        "transport": {
            "connected_meaning": "Local socket is open; this does not confirm device responsiveness",
            "last_frame_received_at": getattr(device, "last_frame_received_at", None),
            "connected": bool(
                device is not None
                and getattr(device, "is_connected", False)
            ),
            "last_error": getattr(device, "last_transport_error", None),
        },
    }
