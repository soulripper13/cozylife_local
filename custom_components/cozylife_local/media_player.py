"""Media-player entities for APK-verified CozyLife Wi-Fi speakers."""

from __future__ import annotations

from typing import Any

from homeassistant.components.media_player import (
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, SWITCH
from .coordinator import CozyLifeCoordinator

_SOURCES = ("Bluetooth", "Local playback", "Line-in")

# The downloaded X7 panel uses DPID 3/4 on its original Wi-Fi profile and
# DPID 17/18 on the later profile. Runtime state wins when it is available,
# which also handles a vendor firmware reporting both layouts.
_SPEAKER_PROFILES = {
    "GriFPh": ("3", "4"),
    "ZhTBTj": ("17", "18"),
}


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities,
) -> None:
    """Set up a CozyLife speaker when its APK catalog profile matches."""
    coordinator: CozyLifeCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    if coordinator.classification.is_speaker:
        async_add_entities([CozyLifeSpeaker(coordinator)])


class CozyLifeSpeaker(
    CoordinatorEntity[CozyLifeCoordinator], MediaPlayerEntity
):
    """A local X7 speaker controlled through its verified CozyLife DPIDs."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_supported_features = (
        MediaPlayerEntityFeature.TURN_ON
        | MediaPlayerEntityFeature.TURN_OFF
        | MediaPlayerEntityFeature.VOLUME_SET
        | MediaPlayerEntityFeature.PLAY
        | MediaPlayerEntityFeature.PAUSE
        | MediaPlayerEntityFeature.SELECT_SOURCE
    )

    def __init__(self, coordinator: CozyLifeCoordinator) -> None:
        """Initialize the speaker entity."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.device.device_id}_speaker"
        self._attr_source_list = list(_SOURCES)

    @property
    def device_info(self) -> DeviceInfo:
        """Describe the parent CozyLife device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.device.device_id)},
            name=self.coordinator.device.device_model_name,
            manufacturer="CozyLife",
            model=self.coordinator.device.pid,
        )

    @property
    def _control_dpids(self) -> tuple[str, str]:
        """Return the reported volume/playback DPID layout."""
        default = _SPEAKER_PROFILES.get(self.coordinator.device.pid, ("3", "4"))
        data = self.coordinator.data
        volume_dpid = "17" if "17" in data else default[0]
        playback_dpid = "18" if "18" in data else default[1]
        return volume_dpid, playback_dpid

    @staticmethod
    def _is_enabled(value: Any) -> bool:
        """Normalize the integer/boolean values used by the panel."""
        return value not in (0, "0", False, "off", "false", None)

    @property
    def state(self) -> MediaPlayerState | None:
        """Return off, playing, or paused from the panel's state DPIDs."""
        if SWITCH not in self.coordinator.data:
            return None
        if not self._is_enabled(self.coordinator.data[SWITCH]):
            return MediaPlayerState.OFF
        _volume_dpid, playback_dpid = self._control_dpids
        if playback_dpid not in self.coordinator.data:
            return MediaPlayerState.ON
        return (
            MediaPlayerState.PLAYING
            if self._is_enabled(self.coordinator.data[playback_dpid])
            else MediaPlayerState.PAUSED
        )

    @property
    def volume_level(self) -> float | None:
        """Convert the panel's 0-1000 volume value to Home Assistant's scale."""
        volume_dpid, _playback_dpid = self._control_dpids
        raw = self.coordinator.data.get(volume_dpid)
        if raw is None:
            return None
        try:
            return max(0.0, min(1.0, float(raw) / 1000))
        except (TypeError, ValueError):
            return None

    @property
    def source(self) -> str | None:
        """Return the current source advertised by DPID 6."""
        raw = self.coordinator.data.get("6")
        try:
            index = int(raw)
        except (TypeError, ValueError, OverflowError):
            return None
        if isinstance(raw, bool) or not 0 <= index < len(_SOURCES):
            return None
        if isinstance(raw, float) and raw != index:
            return None
        return _SOURCES[index]

    async def _async_set(self, payload: dict[str, Any]) -> None:
        """Write one verified state change and update the displayed state."""
        if await self.coordinator.device.async_set_state(payload):
            self.coordinator.data.update(payload)
            self.async_write_ha_state()

    async def async_turn_on(self) -> None:
        """Power on the speaker."""
        await self._async_set({SWITCH: 1})

    async def async_turn_off(self) -> None:
        """Power off the speaker."""
        await self._async_set({SWITCH: 0})

    async def async_media_play(self) -> None:
        """Start playback using the panel's verified playback DPID."""
        _volume_dpid, playback_dpid = self._control_dpids
        await self._async_set({playback_dpid: 1})

    async def async_media_pause(self) -> None:
        """Pause playback using the panel's verified playback DPID."""
        _volume_dpid, playback_dpid = self._control_dpids
        await self._async_set({playback_dpid: 0})

    async def async_set_volume_level(self, volume: float) -> None:
        """Set volume through the panel's 0-1000 raw-value control."""
        volume_dpid, _playback_dpid = self._control_dpids
        await self._async_set({volume_dpid: round(max(0.0, min(1.0, volume)) * 1000)})

    async def async_select_source(self, source: str) -> None:
        """Select Bluetooth, local playback, or line-in."""
        if source not in _SOURCES:
            return
        await self._async_set({"6": _SOURCES.index(source)})

    @callback
    def _handle_coordinator_update(self) -> None:
        """Write state after a local CozyLife update."""
        self.async_write_ha_state()
