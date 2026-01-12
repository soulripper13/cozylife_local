import logging
from typing import Any, Dict, List, Optional

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_HS_COLOR,
    ColorMode,
    LightEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util.color import (
    color_temperature_kelvin_to_mired as kelvin_to_mired,
    color_temperature_mired_to_kelvin as mired_to_kelvin,
)


from .const import (
    DOMAIN,
    LIGHT_TYPE_CODE,
    BRIGHT,
    BRIGHT_ALT,  # Alternative brightness DPID for some devices
    TEMP, # Corrected import
    HUE,
    SAT,
    SWITCH,
    WORK_MODE,
)
from .coordinator import CozyLifeCoordinator

_LOGGER = logging.getLogger(__name__)

# Assumed Kelvin range for CozyLife lights. This can be adjusted.
MIN_KELVIN = 2000
MAX_KELVIN = 6500

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities,
):
    """Set up CozyLife light platform."""
    coordinator: CozyLifeCoordinator = hass.data[DOMAIN][config_entry.entry_id]

    if coordinator.device.device_type_code != LIGHT_TYPE_CODE:
        _LOGGER.info(f"Device {coordinator.device.ip_address} (Type: {coordinator.device.device_type_code}) is not a light, skipping light platform setup.")
        return

    if coordinator.device.dpid is None or coordinator.device.device_model_name is None:
        _LOGGER.error(f"❌ Missing device DPID or model name for {coordinator.device.ip_address}. Cannot set up light.")
        return

    _LOGGER.warning(f"💡 Setting up LIGHT entity for {coordinator.device.device_model_name}")
    async_add_entities([CozyLifeLight(coordinator)], True)

