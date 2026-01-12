import logging
import asyncio
import ipaddress
from typing import Any, Dict, Optional

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult

from .const import DOMAIN
from .cozylife_api import CozyLifeDevice

_LOGGER = logging.getLogger(__name__)

DATA_SCHEMA = vol.Schema({
    vol.Required("ip_address", description={"suggested_value": "192.168.1.100"}): str,
    vol.Optional("skip_validation", default=False): bool,
})

class CozyLifeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for CozyLife integration."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_POLL

    async def async_step_user(self, user_input: Optional[Dict[str, Any]] = None) -> FlowResult:
        """Handle the initial step - asking for a single IP."""
        errors: Dict[str, str] = {}

        if user_input is not None:
            ip_address = user_input["ip_address"]
            skip_validation = user_input.get("skip_validation", False)

            if skip_validation:
                _LOGGER.info(f"Skipping validation for {ip_address} as requested for development.")
                # For development, use IP as unique ID. This is not robust for production
                # as IPs can change, but it's a workaround for remote setup.
                await self.async_set_unique_id(ip_address)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"CozyLife Dev @ {ip_address}",
                    data={"ip_address": ip_address} # No device_id yet
                )
            else:
                return await self._async_create_entry_from_ip(ip_address)
        
        return self.async_show_form(
            step_id="user", data_schema=DATA_SCHEMA, errors=errors
        )
    
    async def _async_create_entry_from_ip(self, ip_address: str) -> FlowResult:
        """Helper to create a config entry from a single IP address."""
        errors: Dict[str, str] = {}
        try:
            _LOGGER.warning(f"🔍 Validating CozyLife device at {ip_address}...")
            device = CozyLifeDevice(ip_address)
            if not await device.async_update_device_info():
                 _LOGGER.error(f"❌ Cannot connect to device at {ip_address}. Check IP address and network connectivity.")
                 errors["base"] = "cannot_connect"
            else:
                _LOGGER.warning(f"✅ Device validated: {device.device_model_name or 'Unknown'} (DID: {device.device_id}, PID: {device.pid})")
                await self.async_set_unique_id(device.device_id)
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=device.device_model_name or ip_address,
                    data={"ip_address": ip_address, "device_id": device.device_id}
                )
        except asyncio.TimeoutError:
            _LOGGER.error(f"❌ Timeout connecting to {ip_address}. Device may be offline or unreachable.")
            errors["base"] = "timeout_connect"
        except ConnectionRefusedError:
            _LOGGER.error(f"❌ Connection refused to {ip_address}. Check if device is powered on.")
            errors["base"] = "cannot_connect"
        except Exception as e:
            _LOGGER.exception(f"❌ Unexpected error during CozyLife device setup at {ip_address}")
            errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user", data_schema=DATA_SCHEMA, errors=errors # Re-show user form on error
        )

class CozyLifeOptionsFlow(config_entries.OptionsFlow):
    """Handle CozyLife options."""

    def __init__(self, config_entry: config_entries.ConfigEntry):
        """Initialize CozyLife options flow."""
        self.config_entry = config_entry

    async def async_step_init(self, user_input: Optional[Dict[str, Any]] = None) -> FlowResult:
        """Manage the options."""
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
            })
        )
