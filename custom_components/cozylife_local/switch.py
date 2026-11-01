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
from .switch_options import supported_dpids

_LOGGER = logging.getLogger(__name__)

# The DPID used for controlling all switch/outlet gangs via a bitmask.
BITMASK_DPID = "1"
LIGHT_POWER_MEMORY_DPID = "9"


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities,
):
    """Set up CozyLife switch platform based on the bitmask DPID."""
    coordinator: CozyLifeCoordinator = hass.data[DOMAIN][config_entry.entry_id]

    if not coordinator.device.dpid:
        _LOGGER.error("Missing DPID list. Cannot set up switch.")
        return

    dpids = supported_dpids(coordinator)
    energy_output_numbers = sorted(
        int(dpid) - 4
        for dpid in dpids
        if dpid.isdigit() and 5 <= int(dpid) <= 20
    )
    has_energy_output_controls = bool(
        coordinator.classification.is_energy_storage
        and BITMASK_DPID in dpids
        and energy_output_numbers
    )
    has_light_power_memory = bool(
        coordinator.classification.is_light and LIGHT_POWER_MEMORY_DPID in dpids
    )

    if not (
        coordinator.classification.supports_switch_entities
        or has_energy_output_controls
        or has_light_power_memory
        or (coordinator.classification.is_gateway and "2" in dpids)
    ):
        _LOGGER.debug(
            "Device %s (Type: %s, Source: %s) does not support switch entities, "
            "skipping switch platform setup.",
            coordinator.device.ip_address,
            coordinator.classification.effective_type_code,
            coordinator.classification.source,
        )
        return

    entities = []
    if coordinator.classification.supports_switch_entities:
        entity_count = coordinator.classification.switch_entity_count
        _LOGGER.info(
            "Detected %s %s entity/entities at %s with DPIDs: %s",
            entity_count,
            "outlet" if coordinator.classification.is_outlet else "switch",
            coordinator.device.ip_address,
            coordinator.device.dpid,
        )

        entities.extend(
            CozyLifeSwitch(
                coordinator,
                gang_bit=gang_bit,
                total_entities=entity_count,
            )
            for gang_bit in range(entity_count)
        )

    if coordinator.classification.supports_plug_metering:
        if PLUG_OVERCURRENT_PROTECTION in dpids:
            entities.append(CozyLifePlugBooleanSwitch(coordinator))

    if coordinator.classification.is_gateway and "2" in dpids:
        entities.append(
            CozyLifeDpidSwitch(
                coordinator,
                dpid="2",
                key="discovery_mode",
                name="Discovery mode",
                entity_category=EntityCategory.CONFIG,
            )
        )

    if has_light_power_memory:
        entities.append(
            CozyLifeDpidSwitch(
                coordinator,
                dpid=LIGHT_POWER_MEMORY_DPID,
                key="power_off_memory",
                name="Power-off memory",
                entity_category=EntityCategory.CONFIG,
            )
        )

    # The published energy-storage DPID table defines DPID 1 as a bitmask of
    # output channels and DPIDs 5--20 as the matching branch-output telemetry.
    # Only advertise a channel when that pairing is present on this actual
    # model; a lone DPID-1 bitmask does not reveal how many outputs it has.
    if has_energy_output_controls:
        entities.extend(
            CozyLifeEnergyOutputSwitch(coordinator, output_number)
            for output_number in energy_output_numbers
        )

    if entities:
        async_add_entities(entities)


class CozyLifeSwitch(CoordinatorEntity[CozyLifeCoordinator], SwitchEntity):
    """Representation of a single gang on a CozyLife Switch using a bitmask."""

    _attr_has_entity_name = True

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
        
        if total_entities == 1:
            self._attr_name = None
        else:
            entity_label = "Outlet" if self._is_outlet else "Switch"
            self._attr_name = f"{entity_label} {self._gang_number}"
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
            # The next coordinator refresh will sync the state later.
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


class CozyLifeEnergyOutputSwitch(CozyLifeSwitch):
    """One documented output channel of a CozyLife power station."""

    def __init__(
        self,
        coordinator: CozyLifeCoordinator,
        output_number: int,
    ) -> None:
        """Initialize the switch for one DPID-1 output bit."""
        super().__init__(
            coordinator,
            gang_bit=output_number - 1,
            total_entities=2,
        )
        self._attr_name = f"Output {output_number}"
        self._attr_unique_id = (
            f"{coordinator.device.device_id}_energy_output_{output_number}"
        )
        self._attr_device_class = SwitchDeviceClass.OUTLET


class CozyLifeDpidSwitch(CoordinatorEntity[CozyLifeCoordinator], SwitchEntity):
    """A documented boolean control represented by one DPID."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: CozyLifeCoordinator,
        *,
        dpid: str,
        key: str,
        name: str,
        entity_category: EntityCategory | None = None,
    ) -> None:
        super().__init__(coordinator)
        self._dpid = dpid
        self._attr_name = name
        self._attr_unique_id = f"{coordinator.device.device_id}_{key}"
        self._attr_entity_category = entity_category

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.device.device_id)},
            name=self.coordinator.device.device_model_name,
            manufacturer="CozyLife",
            model=self.coordinator.device.pid,
        )

    @property
    def is_on(self) -> bool | None:
        raw = self.coordinator.data.get(self._dpid)
        if raw is None:
            return None
        return raw not in (0, "0", False, "off", "false")

    async def async_turn_on(self, **kwargs: Any) -> None:
        if await self.coordinator.device.async_set_state({self._dpid: 1}):
            self.coordinator.data[self._dpid] = 1
            self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        if await self.coordinator.device.async_set_state({self._dpid: 0}):
            self.coordinator.data[self._dpid] = 0
            self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()

class CozyLifePlugBooleanSwitch(
    CoordinatorEntity[CozyLifeCoordinator],
    SwitchEntity,
):
    """Writable boolean option for the metering socket."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: CozyLifeCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_name = "Overcurrent protection"
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
