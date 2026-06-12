import logging

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, LIGHT_COUNTDOWN, PLUG_COUNTDOWN
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
        PLUG_COUNTDOWN in dpids
        and coordinator.classification.supports_plug_metering
    ):
        async_add_entities([CozyLifePlugCountdownNumber(coordinator)])
        return

    if LIGHT_COUNTDOWN in dpids and coordinator.classification.is_light:
        async_add_entities([CozyLifeLightCountdownNumber(coordinator)])
        return

    _LOGGER.debug(
        "Device %s has no supported number DPIDs, skipping number setup.",
        coordinator.device.ip_address,
    )


class CozyLifeCountdownNumberBase(
    CoordinatorEntity[CozyLifeCoordinator],
    NumberEntity,
):
    """Countdown/timer control for CozyLife devices."""

    _attr_icon = "mdi:timer-outline"
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 0
    _attr_native_max_value = MAX_COUNTDOWN_SECONDS
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS

    _countdown_dpid: str
    _device_kind: str

    def __init__(
        self,
        coordinator: CozyLifeCoordinator,
        *,
        dpid: str,
        unique_suffix: str,
        device_kind: str,
    ) -> None:
        super().__init__(coordinator)
        self._countdown_dpid = dpid
        self._device_kind = device_kind
        self._attr_name = f"{coordinator.device.device_model_name} Countdown"
        self._attr_unique_id = f"{coordinator.device.device_id}_{unique_suffix}"

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
        raw = self.coordinator.data.get(self._countdown_dpid)
        if raw is None:
            return None

        try:
            value = int(raw)
        except (TypeError, ValueError):
            return None

        return max(0, min(MAX_COUNTDOWN_SECONDS, value))

    async def async_set_native_value(self, value: float) -> None:
        countdown = max(0, min(MAX_COUNTDOWN_SECONDS, int(value)))
        payload = {self._countdown_dpid: countdown}

        if await self.coordinator.device.async_set_state(payload):
            self.coordinator.data[self._countdown_dpid] = countdown
            await self._async_after_countdown_set(countdown)
            self.async_write_ha_state()
        else:
            _LOGGER.warning(
                "Failed to set %s countdown for CozyLife device %s to %s seconds",
                self._device_kind,
                self.coordinator.device.ip_address,
                countdown,
            )

    async def _async_after_countdown_set(self, countdown: int) -> None:
        """Handle additional behavior after countdown writes."""

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()


class CozyLifePlugCountdownNumber(CozyLifeCountdownNumberBase):
    """Countdown/timer control for CozyLife metered smart plugs."""

    def __init__(self, coordinator: CozyLifeCoordinator) -> None:
        super().__init__(
            coordinator,
            dpid=PLUG_COUNTDOWN,
            unique_suffix="countdown",
            device_kind="plug",
        )


class CozyLifeLightCountdownNumber(CozyLifeCountdownNumberBase):
    """Countdown/timer control for CozyLife lights."""

    def __init__(self, coordinator: CozyLifeCoordinator) -> None:
        super().__init__(
            coordinator,
            dpid=LIGHT_COUNTDOWN,
            unique_suffix="countdown",
            device_kind="light",
        )
