import asyncio
import logging
from datetime import timedelta
from typing import Any, Dict, Optional

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .cozylife_api import CozyLifeDevice
from .const import SENSOR_TEMPERATURE, SENSOR_BATTERY, SWITCH, KNOWN_SENSOR_PIDS, SENSOR_REPORT_INTERVAL_DPID, DEFAULT_SENSOR_REPORT_INTERVAL, SENSOR_TEMP_SENSITIVITY_DPID, SENSOR_HUMIDITY_SENSITIVITY_DPID

_LOGGER = logging.getLogger(__name__)

UPDATE_INTERVAL = timedelta(seconds=30)
SENSOR_UPDATE_INTERVAL = timedelta(seconds=5)  # Sensor port 5555 open window is ~10s; poll frequently to catch it


class CozyLifeCoordinator(DataUpdateCoordinator[Dict[str, Any]]):
    """Manages fetching data from a single CozyLife device."""

    def __init__(self, hass: HomeAssistant, device: CozyLifeDevice, entry: ConfigEntry):
        """Initialize coordinator."""
        # Use PID as primary sensor discriminator; DPID pattern as fallback
        dpid = device.dpid or []
        self._is_sensor = (
            device.pid in KNOWN_SENSOR_PIDS
            or (
                SENSOR_TEMPERATURE in dpid
                and SENSOR_BATTERY in dpid
                and SWITCH not in dpid
            )
        )
        interval = SENSOR_UPDATE_INTERVAL if self._is_sensor else UPDATE_INTERVAL

        super().__init__(
            hass,
            _LOGGER,
            name=f"CozyLife device {device.ip_address}",
            update_interval=interval,
            config_entry=entry,
        )
        self.device = device
        self._report_interval = entry.options.get("report_interval", entry.data.get("report_interval", DEFAULT_SENSOR_REPORT_INTERVAL))
        self._temp_sensitivity = entry.options.get("temp_sensitivity", None)
        self._humidity_sensitivity = entry.options.get("humidity_sensitivity", None)

    @property
    def is_sensor(self) -> bool:
        return self._is_sensor

    async def _async_update_data(self) -> Dict[str, Any]:
        """Fetch data from device."""
        try:
            if not self.device.device_id or not self.device.dpid:
                if not await self.device.async_update_device_info():
                    raise UpdateFailed(f"Failed to get full device info for {self.device.ip_address}")

            _LOGGER.debug(f"[COZYLIFE] Polling device {self.device.ip_address} for state update...")

            state_data = await self.device.async_get_state()
            if state_data is None:
                raise UpdateFailed(f"Failed to query state from device {self.device.ip_address}")

            # Check if report interval needs to be pushed (first connection or reverted by device)
            if self._is_sensor and state_data.get(SENSOR_REPORT_INTERVAL_DPID) != self._report_interval:
                _LOGGER.debug(f"[COZYLIFE] Report interval is {state_data.get(SENSOR_REPORT_INTERVAL_DPID)}, setting to {self._report_interval}s for {self.device.ip_address}")
                await self.device.async_set_state({SENSOR_REPORT_INTERVAL_DPID: self._report_interval})

            # Push sensitivity settings if configured and different from device values
            if self._is_sensor:
                updates = {}
                if self._temp_sensitivity is not None and state_data.get(SENSOR_TEMP_SENSITIVITY_DPID) != self._temp_sensitivity:
                    updates[SENSOR_TEMP_SENSITIVITY_DPID] = self._temp_sensitivity
                if self._humidity_sensitivity is not None and state_data.get(SENSOR_HUMIDITY_SENSITIVITY_DPID) != self._humidity_sensitivity:
                    updates[SENSOR_HUMIDITY_SENSITIVITY_DPID] = self._humidity_sensitivity
                if updates:
                    _LOGGER.debug(f"[COZYLIFE] Pushing sensitivity settings {updates} to {self.device.ip_address}")
                    await self.device.async_set_state(updates)

            _LOGGER.debug(f"[COZYLIFE] Successfully fetched state for {self.device.ip_address}: {state_data}")
            return state_data

        except UpdateFailed:
            # For sleeping sensors: if we have previous data, return it to keep entities available.
            # The device only wakes periodically so connection failures are expected.
            if self._is_sensor and self.data is not None:
                _LOGGER.debug(
                    f"[COZYLIFE] Sensor {self.device.ip_address} unreachable (sleeping), "
                    f"retaining last known state."
                )
                return self.data if self.data else {}
            raise

        except Exception as err:
            _LOGGER.error(f"Error communicating with device {self.device.ip_address}: {err}")
            raise UpdateFailed(f"Error communicating with device {self.device.ip_address}: {err}") from err

