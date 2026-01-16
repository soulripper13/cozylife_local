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
    color_hs_to_RGB,
    color_RGB_to_hs,
)


from .const import (
    DOMAIN,
    LIGHT_TYPE_CODE,
    RGB_LIGHT_TYPE_CODE,
    BRIGHT,
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

    if coordinator.device.dpid is None or coordinator.device.device_model_name is None:
        _LOGGER.error(f"Missing device DPID or model name for {coordinator.device.ip_address}. Cannot set up light.")
        return

    # Distinguish switches from lights using DPID patterns:
    # - Switches: Have DPID '5' (normal_time_2) but NOT DPID '6'
    # - RGB Lights: Have both DPID '5' (hue_value) AND DPID '6' (sat_value)
    # This is reliable because RGB lights always have saturation if they have hue.
    has_dpid_5 = HUE in coordinator.device.dpid  # HUE='5' in const.py
    has_dpid_6 = SAT in coordinator.device.dpid  # SAT='6' in const.py

    if has_dpid_5 and not has_dpid_6:
        _LOGGER.debug(f"Device {coordinator.device.ip_address} has DPID 5 but not DPID 6, indicating it's a switch (not a light), skipping light platform setup.")
        return

    if coordinator.device.device_type_code not in [LIGHT_TYPE_CODE, RGB_LIGHT_TYPE_CODE]:
        _LOGGER.debug(f"Device {coordinator.device.ip_address} (Type: {coordinator.device.device_type_code}) is not a light, skipping light platform setup.")
        return

    async_add_entities([CozyLifeLight(coordinator)], True)

class CozyLifeLight(CoordinatorEntity[CozyLifeCoordinator], LightEntity):
    """Representation of a CozyLife Light."""

    def __init__(self, coordinator: CozyLifeCoordinator):
        """Initialize the CozyLife Light."""
        super().__init__(coordinator)
        self._attr_name = coordinator.device.device_model_name
        self._attr_unique_id = f"{coordinator.device.device_id}_light"
        
        self._supported_color_modes: set[ColorMode] = set()
        if not coordinator.device.dpid:
            self._supported_color_modes.add(ColorMode.ONOFF)
            return

        if HUE in coordinator.device.dpid and SAT in coordinator.device.dpid:
            self._supported_color_modes.add(ColorMode.HS)
        if TEMP in coordinator.device.dpid:
            self._supported_color_modes.add(ColorMode.COLOR_TEMP)
        if BRIGHT in coordinator.device.dpid and not self._supported_color_modes:
            self._supported_color_modes.add(ColorMode.BRIGHTNESS)
        if not self._supported_color_modes:
            self._supported_color_modes.add(ColorMode.ONOFF)

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
        # Original integration checks if value > 0, since we send 255 when turning on
        return self.coordinator.data.get(SWITCH, 0) > 0

    @property
    def brightness(self) -> Optional[int]:
        """Return the brightness of this light between 0..255."""
        if BRIGHT not in self.coordinator.data:
            return None
        # Device returns 0-1000, HA uses 0-255
        device_brightness = self.coordinator.data[BRIGHT]
        return int((device_brightness / 1000) * 255)

    @property
    def color_mode(self) -> Optional[ColorMode]:
        """Return the color mode of the light."""
        # The original integration determines color mode based on which values are valid (< 60000)
        # not just based on work_mode. This is critical for proper UI updates.

        # Check if we have valid color values (< 60000)
        if (HUE in self.coordinator.data and SAT in self.coordinator.data and
            self.coordinator.data[HUE] < 60000):
            return ColorMode.HS

        # Check if we have valid color temp values (< 60000)
        if TEMP in self.coordinator.data and self.coordinator.data[TEMP] < 60000:
            return ColorMode.COLOR_TEMP

        # Check if we have brightness control
        if ColorMode.BRIGHTNESS in self._supported_color_modes and BRIGHT in self.coordinator.data:
            return ColorMode.BRIGHTNESS

        # Fallback to on/off
        return ColorMode.ONOFF

    @property
    def supported_color_modes(self) -> Optional[set[ColorMode]]:
        """Flag of supported color modes by the light."""
        return self._supported_color_modes

    @property
    def hs_color(self) -> Optional[tuple[float, float]]:
        """Return the hs color value."""
        if HUE not in self.coordinator.data or SAT not in self.coordinator.data:
            _LOGGER.info(f"[COZYLIFE] hs_color: HUE or SAT not in coordinator.data. Data keys: {self.coordinator.data.keys()}")
            return None

        # Check if color value is valid (< 60000). Device sends 65535 when in white mode.
        # This matches the original integration's validation logic.
        color_value = self.coordinator.data[HUE]
        if color_value >= 60000:
            _LOGGER.info(f"[COZYLIFE] hs_color: Color value {color_value} >= 60000, ignoring (device in white mode)")
            return None

        # Device sends Hue 0-360, Saturation 0-1000
        # Do RGB round-trip conversion for consistency (matches original integration)
        hue = round(self.coordinator.data[HUE])
        saturation = round(self.coordinator.data[SAT] / 10)
        _LOGGER.info(f"[COZYLIFE] hs_color: Device values - hue={hue}, sat={saturation}, raw_sat={self.coordinator.data[SAT]}")
        r, g, b = color_hs_to_RGB(hue, saturation)
        hs_color = color_RGB_to_hs(r, g, b)
        _LOGGER.info(f"[COZYLIFE] hs_color: After RGB conversion - hs_color={hs_color}")
        return hs_color

    @property
    def color_temp_kelvin(self) -> Optional[int]:
        """Return the color temperature in Kelvin."""
        if TEMP not in self.coordinator.data:
            return None

        # Check if color temp value is valid (< 60000). Device sends 65535 when in color mode.
        # This matches the original integration's validation logic.
        temp_value = self.coordinator.data[TEMP]
        if temp_value >= 60000:
            _LOGGER.info(f"[COZYLIFE] color_temp_kelvin: Temp value {temp_value} >= 60000, ignoring (device in color mode)")
            return None

        # Convert device's 0-1000 scale to Kelvin 2000-6500 scale
        return int(((temp_value / 1000) * (MAX_KELVIN - MIN_KELVIN)) + MIN_KELVIN)

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
        # Match original integration: start with power=255, work_mode=0
        payload: Dict[str, Any] = {SWITCH: 255, WORK_MODE: 0}

        if ATTR_BRIGHTNESS in kwargs and BRIGHT in self.coordinator.device.dpid:
            ha_brightness = kwargs[ATTR_BRIGHTNESS]
            # Convert HA's 0-255 to device's 0-1000 (use round like original)
            payload[BRIGHT] = round(ha_brightness / 255 * 1000)

        if ATTR_HS_COLOR in kwargs and HUE in self.coordinator.device.dpid and SAT in self.coordinator.device.dpid:
            hs_color = kwargs[ATTR_HS_COLOR]
            # Do RGB round-trip conversion for color correction (from original integration)
            r, g, b = color_hs_to_RGB(*hs_color)
            hs_color = color_RGB_to_hs(r, g, b)
            # Convert HA's Hue 0-360, Sat 0-100 to device's Hue 0-360, Sat 0-1000
            payload[HUE] = round(hs_color[0])
            payload[SAT] = round(hs_color[1] * 10)

        if ATTR_COLOR_TEMP_KELVIN in kwargs and TEMP in self.coordinator.device.dpid:
            ha_kelvin = kwargs[ATTR_COLOR_TEMP_KELVIN]
            # Work mode already set to 0 above
            # Convert HA's Kelvin to device's 0-1000 scale
            normalized_val = (ha_kelvin - MIN_KELVIN) / (MAX_KELVIN - MIN_KELVIN)
            payload[TEMP] = round(normalized_val * 1000)

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
        _LOGGER.info(f"[COZYLIFE] Light entity {self.name} received coordinator update. Data: {self.coordinator.data}")
        self.async_write_ha_state()