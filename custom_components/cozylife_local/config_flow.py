import logging
import asyncio
import ipaddress
from typing import Any, Dict, Optional

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult

from .const import DOMAIN, LIGHT_TYPE_CODE, RGB_LIGHT_TYPE_CODE
from .cozylife_api import CozyLifeDevice

_LOGGER = logging.getLogger(__name__)

DATA_SCHEMA = vol.Schema({
    vol.Required("ip_address", description={"suggested_value": "192.168.1.100"}): str,
    vol.Optional("skip_validation", default=False): bool,
})

DATA_SCHEMA_LIGHT = vol.Schema({
    vol.Required("ip_address", description={"suggested_value": "192.168.1.100"}): str,
    vol.Optional("min_kelvin", default=2000): vol.All(int, vol.Range(min=1000, max=10000)),
    vol.Optional("max_kelvin", default=6500): vol.All(int, vol.Range(min=1000, max=10000)),
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
                await self.async_set_unique_id(ip_address)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"CozyLife Dev @ {ip_address}",
                    data={
                        "ip_address": ip_address,
                        "min_kelvin": user_input.get("min_kelvin", 2000),
                        "max_kelvin": user_input.get("max_kelvin", 6500),
                    }
                )
            else:
                return await self._async_create_entry_from_ip(
                    ip_address,
                    user_input.get("min_kelvin", 2000),
                    user_input.get("max_kelvin", 6500),
                    user_input,
                )

        return self.async_show_form(
            step_id="user", data_schema=DATA_SCHEMA, errors=errors
        )
    
    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return CozyLifeOptionsFlow(config_entry)

    async def _async_create_entry_from_ip(self, ip_address: str, min_kelvin: int, max_kelvin: int, user_input: Dict[str, Any]) -> FlowResult:
        """Helper to create a config entry from a single IP address."""
        errors: Dict[str, str] = {}
        try:
            device = CozyLifeDevice(ip_address)
            if not await device.async_update_device_info():
                 errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(device.device_id)
                self._abort_if_unique_id_configured()

                is_light = device.device_type_code in [LIGHT_TYPE_CODE, RGB_LIGHT_TYPE_CODE]

                # If this is a light and kelvin fields weren't provided yet, re-show with light schema
                if is_light and "min_kelvin" not in user_input:
                    self._ip_address = ip_address
                    self._device_id = device.device_id
                    self._device_model_name = device.device_model_name
                    return self.async_show_form(
                        step_id="user",
                        data_schema=DATA_SCHEMA_LIGHT,
                        errors={},
                        description_placeholders={"ip_address": ip_address},
                    )

                return self.async_create_entry(
                    title=device.device_model_name or ip_address,
                    data={
                        "ip_address": ip_address,
                        "device_id": device.device_id,
                        "device_type_code": device.device_type_code,
                        "min_kelvin": min_kelvin if is_light else 2000,
                        "max_kelvin": max_kelvin if is_light else 6500,
                    }
                )
        except asyncio.TimeoutError:
            errors["base"] = "timeout_connect"
        except ConnectionRefusedError:
            errors["base"] = "cannot_connect"
        except Exception as e:
            _LOGGER.exception("Unexpected error during CozyLife device setup")
            errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user", data_schema=DATA_SCHEMA, errors=errors
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

        is_light = self._config_entry.data.get("device_type_code") in [LIGHT_TYPE_CODE, RGB_LIGHT_TYPE_CODE]

        schema_fields: Dict[Any, Any] = {}
        if is_light:
            schema_fields[vol.Optional("min_kelvin", default=self._config_entry.data.get("min_kelvin", 2000))] = vol.All(int, vol.Range(min=1000, max=10000))
            schema_fields[vol.Optional("max_kelvin", default=self._config_entry.data.get("max_kelvin", 6500))] = vol.All(int, vol.Range(min=1000, max=10000))
        schema_fields[vol.Optional("enable_debug", default=self._config_entry.options.get("enable_debug", False))] = bool

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(schema_fields),
        )
