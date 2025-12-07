import logging
from typing import Any, Dict, Optional

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, LIGHT_TYPE_CODE
from .coordinator import CozyLifeCoordinator

_LOGGER = logging.getLogger(__name__)

# The DPID used for controlling all switch gangs via a bitmask.
BITMASK_DPID = '1'
# For a double rocker switch, we will create entities for the first two gangs (bits 0 and 1).
NUM_GANGS = 2

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

    # Do not create switches for devices that are explicitly lights.
    if coordinator.device.device_type_code == LIGHT_TYPE_CODE:
        _LOGGER.info(f"Device {coordinator.device.ip_address} is a light, skipping switch setup.")
        return

    entities = []
    # Check if the primary bitmask DPID is supported by the device.
    if BITMASK_DPID in coordinator.device.dpid:
        # Create an entity for each gang of the double rocker switch.
        for i in range(NUM_GANGS):
            entities.append(CozyLifeSwitch(coordinator, gang_bit=i))
    else:
        _LOGGER.info(
            f"No switch entities created for device {coordinator.device.ip_address}. "
            f"The required bitmask DPID '{BITMASK_DPID}' was not found in the device's reported DPIDs: {coordinator.device.dpid}."
        )

    if entities:
        async_add_entities(entities)


class CozyLifeSwitch(CoordinatorEntity[CozyLifeCoordinator], SwitchEntity):
    """Representation of a single gang on a CozyLife Switch using a bitmask."""

    def __init__(self, coordinator: CozyLifeCoordinator, gang_bit: int):
        """
        Initialize the CozyLife Switch.
        Args:
            coordinator: The data coordinator.
            gang_bit: The bit position for this switch gang (e.g., 0 for switch 1, 1 for switch 2).
        """
        super().__init__(coordinator)
        self._gang_bit = gang_bit
        self._gang_number = gang_bit + 1
        
        device_name = coordinator.device.device_model_name or "CozyLife Switch"
        
        self._attr_name = f"{device_name} {self._gang_number}"
        self._attr_unique_id = f"{coordinator.device.device_id}_{self._gang_number}"

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