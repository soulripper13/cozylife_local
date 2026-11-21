import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    PLUG_LED_STATUS,
    PLUG_POWER_ON_STATE,
)
from .coordinator import CozyLifeCoordinator
from .switch_options import supported_dpids

_LOGGER = logging.getLogger(__name__)

POWER_ON_STATE_OPTIONS = {
    "Off": 0,
    "On": 1,
    "Previous state": 2,
}
POWER_ON_STATE_VALUES = {
    value: option for option, value in POWER_ON_STATE_OPTIONS.items()
}
LED_STATUS_OPTIONS = {
    "Off": 0,
    "State": 1,
    "Locator": 2,
}
LED_STATUS_VALUES = {
    value: option for option, value in LED_STATUS_OPTIONS.items()
}
AI_EXPRESSION_OPTIONS = {
    "Happy": 1,
    "Sad": 2,
    "Disappointed": 3,
    "Angry": 4,
    "Surprised": 5,
}
AI_EXPRESSION_VALUES = {
    value: option for option, value in AI_EXPRESSION_OPTIONS.items()
}


def _option_from_report(raw: object, options: dict[int, str]) -> str | None:
    """Decode an exact numeric option from a device report."""
    if isinstance(raw, str):
        try:
            raw = int(raw)
        except ValueError:
            return None
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    return options.get(raw)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities,
):
    """Set up CozyLife select platform."""
    coordinator: CozyLifeCoordinator = hass.data[DOMAIN][config_entry.entry_id]

    if not coordinator.device.dpid:
        _LOGGER.debug(
            "Device %s has no supported select DPIDs, skipping select setup.",
            coordinator.device.ip_address,
        )
        return

    entities = []
    if coordinator.classification.supports_switch_entities:
        dpids = supported_dpids(coordinator)
        if PLUG_POWER_ON_STATE in dpids:
            entities.append(CozyLifePowerOnStateSelect(coordinator))
        if PLUG_LED_STATUS in dpids:
            entities.append(CozyLifeLedStatusSelect(coordinator))
    if (
        coordinator.classification.is_ai_expression_device
        and "211" in (coordinator.device.dpid or [])
    ):
        entities.append(CozyLifeAiExpressionSelect(coordinator))
    if entities:
        async_add_entities(entities)
        return

    _LOGGER.debug(
        "Device %s has no supported select DPIDs, skipping select setup.",
        coordinator.device.ip_address,
    )


class CozyLifePowerOnStateSelect(
    CoordinatorEntity[CozyLifeCoordinator],
    SelectEntity,
):
    """Power-on restore behavior for the metering socket."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_has_entity_name = True

    def __init__(self, coordinator: CozyLifeCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_name = "Power-on state"
        self._attr_unique_id = f"{coordinator.device.device_id}_power_on_state"
        self._attr_options = list(POWER_ON_STATE_OPTIONS)

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device info for the parent device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.device.device_id)},
            name=self.coordinator.device.device_model_name,
            manufacturer="CozyLife",
            model=self.coordinator.device.pid,
        )

    @property
    def current_option(self) -> str | None:
        """Return the selected power-on state."""
        raw = self.coordinator.data.get(PLUG_POWER_ON_STATE)
        return _option_from_report(raw, POWER_ON_STATE_VALUES)

    async def async_select_option(self, option: str) -> None:
        """Set the power-on restore behavior."""
        value = POWER_ON_STATE_OPTIONS[option]
        if await self.coordinator.device.async_set_state({PLUG_POWER_ON_STATE: value}):
            self.coordinator.data[PLUG_POWER_ON_STATE] = value
            self.async_write_ha_state()
        else:
            _LOGGER.warning(
                "Failed to set CozyLife power-on state",
            )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()

class CozyLifeLedStatusSelect(
    CoordinatorEntity[CozyLifeCoordinator],
    SelectEntity,
):
    """Indicator LED behavior for the metering socket."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_has_entity_name = True

    def __init__(self, coordinator: CozyLifeCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_name = "LED status"
        self._attr_unique_id = f"{coordinator.device.device_id}_led_status"
        self._attr_options = list(LED_STATUS_OPTIONS)

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device info for the parent device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.device.device_id)},
            name=self.coordinator.device.device_model_name,
            manufacturer="CozyLife",
            model=self.coordinator.device.pid,
        )

    @property
    def current_option(self) -> str | None:
        """Return the selected LED behavior."""
        raw = self.coordinator.data.get(PLUG_LED_STATUS)
        return _option_from_report(raw, LED_STATUS_VALUES)

    async def async_select_option(self, option: str) -> None:
        """Set the indicator LED behavior."""
        value = LED_STATUS_OPTIONS[option]
        if await self.coordinator.device.async_set_state({PLUG_LED_STATUS: value}):
            self.coordinator.data[PLUG_LED_STATUS] = value
            self.async_write_ha_state()
        else:
            _LOGGER.warning(
                "Failed to set CozyLife LED status",
            )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()


class CozyLifeAiExpressionSelect(
    CoordinatorEntity[CozyLifeCoordinator],
    SelectEntity,
):
    """Expression selector for the panel-verified CozyLife S3 profile."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: CozyLifeCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_name = "Expression"
        self._attr_unique_id = f"{coordinator.device.device_id}_expression"
        self._attr_options = list(AI_EXPRESSION_OPTIONS)

    @property
    def device_info(self) -> DeviceInfo:
        """Return the parent device metadata."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.device.device_id)},
            name=self.coordinator.device.device_model_name,
            manufacturer="CozyLife",
            model=self.coordinator.device.pid,
        )

    @property
    def current_option(self) -> str | None:
        """Return the current expression from the coordinator's DPID state."""
        raw = self.coordinator.data.get("211")
        return _option_from_report(raw, AI_EXPRESSION_VALUES)

    async def async_select_option(self, option: str) -> None:
        """Set the local expression through the verified panel DPID."""
        value = AI_EXPRESSION_OPTIONS[option]
        if await self.coordinator.device.async_set_state({"211": value}):
            self.coordinator.data["211"] = value
            self.async_write_ha_state()
        else:
            _LOGGER.warning("Failed to set CozyLife expression")

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()
