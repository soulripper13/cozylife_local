import logging
import re
from typing import Any, Dict, Optional

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
    color_hs_to_RGB,
    color_RGB_to_hs,
)


from .const import (
    DOMAIN,
    BRIGHT,
    TEMP,
    HUE,
    SAT,
    SWITCH,
    WORK_MODE,
    DEFAULT_MIN_KELVIN,
    DEFAULT_MAX_KELVIN,
    LIGHT_KELVIN_RANGES,
)
from .coordinator import CozyLifeCoordinator

_LOGGER = logging.getLogger(__name__)

# DreamColor strips (pid v3sjfa) ignore dpid 5/6 and report/accept colour as
# dpid 16 = "HHHHSSSSVVVV" hex (hue 0-360, sat 0-1000, VVVV=ffff). The device
# only applies it when dpid 2 (work mode 0) is in the same command.
COLOR_HEX = "16"


def _parse_color_hex(value: Any) -> Optional[tuple[int, int]]:
    """Parse dpid 16 'HHHHSSSSVVVV' into (hue 0-360, sat 0-1000)."""
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-fA-F]{12}", value) is None:
        return None
    try:
        hue = int(value[0:4], 16)
        sat = int(value[4:8], 16)
    except ValueError:
        return None
    if hue > 360 or sat > 1000:
        return None
    return hue, sat


def _kelvin_range_for_pid(pid: str | None) -> tuple[int, int]:
    """Return the default Kelvin range for a light model."""
    return LIGHT_KELVIN_RANGES.get(
        pid or "",
        (DEFAULT_MIN_KELVIN, DEFAULT_MAX_KELVIN),
    )


def _valid_color_component(value: Any, maximum: int) -> bool:
    """Accept finite numeric reports within the component's wire range."""
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and 0 <= value <= maximum
    )


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities,
):
    """Set up CozyLife light platform."""
    coordinator: CozyLifeCoordinator = hass.data[DOMAIN][config_entry.entry_id]

    if coordinator.device.dpid is None or coordinator.device.device_model_name is None:
        _LOGGER.error("Missing device DPID or model name. Cannot set up light.")
        return

    if not coordinator.classification.is_light:
        _LOGGER.debug(
            "Device %s (Type: %s, Source: %s) is not a light, skipping light platform setup.",
            coordinator.device.ip_address,
            coordinator.classification.effective_type_code,
            coordinator.classification.source,
        )
        return

    async_add_entities([CozyLifeLight(coordinator, config_entry)], True)

