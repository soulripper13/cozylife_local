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
from .switch_options import supports_light_schedule_options, supports_schedule_options


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities,
):
    """Set up CozyLife schedule time entities."""
    coordinator: CozyLifeCoordinator = hass.data[DOMAIN][config_entry.entry_id]

    entities = []
    if supports_schedule_options(coordinator):
        entities.append(CozyLifePlugScheduleTime(coordinator))
    if supports_light_schedule_options(coordinator):
        entities.append(CozyLifeLightScheduleTime(coordinator))

    if entities:
        async_add_entities(entities)


class CozyLifePlugScheduleTime(TimeEntity):
    """Default plug schedule time."""

    _attr_icon = "mdi:clock-outline"
    _entity_domain = "switch"
    _unique_suffix = "schedule_time"
    _name_suffix = "Schedule"

    def __init__(
        self,
        coordinator: CozyLifeCoordinator,
        schedule_id: str = DEFAULT_SCHEDULE_ID,
    ) -> None:
        self.coordinator = coordinator
        self._schedule_id = schedule_id
        self._attr_name = (
            f"{coordinator.device.device_model_name} {self._name_suffix}"
        )
        self._attr_unique_id = (
            f"{coordinator.device.device_id}_{self._unique_suffix}"
        )

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
            entity_domain=self._entity_domain,
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
            entity_domain=self._entity_domain,
        )
        self.async_write_ha_state()


class CozyLifeLightScheduleTime(CozyLifePlugScheduleTime):
    """Default light schedule time."""

    _entity_domain = "light"
    _unique_suffix = "light_schedule_time"
