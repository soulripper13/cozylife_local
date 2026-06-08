"""Schedule services for CozyLife Local plug timers."""

from __future__ import annotations

import logging
from datetime import datetime, time as dt_time, timedelta
from typing import Any

import voluptuous as vol

from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN, PLUG_TIMER_SCHEDULE
from .coordinator import CozyLifeCoordinator

_LOGGER = logging.getLogger(__name__)

SERVICE_SET_PLUG_SCHEDULE = "set_plug_schedule"
SERVICE_CLEAR_PLUG_SCHEDULE = "clear_plug_schedule"

SCHEDULE_MANAGER = "schedule_manager"
STORE_VERSION = 1
STORE_KEY = f"{DOMAIN}_plug_schedules"

CONF_ACTION = "action"
CONF_CLEAR_DEVICE = "clear_device"
CONF_ENABLED = "enabled"
CONF_REPEAT = "repeat"
CONF_SCHEDULE_ID = "schedule_id"
CONF_SYNC_TO_DEVICE = "sync_to_device"
CONF_TIME = "time"
CONF_TARGET = "target"

ACTION_TURN_ON = "turn_on"
ACTION_TURN_OFF = "turn_off"
DEFAULT_SCHEDULE_ID = "default"
DEFAULT_SCHEDULE_TIME = "23:00:00"

EMPTY_TIMER_SCHEDULE = "0" * 50
TIMER_PREFIX_BY_ACTION = {
    ACTION_TURN_OFF: "10",
}
WEEKDAYS = {
    "mon": 0,
    "tue": 1,
    "wed": 2,
    "thu": 3,
    "fri": 4,
    "sat": 5,
    "sun": 6,
}


SET_PLUG_SCHEDULE_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_ENTITY_ID): cv.entity_ids,
        vol.Optional(CONF_TARGET): vol.Schema(
            {vol.Optional(ATTR_ENTITY_ID): cv.entity_ids},
            extra=vol.ALLOW_EXTRA,
        ),
        vol.Optional(CONF_SCHEDULE_ID, default=DEFAULT_SCHEDULE_ID): cv.string,
        vol.Required(CONF_TIME): cv.string,
        vol.Required(CONF_ACTION): vol.In([ACTION_TURN_ON, ACTION_TURN_OFF]),
        vol.Optional(CONF_REPEAT, default=[]): vol.All(
            cv.ensure_list,
            [vol.In(list(WEEKDAYS))],
        ),
        vol.Optional(CONF_ENABLED, default=True): cv.boolean,
        vol.Optional(CONF_SYNC_TO_DEVICE, default=True): cv.boolean,
    }
)

CLEAR_PLUG_SCHEDULE_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_ENTITY_ID): cv.entity_ids,
        vol.Optional(CONF_TARGET): vol.Schema(
            {vol.Optional(ATTR_ENTITY_ID): cv.entity_ids},
            extra=vol.ALLOW_EXTRA,
        ),
        vol.Optional(CONF_SCHEDULE_ID): cv.string,
        vol.Optional(CONF_CLEAR_DEVICE, default=True): cv.boolean,
    }
)


def _entity_ids_from_call(call: ServiceCall) -> list[str]:
    """Return entity IDs from direct data or Home Assistant target payloads."""
    entity_ids = list(call.data.get(ATTR_ENTITY_ID, []))
    target = call.data.get(CONF_TARGET)
    if isinstance(target, dict):
        entity_ids.extend(target.get(ATTR_ENTITY_ID, []))

    if not entity_ids:
        raise HomeAssistantError("A CozyLife plug switch entity is required")

    return list(dict.fromkeys(entity_ids))


def _parse_time(value: str) -> dt_time:
    """Parse a Home Assistant time selector value."""
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(value, fmt).time()
        except ValueError:
            continue

    raise HomeAssistantError(
        f"Invalid schedule time {value!r}; expected HH:MM or HH:MM:SS"
    )


def _next_local_datetime(schedule_time: dt_time) -> datetime:
    """Return the next local datetime for a one-shot schedule."""
    now = dt_util.now()
    scheduled = datetime.combine(now.date(), schedule_time, tzinfo=now.tzinfo)
    if scheduled <= now:
        scheduled += timedelta(days=1)
    return scheduled


