"""Time entities for CozyLife Local schedules."""

from __future__ import annotations

from datetime import datetime, time as dt_time

from homeassistant.components.time import TimeEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN
from .coordinator import CozyLifeCoordinator
from .schedule import DEFAULT_SCHEDULE_ID, SCHEDULE_MANAGER, CozyLifeScheduleManager
from .switch_options import supports_schedule_options


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities,
):
    """Set up CozyLife schedule time entities."""
    coordinator: CozyLifeCoordinator = hass.data[DOMAIN][config_entry.entry_id]

    if not supports_schedule_options(coordinator):
        return

    async_add_entities([CozyLifePlugScheduleTime(coordinator)])


class CozyLifePlugScheduleTime(TimeEntity):
    """Default plug schedule time."""

    _attr_icon = "mdi:clock-outline"

    def __init__(
        self,
        coordinator: CozyLifeCoordinator,
        schedule_id: str = DEFAULT_SCHEDULE_ID,
    ) -> None:
        self.coordinator = coordinator
        self._schedule_id = schedule_id
        self._attr_name = f"{coordinator.device.device_model_name} Schedule"
        self._attr_unique_id = f"{coordinator.device.device_id}_schedule_time"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.device.device_id)},
            name=self.coordinator.device.device_model_name,
            manufacturer="CozyLife",
            model=self.coordinator.device.pid,
        )

    @property
    def _manager(self) -> CozyLifeScheduleManager:
        return self.coordinator.hass.data[DOMAIN][SCHEDULE_MANAGER]

    @property
    def native_value(self) -> dt_time | None:
        schedule = self._manager.schedule_for_coordinator(
            self.coordinator,
            self._schedule_id,
        )
        value = str(schedule.get("time", "23:00:00"))
        for fmt in ("%H:%M:%S", "%H:%M"):
            try:
                return datetime.strptime(value, fmt).time()
            except ValueError:
                continue
        return None

    async def async_set_value(self, value: dt_time) -> None:
        await self._manager.async_update_coordinator_schedule(
            self.coordinator,
            self._schedule_id,
            schedule_time=value,
        )
        self.async_write_ha_state()
