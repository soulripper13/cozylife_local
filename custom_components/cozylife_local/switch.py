import logging
from typing import Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, PLUG_OVERCURRENT_PROTECTION
from .coordinator import CozyLifeCoordinator
from .schedule import (
    DEFAULT_SCHEDULE_ID,
    SCHEDULE_MANAGER,
    CozyLifeScheduleManager,
)

_LOGGER = logging.getLogger(__name__)

# The DPID used for controlling all switch/outlet gangs via a bitmask.
BITMASK_DPID = "1"

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities,
):
    """Set up CozyLife switch platform based on the bitmask DPID."""
    coordinator: CozyLifeCoordinator = hass.data[DOMAIN][config_entry.entry_id]

    if not coordinator.device.dpid:
        _LOGGER.error(f"Missing DPID list for {coordinator.device.ip_address}. Cannot set up switch.")
        return

    if not coordinator.classification.supports_switch_entities:
        _LOGGER.debug(
            "Device %s (Type: %s, Source: %s) does not support switch entities, "
            "skipping switch platform setup.",
            coordinator.device.ip_address,
            coordinator.classification.effective_type_code,
            coordinator.classification.source,
        )
        return

    entity_count = coordinator.classification.switch_entity_count
    _LOGGER.info(
        "Detected %s %s entity/entities at %s with DPIDs: %s",
        entity_count,
        "outlet" if coordinator.classification.is_outlet else "switch",
        coordinator.device.ip_address,
        coordinator.device.dpid,
    )

    entities = [
        CozyLifeSwitch(
            coordinator,
            gang_bit=gang_bit,
            total_entities=entity_count,
        )
        for gang_bit in range(entity_count)
    ]

    if coordinator.device.pid == "2MWESf":
        entities.extend(
            [
                CozyLifePlugBooleanSwitch(coordinator),
                CozyLifePlugScheduleEnabledSwitch(coordinator),
            ]
        )

    if entities:
        async_add_entities(entities)


class CozyLifeSwitch(CoordinatorEntity[CozyLifeCoordinator], SwitchEntity):
    """Representation of a single gang on a CozyLife Switch using a bitmask."""

    def __init__(
        self,
        coordinator: CozyLifeCoordinator,
        gang_bit: int,
        total_entities: int,
    ):
        """
        Initialize the CozyLife Switch.
        Args:
            coordinator: The data coordinator.
            gang_bit: The bit position for this switch gang (e.g., 0 for switch 1, 1 for switch 2).
        """
        super().__init__(coordinator)
        self._gang_bit = gang_bit
        self._gang_number = gang_bit + 1
        self._total_entities = total_entities
        self._is_outlet = coordinator.classification.is_outlet
        
        device_name = coordinator.device.device_model_name or "CozyLife Switch"
        if total_entities == 1:
            self._attr_name = device_name
        else:
            entity_label = "Outlet" if self._is_outlet else "Switch"
            self._attr_name = f"{device_name} {entity_label} {self._gang_number}"
        self._attr_unique_id = f"{coordinator.device.device_id}_{self._gang_number}"
        self._attr_device_class = (
            SwitchDeviceClass.OUTLET if self._is_outlet else SwitchDeviceClass.SWITCH
        )

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
    def is_on(self) -> bool:
        """Return true if this switch gang's bit is set in the bitmask."""
        # Get the integer value of the bitmask from the coordinator's data.
        bitmask_state = self.coordinator.data.get(BITMASK_DPID, 0)
        
        # Check if the bit for this gang is set.
        return (bitmask_state & (1 << self._gang_bit)) != 0

    async def _async_set_gang_state(self, new_gang_state: bool) -> None:
        """Calculate and send the new bitmask state, then optimistically update."""
        current_bitmask = self.coordinator.data.get(BITMASK_DPID, 0)
        
        if new_gang_state:
            # To turn on, perform a bitwise OR.
            new_bitmask = current_bitmask | (1 << self._gang_bit)
        else:
            # To turn off, perform a bitwise AND with a NOT.
            new_bitmask = current_bitmask & ~(1 << self._gang_bit)
            
        # Only send a command if the state actually needs to change.
        if new_bitmask != current_bitmask:
            payload = {BITMASK_DPID: new_bitmask}
            
            # Send the command to the device.
            if await self.coordinator.device.async_set_state(payload):
                # Optimistically update the state in the coordinator's data.
                # This makes the UI feel instantaneous.
                self.coordinator.data[BITMASK_DPID] = new_bitmask
                self.async_write_ha_state()
            # Do NOT request a refresh here, as it causes a race condition.
            # The regular scheduled update will sync the state later.
        else:
            _LOGGER.debug(f"Switch {self.name} is already in the desired state.")

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn this specific switch gang on."""
        await self._async_set_gang_state(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn this specific switch gang off."""
        await self._async_set_gang_state(False)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()


class CozyLifePlugScheduleSwitchBase(SwitchEntity):
    """Base class for Home Assistant backed schedule switches."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: CozyLifeCoordinator,
        schedule_id: str = DEFAULT_SCHEDULE_ID,
    ) -> None:
        self.coordinator = coordinator
        self._schedule_id = schedule_id

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


class CozyLifePlugScheduleEnabledSwitch(CozyLifePlugScheduleSwitchBase):
    """Enable the default plug schedule."""

    _attr_icon = "mdi:calendar-check"

    def __init__(
        self,
        coordinator: CozyLifeCoordinator,
        schedule_id: str = DEFAULT_SCHEDULE_ID,
    ) -> None:
        super().__init__(coordinator, schedule_id)
        self._attr_name = f"{coordinator.device.device_model_name} Schedule Enabled"
        self._attr_unique_id = f"{coordinator.device.device_id}_schedule_enabled"

    @property
    def is_on(self) -> bool:
        schedule = self._manager.schedule_for_coordinator(
            self.coordinator,
            self._schedule_id,
        )
        return bool(schedule.get("enabled", False))

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._manager.async_update_coordinator_schedule(
            self.coordinator,
            self._schedule_id,
            enabled=True,
        )
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._manager.async_update_coordinator_schedule(
            self.coordinator,
            self._schedule_id,
            enabled=False,
            sync_to_device=False,
        )
        self.async_write_ha_state()


class CozyLifePlugBooleanSwitch(
    CoordinatorEntity[CozyLifeCoordinator],
    SwitchEntity,
):
    """Writable boolean option for the metering socket."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: CozyLifeCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_name = f"{coordinator.device.device_model_name} Overcurrent Protection"
        self._attr_unique_id = (
            f"{coordinator.device.device_id}_overcurrent_protection"
        )

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
    def is_on(self) -> bool | None:
        """Return whether overcurrent protection is enabled."""
        raw = self.coordinator.data.get(PLUG_OVERCURRENT_PROTECTION)
        if raw is None:
            return None
        return bool(raw)

    async def _async_set_state(self, enabled: bool) -> None:
        value = 1 if enabled else 0
        if await self.coordinator.device.async_set_state(
            {PLUG_OVERCURRENT_PROTECTION: value}
        ):
            self.coordinator.data[PLUG_OVERCURRENT_PROTECTION] = value
            self.async_write_ha_state()
        else:
            _LOGGER.warning(
                "Failed to set overcurrent protection for CozyLife device %s",
                self.coordinator.device.ip_address,
            )

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable overcurrent protection."""
        await self._async_set_state(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable overcurrent protection."""
        await self._async_set_state(False)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()
