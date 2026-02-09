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
    
    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return CozyLifeOptionsFlow(config_entry)

    async def _async_create_entry_from_ip(self, ip_address: str) -> FlowResult:
        """Helper to create a config entry from a single IP address."""
        errors: Dict[str, str] = {}
        try:
            device = CozyLifeDevice(ip_address)
            if not await device.async_update_device_info():
                 errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(device.device_id)
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=device.device_model_name or ip_address,
                    data={"ip_address": ip_address, "device_id": device.device_id}
                )
        except asyncio.TimeoutError:
            errors["base"] = "timeout_connect"
        except ConnectionRefusedError:
            errors["base"] = "cannot_connect"
        except Exception as e:
            _LOGGER.exception("Unexpected error during CozyLife device setup")
            errors["base"] = "unknown"
        
        return self.async_show_form(
            step_id="user", data_schema=DATA_SCHEMA, errors=errors # Re-show user form on error
        )

class CozyLifeOptionsFlow(config_entries.OptionsFlow):
    """Handle CozyLife options."""

    def __init__(self, config_entry: config_entries.ConfigEntry):
        """Initialize CozyLife options flow."""
        self._config_entry = config_entry

    async def async_step_init(self, user_input: Optional[Dict[str, Any]] = None) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Optional(
                    "enable_debug",
                    default=self._config_entry.options.get("enable_debug", False)
                ): bool,
            })
        )
