import logging
from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, PLUG_COUNTDOWN
from .coordinator import CozyLifeCoordinator
from .discovery import get_model_info

_LOGGER = logging.getLogger(__name__)

MAX_COUNTDOWN_SECONDS = 24 * 60 * 60


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities,
):
    """Set up CozyLife number platform."""
    coordinator: CozyLifeCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    dpids = set(coordinator.device.dpid or [])
    model_info = get_model_info(coordinator.device.pid)
    if model_info:
        dpids.update(model_info.dpids)

    if (
        PLUG_COUNTDOWN not in dpids
        or not coordinator.classification.supports_plug_metering
    ):
        _LOGGER.debug(
            "Device %s has no supported number DPIDs, skipping number setup.",
            coordinator.device.ip_address,
        )
        return

    async_add_entities([CozyLifeCountdownNumber(coordinator)])


class CozyLifeCountdownNumber(CoordinatorEntity[CozyLifeCoordinator], NumberEntity):
    """Countdown/timer control for CozyLife metered smart plugs."""

    _attr_icon = "mdi:timer-outline"
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 0
    _attr_native_max_value = MAX_COUNTDOWN_SECONDS
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS

    def __init__(self, coordinator: CozyLifeCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_name = f"{coordinator.device.device_model_name} Countdown"
        self._attr_unique_id = f"{coordinator.device.device_id}_countdown"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.device.device_id)},
            name=self.coordinator.device.device_model_name,
            manufacturer="CozyLife",
            model=self.coordinator.device.pid,
        )

    @property
    def native_value(self) -> int | None:
        raw = self.coordinator.data.get(PLUG_COUNTDOWN)
        if raw is None:
            return None

        try:
            value = int(raw)
        except (TypeError, ValueError):
            return None

        return max(0, min(MAX_COUNTDOWN_SECONDS, value))

    async def async_set_native_value(self, value: float) -> None:
        countdown = max(0, min(MAX_COUNTDOWN_SECONDS, int(value)))
        payload = {PLUG_COUNTDOWN: countdown}

        if await self.coordinator.device.async_set_state(payload):
            self.coordinator.data[PLUG_COUNTDOWN] = countdown
            self.async_write_ha_state()
        else:
            _LOGGER.warning(
                "Failed to set countdown for CozyLife device %s to %s seconds",
                self.coordinator.device.ip_address,
                countdown,
            )

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()
