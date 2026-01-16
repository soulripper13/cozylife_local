import asyncio
import logging
from datetime import timedelta
from typing import Any, Dict, Optional

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .cozylife_api import CozyLifeDevice

_LOGGER = logging.getLogger(__name__)

UPDATE_INTERVAL = timedelta(seconds=30)  # How often to poll the device

class CozyLifeCoordinator(DataUpdateCoordinator[Dict[str, Any]]):
    """Manages fetching data from a single CozyLife device."""

    def __init__(self, hass: HomeAssistant, device: CozyLifeDevice):
        """Initialize coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"CozyLife device {device.ip_address}",
            update_interval=UPDATE_INTERVAL,
        )
        self.device = device

    async def _async_update_data(self) -> Dict[str, Any]:
        """Fetch data from device."""
        try:
            # Update device info if not already done or if a refresh is needed.
            # This is now a fully local call.
            if not self.device.device_id or not self.device.dpid:
                if not await self.device.async_update_device_info():
                    raise UpdateFailed(f"Failed to get full device info for {self.device.ip_address}")

            # Query the current state of the device
            _LOGGER.debug(f"Polling device {self.device.ip_address} for state update...")
            state_data = await self.device.async_get_state()
            if state_data is None:
                raise UpdateFailed(f"Failed to query state from device {self.device.ip_address}")

            _LOGGER.debug(f"Successfully fetched state for {self.device.ip_address}: {state_data}")
            return state_data
        except Exception as err:
            _LOGGER.error(f"Error communicating with device {self.device.ip_address}: {err}")
            raise UpdateFailed(f"Error communicating with device {self.device.ip_address}: {err}") from err