class CozyLifeLight(CoordinatorEntity[CozyLifeCoordinator], LightEntity):
    """Representation of a CozyLife Light."""

    def __init__(self, coordinator: CozyLifeCoordinator):
        """Initialize the CozyLife Light."""
        super().__init__(coordinator)
        self._attr_name = coordinator.device.device_model_name
        self._attr_unique_id = f"{coordinator.device.device_id}_light"

        _LOGGER.warning(f"   ├─ Analyzing light capabilities...")
        _LOGGER.warning(f"   ├─ DPIDs: {coordinator.device.dpid}")

        self._supported_color_modes: set[ColorMode] = set()
        if not coordinator.device.dpid:
            self._supported_color_modes.add(ColorMode.ONOFF)
            _LOGGER.warning(f"   └─ ⚠️  No DPIDs found - defaulting to ON/OFF only")
            return

        # Determine which brightness DPID this device uses (if any)
        # Some devices use DPID '4', others use DPID '3' for brightness
        has_brightness = BRIGHT in coordinator.device.dpid or BRIGHT_ALT in coordinator.device.dpid
        self._brightness_dpid = BRIGHT if BRIGHT in coordinator.device.dpid else (BRIGHT_ALT if BRIGHT_ALT in coordinator.device.dpid else None)

        if self._brightness_dpid:
            _LOGGER.warning(f"   ├─ ✓ Brightness: DPID {self._brightness_dpid}")

        # Check for RGB color support (HS mode includes brightness)
        if HUE in coordinator.device.dpid and SAT in coordinator.device.dpid:
            self._supported_color_modes.add(ColorMode.HS)
            _LOGGER.warning(f"   ├─ ✓ RGB Color: DPIDs {HUE} (Hue) + {SAT} (Saturation)")
        # Check for color temperature support (also includes brightness)
        # Only treat DPID '3' as TEMP if device doesn't use it for brightness
        if TEMP in coordinator.device.dpid and self._brightness_dpid != BRIGHT_ALT:
            self._supported_color_modes.add(ColorMode.COLOR_TEMP)
            _LOGGER.warning(f"   ├─ ✓ Color Temperature: DPID {TEMP}")
        # Check for brightness-only support (no color)
        if has_brightness and not self._supported_color_modes:
            self._supported_color_modes.add(ColorMode.BRIGHTNESS)
            _LOGGER.warning(f"   ├─ Brightness-only light (no color)")
        # Fallback to on/off only
        if not self._supported_color_modes:
            self._supported_color_modes.add(ColorMode.ONOFF)
            _LOGGER.warning(f"   └─ ⚠️  ON/OFF only - no dimming/color capabilities detected")
        else:
            modes_str = ", ".join([mode.value for mode in self._supported_color_modes])
            _LOGGER.warning(f"   └─ Supported modes: {modes_str}")

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device info."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.device.device_id)},
            name=self.coordinator.device.device_model_name,
            manufacturer="CozyLife",
            model=self.coordinator.device.pid,
        )

    @property
    def is_on(self) -> Optional[bool]:
        """Return true if light is on."""
        return self.coordinator.data.get(SWITCH) == 1

    @property
    def brightness(self) -> Optional[int]:
        """Return the brightness of this light between 0..255."""
        if not self._brightness_dpid or self._brightness_dpid not in self.coordinator.data:
            return None
        # Device returns 0-1000, HA uses 0-255
        device_brightness = self.coordinator.data[self._brightness_dpid]
        return int((device_brightness / 1000) * 255)

    @property
    def color_mode(self) -> Optional[ColorMode]:
        """Return the color mode of the light."""
        # WORK_MODE '2' often determines if the light is in white or color mode
        work_mode = self.coordinator.data.get(WORK_MODE)
        if work_mode == 1 and ColorMode.HS in self._supported_color_modes: # Assuming 1 is color mode
             return ColorMode.HS
        if work_mode == 0 and ColorMode.COLOR_TEMP in self._supported_color_modes: # Assuming 0 is white mode
            return ColorMode.COLOR_TEMP

        # Fallback if work_mode is not present
        if ColorMode.HS in self._supported_color_modes and HUE in self.coordinator.data:
            return ColorMode.HS
        if ColorMode.COLOR_TEMP in self._supported_color_modes and TEMP in self.coordinator.data:
            return ColorMode.COLOR_TEMP
        if ColorMode.BRIGHTNESS in self._supported_color_modes and self._brightness_dpid and self._brightness_dpid in self.coordinator.data:
             return ColorMode.BRIGHTNESS
        return ColorMode.ONOFF

    @property
    def supported_color_modes(self) -> Optional[set[ColorMode]]:
        """Flag of supported color modes by the light."""
        return self._supported_color_modes

    @property
    def hs_color(self) -> Optional[tuple[float, float]]:
        """Return the hs color value."""
        if HUE not in self.coordinator.data or SAT not in self.coordinator.data:
            return None
        # Assuming device sends Hue 0-360, Saturation 0-1000. Normalize to HA's format.
        hue = self.coordinator.data[HUE]
        saturation = self.coordinator.data[SAT] / 10
        return (hue, saturation)

    @property
    def color_temp_kelvin(self) -> Optional[int]:
        """Return the color temperature in Kelvin."""
        if TEMP not in self.coordinator.data:
            return None
        # Convert device's 0-1000 scale to Kelvin 2000-6500 scale
        device_val = self.coordinator.data[TEMP]
        return int(((device_val / 1000) * (MAX_KELVIN - MIN_KELVIN)) + MIN_KELVIN)

    @property
    def min_color_temp_kelvin(self) -> int:
        """Return the warmest color temperature in Kelvin."""
        return MIN_KELVIN

    @property
    def max_color_temp_kelvin(self) -> int:
        """Return the coldest color temperature in Kelvin."""
        return MAX_KELVIN

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on."""
        payload: Dict[str, Any] = {SWITCH: 1}

        if ATTR_BRIGHTNESS in kwargs and self._brightness_dpid and self._brightness_dpid in self.coordinator.device.dpid:
            ha_brightness = kwargs[ATTR_BRIGHTNESS]
            # Convert HA's 0-255 to device's 0-1000
            payload[self._brightness_dpid] = int((ha_brightness / 255) * 1000)

        if ATTR_HS_COLOR in kwargs and HUE in self.coordinator.device.dpid and SAT in self.coordinator.device.dpid:
            hs_color = kwargs[ATTR_HS_COLOR]
            # Set work mode to color (assuming 1)
            if WORK_MODE in self.coordinator.device.dpid:
                payload[WORK_MODE] = 1
            # Convert HA's Hue 0-360, Sat 0-100 to device's Hue 0-360, Sat 0-1000
            payload[HUE] = int(hs_color[0])
            payload[SAT] = int(hs_color[1] * 10)

        if ATTR_COLOR_TEMP_KELVIN in kwargs and TEMP in self.coordinator.device.dpid and self._brightness_dpid != BRIGHT_ALT:
            ha_kelvin = kwargs[ATTR_COLOR_TEMP_KELVIN]
            # Set work mode to white (assuming 0)
            if WORK_MODE in self.coordinator.device.dpid:
                payload[WORK_MODE] = 0
            # Convert HA's Kelvin to device's 0-1000 scale
            normalized_val = (ha_kelvin - MIN_KELVIN) / (MAX_KELVIN - MIN_KELVIN)
            payload[TEMP] = int(normalized_val * 1000)

        await self.coordinator.device.async_set_state(payload)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        payload = {SWITCH: 0}
        await self.coordinator.device.async_set_state(payload)
        await self.coordinator.async_request_refresh()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()