def _encode_device_timer_schedule(schedule_time: dt_time, action: str) -> str:
    """Encode a one-shot app timer payload for DPID 3."""
    if action not in TIMER_PREFIX_BY_ACTION:
        raise HomeAssistantError(
            f"Device schedule payload for action {action!r} has not been decoded"
        )

    when = _next_local_datetime(schedule_time)
    timestamp = int(when.timestamp())
    return f"{TIMER_PREFIX_BY_ACTION[action]}{timestamp:08X}{'0' * 40}"


class CozyLifeScheduleManager:
    """Manage Home Assistant side recurring plug schedules."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._store = Store(hass, STORE_VERSION, STORE_KEY)
        self._schedules: dict[str, dict[str, Any]] = {}
        self._last_fired: dict[str, str] = {}
        self._unsub_time = None

    async def async_setup(self) -> None:
        """Load schedules, start the timer, and register services."""
        data = await self._store.async_load()
        if isinstance(data, dict):
            schedules = data.get("schedules", {})
            if isinstance(schedules, dict):
                self._schedules = schedules

        self._unsub_time = async_track_time_change(
            self.hass,
            self._async_time_changed,
            second=0,
        )

        if not self.hass.services.has_service(DOMAIN, SERVICE_SET_PLUG_SCHEDULE):
            self.hass.services.async_register(
                DOMAIN,
                SERVICE_SET_PLUG_SCHEDULE,
                self._async_handle_set_schedule,
                schema=SET_PLUG_SCHEDULE_SCHEMA,
            )
        if not self.hass.services.has_service(DOMAIN, SERVICE_CLEAR_PLUG_SCHEDULE):
            self.hass.services.async_register(
                DOMAIN,
                SERVICE_CLEAR_PLUG_SCHEDULE,
                self._async_handle_clear_schedule,
                schema=CLEAR_PLUG_SCHEDULE_SCHEMA,
            )

    async def async_unload(self) -> None:
        """Stop the schedule timer."""
        if self._unsub_time:
            self._unsub_time()
            self._unsub_time = None
        if self.hass.services.has_service(DOMAIN, SERVICE_SET_PLUG_SCHEDULE):
            self.hass.services.async_remove(DOMAIN, SERVICE_SET_PLUG_SCHEDULE)
        if self.hass.services.has_service(DOMAIN, SERVICE_CLEAR_PLUG_SCHEDULE):
            self.hass.services.async_remove(DOMAIN, SERVICE_CLEAR_PLUG_SCHEDULE)

    async def _async_save(self) -> None:
        await self._store.async_save({"schedules": self._schedules})

    def _switch_entity_id_for_coordinator(
        self,
        coordinator: CozyLifeCoordinator,
    ) -> str:
        """Return the primary plug switch entity for a coordinator."""
        registry = er.async_get(self.hass)
        entity_id = registry.async_get_entity_id(
            "switch",
            DOMAIN,
            f"{coordinator.device.device_id}_1",
        )
        if entity_id is None:
            raise HomeAssistantError(
                f"Could not find plug switch entity for {coordinator.device.device_id}"
            )
        return entity_id

    def _schedule_key(
        self,
        coordinator: CozyLifeCoordinator,
        schedule_id: str = DEFAULT_SCHEDULE_ID,
    ) -> str:
        return f"{coordinator.device.device_id}:{schedule_id}"

    def schedule_for_coordinator(
        self,
        coordinator: CozyLifeCoordinator,
        schedule_id: str = DEFAULT_SCHEDULE_ID,
    ) -> dict[str, Any]:
        """Return a stored schedule or a default disabled schedule."""
        entity_id = self._switch_entity_id_for_coordinator(coordinator)
        key = self._schedule_key(coordinator, schedule_id)
        schedule = dict(
            self._schedules.get(
                key,
                {
                    "entity_id": entity_id,
                    "schedule_id": schedule_id,
                    "time": DEFAULT_SCHEDULE_TIME,
                    "action": ACTION_TURN_OFF,
                    "repeat": [],
                    "enabled": False,
                },
            )
        )
        schedule["entity_id"] = entity_id
        schedule["schedule_id"] = schedule_id
        return schedule

    async def async_update_coordinator_schedule(
        self,
        coordinator: CozyLifeCoordinator,
        schedule_id: str = DEFAULT_SCHEDULE_ID,
        *,
        schedule_time: dt_time | None = None,
        action: str | None = None,
        repeat: list[str] | None = None,
        enabled: bool | None = None,
        sync_to_device: bool = True,
    ) -> dict[str, Any]:
        """Update the default UI schedule for a coordinator."""
        entity_id = self._switch_entity_id_for_coordinator(coordinator)
        schedule = self.schedule_for_coordinator(coordinator, schedule_id)

        if schedule_time is not None:
            schedule["time"] = schedule_time.strftime("%H:%M:%S")
        if action is not None:
            schedule["action"] = action
        if repeat is not None:
            schedule["repeat"] = repeat
        if enabled is not None:
            schedule["enabled"] = enabled

        parsed_time = _parse_time(schedule["time"])
        repeat_days = list(schedule.get("repeat", []))
        if not repeat_days:
            schedule["run_at"] = _next_local_datetime(parsed_time).isoformat()
        else:
            schedule.pop("run_at", None)

        schedule["entity_id"] = entity_id
        key = self._schedule_key(coordinator, schedule_id)
        self._schedules[key] = schedule

        if (
            schedule.get("enabled", True)
            and sync_to_device
            and not repeat_days
            and schedule.get("action") == ACTION_TURN_OFF
            and coordinator.classification.supports_plug_metering
        ):
            await self._async_write_device_schedule(
                entity_id,
                parsed_time,
                schedule["action"],
            )

        await self._async_save()
        return schedule

    async def async_clear_coordinator_schedule(
        self,
        coordinator: CozyLifeCoordinator,
        schedule_id: str = DEFAULT_SCHEDULE_ID,
        *,
        clear_device: bool = True,
    ) -> None:
        """Clear a coordinator schedule."""
        entity_id = self._switch_entity_id_for_coordinator(coordinator)
        self._schedules.pop(self._schedule_key(coordinator, schedule_id), None)
        if clear_device:
            await self._async_clear_device_schedule(entity_id)
        await self._async_save()

    def _coordinator_from_entity_id(
        self,
        entity_id: str,
    ) -> CozyLifeCoordinator:
        if not entity_id.startswith("switch."):
            raise HomeAssistantError(
                f"{entity_id} is not a switch entity; use the plug switch entity"
            )

        registry = er.async_get(self.hass)
        entry = registry.async_get(entity_id)
        if entry is None or entry.config_entry_id is None:
            raise HomeAssistantError(
                f"{entity_id} is not a registered CozyLife entity"
            )

        coordinator = self.hass.data.get(DOMAIN, {}).get(entry.config_entry_id)
        if coordinator is None:
            raise HomeAssistantError(
                f"Could not find CozyLife coordinator for {entity_id}"
            )
        if not coordinator.classification.supports_switch_entities:
            raise HomeAssistantError(
                f"{entity_id} does not belong to a supported CozyLife switch"
            )

        return coordinator

    async def _async_write_device_schedule(
        self,
        entity_id: str,
        schedule_time: dt_time,
        action: str,
    ) -> None:
        coordinator = self._coordinator_from_entity_id(entity_id)
        payload = _encode_device_timer_schedule(schedule_time, action)
        if not await coordinator.device.async_set_state({PLUG_TIMER_SCHEDULE: payload}):
            raise HomeAssistantError(
                f"Failed to set device schedule for {entity_id}"
            )

        coordinator.data[PLUG_TIMER_SCHEDULE] = payload
        coordinator.async_set_updated_data(coordinator.data)

    async def _async_clear_device_schedule(self, entity_id: str) -> None:
        coordinator = self._coordinator_from_entity_id(entity_id)
        if not await coordinator.device.async_set_state(
            {PLUG_TIMER_SCHEDULE: EMPTY_TIMER_SCHEDULE}
        ):
            raise HomeAssistantError(
                f"Failed to clear device schedule for {entity_id}"
            )

        coordinator.data[PLUG_TIMER_SCHEDULE] = EMPTY_TIMER_SCHEDULE
        coordinator.async_set_updated_data(coordinator.data)

    async def _async_handle_set_schedule(self, call: ServiceCall) -> None:
        entity_ids = _entity_ids_from_call(call)
        schedule_id = call.data[CONF_SCHEDULE_ID]
        schedule_time = _parse_time(call.data[CONF_TIME])
        action = call.data[CONF_ACTION]
        repeat = list(call.data[CONF_REPEAT])
        enabled = call.data[CONF_ENABLED]
        sync_to_device = call.data[CONF_SYNC_TO_DEVICE]

        for entity_id in entity_ids:
            coordinator = self._coordinator_from_entity_id(entity_id)
            key = self._schedule_key(coordinator, schedule_id)
            scheduled_at = _next_local_datetime(schedule_time)

            self._schedules[key] = {
                "entity_id": entity_id,
                "schedule_id": schedule_id,
                "time": schedule_time.strftime("%H:%M:%S"),
                "action": action,
                "repeat": repeat,
                "enabled": enabled,
            }
            if not repeat:
                self._schedules[key]["run_at"] = scheduled_at.isoformat()

            if (
                enabled
                and sync_to_device
                and not repeat
                and action == ACTION_TURN_OFF
                and coordinator.classification.supports_plug_metering
            ):
                await self._async_write_device_schedule(entity_id, schedule_time, action)

        await self._async_save()

    async def _async_handle_clear_schedule(self, call: ServiceCall) -> None:
        entity_ids = _entity_ids_from_call(call)
        schedule_id = call.data.get(CONF_SCHEDULE_ID)
        clear_device = call.data[CONF_CLEAR_DEVICE]

        for entity_id in entity_ids:
            if schedule_id:
                coordinator = self._coordinator_from_entity_id(entity_id)
                self._schedules.pop(self._schedule_key(coordinator, schedule_id), None)
            else:
                coordinator = self._coordinator_from_entity_id(entity_id)
                prefix = f"{coordinator.device.device_id}:"
                for key in list(self._schedules):
                    if key.startswith(prefix):
                        self._schedules.pop(key, None)

            if clear_device:
                await self._async_clear_device_schedule(entity_id)

        await self._async_save()

    @callback
    def _async_time_changed(self, now: datetime) -> None:
        """Check recurring schedules once per minute."""
        local_now = dt_util.as_local(now)
        weekday = local_now.weekday()
        current_time = local_now.strftime("%H:%M")
        fire_key = local_now.strftime("%Y-%m-%d %H:%M")

        for key, schedule in list(self._schedules.items()):
            if not schedule.get("enabled", True):
                continue
            entity_id = schedule.get("entity_id")
            if not entity_id:
                continue
            repeat = schedule.get("repeat", [])
            if repeat:
                if weekday not in [WEEKDAYS[day] for day in repeat]:
                    continue
                if str(schedule.get("time", ""))[:5] != current_time:
                    continue
            else:
                run_at = schedule.get("run_at")
                if not run_at:
                    continue
                try:
                    scheduled_at = dt_util.parse_datetime(run_at)
                except (TypeError, ValueError):
                    _LOGGER.debug("Ignoring invalid CozyLife schedule time %s", run_at)
                    continue
                if scheduled_at is None or local_now < dt_util.as_local(scheduled_at):
                    continue
                fire_key = str(run_at)
            if self._last_fired.get(key) == fire_key:
                continue

            self._last_fired[key] = fire_key
            if not repeat:
                self._schedules.pop(key, None)
                self.hass.async_create_task(self._async_save())
            self.hass.async_create_task(
                self.hass.services.async_call(
                    "switch",
                    schedule["action"],
                    {ATTR_ENTITY_ID: entity_id},
                    blocking=True,
                )
            )


async def async_setup_schedule_services(hass: HomeAssistant) -> None:
    """Set up schedule services once for the integration."""
    manager = hass.data[DOMAIN].get(SCHEDULE_MANAGER)
    if manager is None:
        manager = CozyLifeScheduleManager(hass)
        hass.data[DOMAIN][SCHEDULE_MANAGER] = manager
        await manager.async_setup()