class CozyLifeLight(CoordinatorEntity[CozyLifeCoordinator], LightEntity):
    """Representation of a CozyLife Light."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: CozyLifeCoordinator, entry: ConfigEntry):
        """Initialize the CozyLife Light."""
        super().__init__(coordinator)
        self._attr_name = None
        self._attr_unique_id = f"{coordinator.device.device_id}_light"
        model_min_kelvin, model_max_kelvin = _kelvin_range_for_pid(
            coordinator.device.pid
        )
        stored_min_kelvin = entry.data.get("min_kelvin", DEFAULT_MIN_KELVIN)
        stored_max_kelvin = entry.data.get("max_kelvin", DEFAULT_MAX_KELVIN)
        self._min_kelvin = entry.options.get(
            "min_kelvin",
            (
                model_min_kelvin
                if stored_min_kelvin == DEFAULT_MIN_KELVIN
                else stored_min_kelvin
            ),
        )
        self._max_kelvin = entry.options.get(
            "max_kelvin",
            (
                model_max_kelvin
                if stored_max_kelvin == DEFAULT_MAX_KELVIN
                else stored_max_kelvin
            ),
        )
        
        self._supported_color_modes: set[ColorMode] = set()
        if not coordinator.device.dpid:
            self._supported_color_modes.add(ColorMode.ONOFF)
            return

        if (coordinator.device.pid == "v3sjfa"
                or HUE in coordinator.device.dpid and SAT in coordinator.device.dpid
                or _parse_color_hex(coordinator.data.get(COLOR_HEX)) is not None):
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
        value = self.coordinator.data.get(SWITCH, 0)
        if value in ("0", "1"):
            return value == "1"
        if isinstance(value, (int, float)):
            return value > 0
        return None

    @property
    def brightness(self) -> Optional[int]:
        """Return the brightness of this light between 0..255."""
        if BRIGHT not in self.coordinator.data:
            return None
        # Device returns 0-1000, HA uses 0-255
        device_brightness = self.coordinator.data[BRIGHT]
        if (
            isinstance(device_brightness, bool)
            or not isinstance(device_brightness, (int, float))
            or not 0 <= device_brightness <= 1000
        ):
            return None
        return int((device_brightness / 1000) * 255)

    @property
    def color_mode(self) -> Optional[ColorMode]:
        """Return the color mode of the light."""
        if self._dreamcolor_white_value() is not None:
            return ColorMode.COLOR_TEMP
        if _parse_color_hex(self.coordinator.data.get(COLOR_HEX)) is not None:
            return ColorMode.HS
        # The original integration determines color mode based on which values are valid (< 60000)
        # not just based on work_mode. This is critical for proper UI updates.

        # Check if we have valid color values (< 60000)
        if (
            _valid_color_component(self.coordinator.data.get(HUE), 360)
            and _valid_color_component(self.coordinator.data.get(SAT), 1000)
        ):
            return ColorMode.HS

        # Check if we have valid color temp values (< 60000)
        if _valid_color_component(self.coordinator.data.get(TEMP), 1000):
            return ColorMode.COLOR_TEMP

        # Check if we have brightness control
        if ColorMode.BRIGHTNESS in self._supported_color_modes and BRIGHT in self.coordinator.data:
            return ColorMode.BRIGHTNESS

        if ColorMode.COLOR_TEMP in self._supported_color_modes:
            return ColorMode.COLOR_TEMP
        if ColorMode.HS in self._supported_color_modes:
            return ColorMode.HS
        if ColorMode.BRIGHTNESS in self._supported_color_modes:
            return ColorMode.BRIGHTNESS

        return ColorMode.ONOFF

    @property
    def supported_color_modes(self) -> Optional[set[ColorMode]]:
        """Flag of supported color modes by the light."""
        return self._supported_color_modes

    @property
    def hs_color(self) -> Optional[tuple[float, float]]:
        """Return the hs color value."""
        if self._dreamcolor_white_value() is not None:
            return None
        parsed = _parse_color_hex(self.coordinator.data.get(COLOR_HEX))
        if parsed is not None:
            return float(parsed[0]), parsed[1] / 10

        if HUE not in self.coordinator.data or SAT not in self.coordinator.data:
            _LOGGER.debug(f"[COZYLIFE] hs_color: HUE or SAT not in coordinator.data. Data keys: {self.coordinator.data.keys()}")
            return None

        # Check if color value is valid (< 60000). Device sends 65535 when in white mode.
        # This matches the original integration's validation logic.
        hue_value = self.coordinator.data[HUE]
        saturation_value = self.coordinator.data[SAT]
        if not (
            _valid_color_component(hue_value, 360)
            and _valid_color_component(saturation_value, 1000)
        ):
            return None

        # Device sends Hue 0-360, Saturation 0-1000
        # Do RGB round-trip conversion for consistency (matches original integration)
        hue = round(hue_value)
        saturation = round(saturation_value / 10)
        r, g, b = color_hs_to_RGB(hue, saturation)
        hs_color = color_RGB_to_hs(r, g, b)
        return hs_color

    def _dreamcolor_white_value(self) -> Optional[int]:
        """Decode the white slider value in a whole-strip DPID 16 report."""
        if ColorMode.COLOR_TEMP not in self._supported_color_modes:
            return None
        value = self.coordinator.data.get(COLOR_HEX)
        if not isinstance(value, str) or re.fullmatch(r"ffffffff[0-9a-f]{4}", value, re.I) is None:
            return None
        temperature = int(value[8:], 16)
        return temperature if temperature <= 1000 else None

    @property
    def color_temp_kelvin(self) -> Optional[int]:
        """Return the color temperature in Kelvin."""
        temp_value = self._dreamcolor_white_value()
        if temp_value is None:
            temp_value = self.coordinator.data.get(TEMP)
        if not _valid_color_component(temp_value, 1000):
            return None

        # Convert device's 0-1000 scale to Kelvin
        return int(((temp_value / 1000) * (self._max_kelvin - self._min_kelvin)) + self._min_kelvin)

    @property
    def min_color_temp_kelvin(self) -> int:
        """Return the warmest color temperature in Kelvin."""
        return self._min_kelvin

    @property
    def max_color_temp_kelvin(self) -> int:
        """Return the coldest color temperature in Kelvin."""
        return self._max_kelvin

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on."""
        # Match original integration where work mode is supported.
        payload: Dict[str, Any] = {SWITCH: 1}

        if WORK_MODE in self.coordinator.device.dpid:
            payload[WORK_MODE] = 0

        if ATTR_BRIGHTNESS in kwargs and BRIGHT in self.coordinator.device.dpid:
            ha_brightness = kwargs[ATTR_BRIGHTNESS]
            # Convert HA's 0-255 to device's 0-1000 (use round like original)
            payload[BRIGHT] = round(ha_brightness / 255 * 1000)
            if (
                self.coordinator.device.pid == "kHap0G"
                and ATTR_HS_COLOR not in kwargs
                and ATTR_COLOR_TEMP_KELVIN not in kwargs
            ):
                # This model's panel pairs brightness with the current
                # scene tab's mode. Preserve it when the device reports it.
                mode = self.coordinator.data.get(WORK_MODE)
                if mode in (6, 7, 8):
                    payload[WORK_MODE] = mode

        color_hex = (
            self.coordinator.device.pid == "v3sjfa"
            or _parse_color_hex(self.coordinator.data.get(COLOR_HEX)) is not None
        )
        if ATTR_HS_COLOR in kwargs and (
            color_hex or HUE in self.coordinator.device.dpid and SAT in self.coordinator.device.dpid
        ):
            hs_color = kwargs[ATTR_HS_COLOR]
            if color_hex:
                # DreamColor: colour via dpid 16, needs work mode 0 in same frame
                payload[WORK_MODE] = 0
                payload[COLOR_HEX] = "%04x%04xffff" % (
                    max(0, min(360, round(hs_color[0]))),
                    max(0, min(1000, round(hs_color[1] * 10))),
                )
            else:
                # Preserve the original conversion for ordinary DPID 5/6 lights.
                r, g, b = color_hs_to_RGB(*hs_color)
                hs_color = color_RGB_to_hs(r, g, b)
                # Convert HA's Hue 0-360, Sat 0-100 to device's Hue 0-360, Sat 0-1000
                payload[HUE] = round(hs_color[0])
                payload[SAT] = round(hs_color[1] * 10)

        if ATTR_COLOR_TEMP_KELVIN in kwargs and TEMP in self.coordinator.device.dpid:
            ha_kelvin = kwargs[ATTR_COLOR_TEMP_KELVIN]
            normalized_val = (ha_kelvin - self._min_kelvin) / (self._max_kelvin - self._min_kelvin)
            payload[TEMP] = round(max(0, min(1000, normalized_val * 1000)))

        if await self.coordinator.device.async_set_state(payload):
            if (
                TEMP in payload
                and COLOR_HEX not in payload
                and (
                    self.coordinator.device.pid == "v3sjfa"
                    or self._dreamcolor_white_value() is not None
                )
            ):
                self.coordinator.data.pop(COLOR_HEX, None)
            self.coordinator.data.update(payload)
            self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        payload = {SWITCH: 0}
        if await self.coordinator.device.async_set_state(payload):
            self.coordinator.data.update(payload)
            self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()
