import asyncio
import logging
from datetime import timedelta
from typing import Any, Dict, Optional

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .cozylife_api import CozyLifeDevice
from .const import SENSOR_TEMPERATURE, SENSOR_BATTERY, SWITCH

_LOGGER = logging.getLogger(__name__)

UPDATE_INTERVAL = timedelta(seconds=30)
SENSOR_UPDATE_INTERVAL = timedelta(seconds=30)  # Poll frequently; sensor opens port 5555 briefly on wakeup every 30 min


class CozyLifeCoordinator(DataUpdateCoordinator[Dict[str, Any]]):
    """Manages fetching data from a single CozyLife device."""

    def __init__(self, hass: HomeAssistant, device: CozyLifeDevice, entry: ConfigEntry):
        """Initialize coordinator."""
        # Detect sensor by DPID pattern: has temp(8) + battery(9) but no switch(1)
        dpid = device.dpid or []
        self._is_sensor = (
            SENSOR_TEMPERATURE in dpid
            and SENSOR_BATTERY in dpid
            and SWITCH not in dpid
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

            # Sensors report stale (previous cycle) values on first query after wakeup.
            # Wait briefly then query again to get the freshly measured values.
            if self._is_sensor:
                await asyncio.sleep(2)
                fresh_data = await self.device.async_get_state()
                if fresh_data is not None:
                    state_data = fresh_data
                    _LOGGER.debug(f"[COZYLIFE] Second query got fresh sensor data for {self.device.ip_address}: {state_data}")

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

