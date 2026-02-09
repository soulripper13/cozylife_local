import logging
from typing import Any, Dict, Optional

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, LIGHT_TYPE_CODE, RGB_LIGHT_TYPE_CODE
from .coordinator import CozyLifeCoordinator

_LOGGER = logging.getLogger(__name__)

# The DPID used for controlling all switch gangs via a bitmask.
BITMASK_DPID = '1'
# Countdown timer DPIDs for each gang (gang 1-8)
COUNTDOWN_DPIDS = ['2', '4', '6', '8', '10', '12', '14', '16']

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

    # Skip devices that are lights by checking for light-specific DPIDs
    # - DPID '3' (TEMP - color temperature) exists in dimmable/RGB lights, never in switches
    # - DPID '6' (SAT - saturation) exists in RGB lights, never in switches
    # - Multi-gang switches have DPID '2' and '4' (countdown timers), but simple lights only have '2'
    # DO NOT check device type code here - switches often report incorrect type codes
    from .const import TEMP, SAT, WORK_MODE, BRIGHT, HUE, LIGHT_TYPE_CODE, RGB_LIGHT_TYPE_CODE

    has_work_mode = WORK_MODE in coordinator.device.dpid  # WORK_MODE='2'
    has_temp = TEMP in coordinator.device.dpid            # TEMP='3'
    has_bright = BRIGHT in coordinator.device.dpid        # BRIGHT='4'
    has_hue = HUE in coordinator.device.dpid              # HUE='5'
    has_saturation = SAT in coordinator.device.dpid       # SAT='6'
    is_light_type = coordinator.device.device_type_code in [LIGHT_TYPE_CODE, RGB_LIGHT_TYPE_CODE]

    # Definitive light indicators:
    # 1. Has TEMP (3) -> CCT Light
    # 2. Has SAT (6) AND HUE (5) -> RGB Light
    #    (Note: DPID 6 is also 'countdown_3' for switches.
    #     Standard 3-gang switches have 6 but NOT 5.
    #     If a device has both 5 and 6, we check the type code to be sure it's not an advanced switch.)
    if has_temp or (has_saturation and has_hue and is_light_type):
        _LOGGER.debug(f"Device {coordinator.device.ip_address} has light-specific DPIDs (TEMP='3' or SAT='6'+HUE='5'+Type='Light'), skipping switch platform setup.")
        return

    # Simple light case: has DPID 2 (work_mode) but not DPID 4 (brightness/countdown_2)
    # AND reports light type code - this is a simple on/off light
    if has_work_mode and not has_bright and is_light_type:
        _LOGGER.debug(f"Device {coordinator.device.ip_address} appears to be a simple light (has DPID 2 but not 4, with light type code), skipping switch platform setup.")
        return

    entities = []
    # Check if the primary bitmask DPID is supported by the device.
    if BITMASK_DPID in coordinator.device.dpid:
        # Auto-detect number of gangs by counting countdown timer DPIDs
        num_gangs = sum(1 for dpid in COUNTDOWN_DPIDS if dpid in coordinator.device.dpid)

        if num_gangs == 0:
            # Fallback: assume 1 gang if no countdown DPIDs found
            num_gangs = 1

        _LOGGER.info(f"Detected {num_gangs}-gang switch at {coordinator.device.ip_address} with DPIDs: {coordinator.device.dpid}")

        # Create an entity for each gang
        for i in range(num_gangs):
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