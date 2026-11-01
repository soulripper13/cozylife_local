import logging
import ipaddress
from typing import Any, Dict, Optional

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .const import (
    DISCOVERY_CONTROLLER_ENTRY_TYPE,
    DOMAIN,
    DEFAULT_MAX_KELVIN,
    DEFAULT_MIN_KELVIN,
    LIGHT_KELVIN_RANGES,
)
from .discovery import (
    async_load_model_catalog,
    classify_device,
    get_model_info,
    supports_local_entities,
)

_LOGGER = logging.getLogger(__name__)

def _kelvin_range_for_pid(pid: str | None) -> tuple[int, int]:
    """Return the default Kelvin range for a light model."""
    return LIGHT_KELVIN_RANGES.get(
        pid or "",
        (DEFAULT_MIN_KELVIN, DEFAULT_MAX_KELVIN),
    )


class CozyLifeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for CozyLife integration."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_POLL

    async def async_step_user(self, user_input: Optional[Dict[str, Any]] = None) -> FlowResult:
        """Create the one persistent, no-fields discovery controller entry."""
        if user_input is None:
            return self.async_show_form(step_id="user", data_schema=vol.Schema({}))

        await self.async_set_unique_id(f"{DOMAIN}_discovery_controller")
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title="CozyLife Local",
            data={"entry_type": DISCOVERY_CONTROLLER_ENTRY_TYPE},
        )

    async def async_step_import(self, import_data: Dict[str, Any]) -> FlowResult:
        """Create the controller automatically when upgrading legacy entries."""
        if import_data.get("entry_type") != DISCOVERY_CONTROLLER_ENTRY_TYPE:
            return self.async_abort(reason="invalid_import")

        await self.async_set_unique_id(f"{DOMAIN}_discovery_controller")
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title="CozyLife Local",
            data={"entry_type": DISCOVERY_CONTROLLER_ENTRY_TYPE},
        )

    async def async_step_integration_discovery(
        self,
        discovery_info: Dict[str, Any],
    ) -> FlowResult:
        """Present an APK-announced Wi-Fi device for normal HA confirmation."""
        device_id = discovery_info.get("device_id")
        ip_address = discovery_info.get("ip_address")
        if not isinstance(device_id, str) or not isinstance(ip_address, str):
            return self.async_abort(reason="invalid_discovery")

        try:
            ipaddress.IPv4Address(ip_address)
        except ipaddress.AddressValueError:
            return self.async_abort(reason="invalid_discovery")

        await self.async_set_unique_id(device_id)
        self._abort_if_unique_id_configured()
        # Keep the UDP identity on this config-flow instance.  A sleeping
        # sensor may return to sleep before the user presses Add, so the
        # confirmation step must not depend on a second TCP handshake.
        self._udp_discovery_info = dict(discovery_info)
        return await self.async_step_confirm_discovery()

    async def _async_create_discovered_entry(
        self,
        discovery_info: Dict[str, Any],
    ) -> FlowResult:
        """Create a confirmed entry from its saved UDP announcement."""
        ip_address = discovery_info["ip_address"]
        device_id = discovery_info["device_id"]

        # The APK treats an announced DID/IP pair as the local identity. It
        # associates that identity with its catalogue profile, then refreshes
        # it asynchronously with cmd 2 / attr:[1]; it does not make a cmd 0
        # inventory reply a prerequisite to adding the device. Mirror that for
        # known Wi-Fi models for which this integration implements entities.
        pid = discovery_info.get("pid")
        if isinstance(pid, str) and pid:
            await async_load_model_catalog(self.hass)
            model = get_model_info(pid)
            device_type_code = discovery_info.get("device_type_code")
            if model is not None:
                classification = classify_device(
                    pid,
                    device_type_code or model.type_code,
                    model.dpids,
                )
                if classification.is_local_transport and supports_local_entities(
                    pid, classification
                ):
                    min_kelvin, max_kelvin = (
                        _kelvin_range_for_pid(pid)
                        if classification.is_light
                        else (DEFAULT_MIN_KELVIN, DEFAULT_MAX_KELVIN)
                    )
                    return self.async_create_entry(
                        title=model.model_name or "CozyLife device",
                        data={
                            "ip_address": ip_address,
                            "device_id": device_id,
                            "pid": pid,
                            "device_type_code": (
                                device_type_code or model.type_code
                            ),
                            "dpids": sorted(model.dpids),
                            "min_kelvin": min_kelvin,
                            "max_kelvin": max_kelvin,
                            "announced_via_udp": True,
                        },
                    )

        return self.async_abort(reason="unsupported_announced_model")

    async def async_step_confirm_discovery(
        self,
        user_input: Optional[Dict[str, Any]] = None,
    ) -> FlowResult:
        """Let Home Assistant's standard discovery card confirm a device."""
        discovery_info = getattr(self, "_udp_discovery_info", None)
        if not isinstance(discovery_info, dict):
            return self.async_abort(reason="invalid_discovery")

        if user_input is not None:
            return await self._async_create_discovered_entry(discovery_info)

        await async_load_model_catalog(self.hass)
        model = get_model_info(discovery_info.get("pid"))
        device_name = (
            model.model_name
            if model is not None and model.model_name
            else "CozyLife device"
        )
        return self.async_show_form(
            step_id="confirm_discovery",
            description_placeholders={
                "device": device_name,
                "ip_address": discovery_info["ip_address"],
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return CozyLifeOptionsFlow(config_entry)

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
            data_schema=self._options_schema(),
        )

    def _options_schema(self) -> vol.Schema:
        """Return the options schema for this config entry."""
        dpids = self._config_entry.data.get("dpids") or []
        pid = self._config_entry.data.get("pid", "")
        classification = classify_device(
            pid,
            self._config_entry.data.get("device_type_code"),
            dpids,
        )
        is_light = classification.is_light

        schema_fields: Dict[Any, Any] = {}
        if classification.is_environment_sensor:
            options = self._config_entry.options
            schema_fields[vol.Optional("filter_outliers", default=options.get("filter_outliers", False))] = bool
            schema_fields[vol.Optional("temperature_radius", default=options.get("temperature_radius", 2.0))] = vol.All(vol.Coerce(float), vol.Range(min=0.1, max=120))
            schema_fields[vol.Optional("humidity_radius", default=options.get("humidity_radius", 10.0))] = vol.All(vol.Coerce(float), vol.Range(min=0.1, max=100))
        if is_light:
            model_min_kelvin, model_max_kelvin = _kelvin_range_for_pid(pid)
            stored_min_kelvin = self._config_entry.data.get("min_kelvin", DEFAULT_MIN_KELVIN)
            stored_max_kelvin = self._config_entry.data.get("max_kelvin", DEFAULT_MAX_KELVIN)
            schema_fields[
                vol.Optional(
                    "min_kelvin",
                    default=(
                        model_min_kelvin
                        if stored_min_kelvin == DEFAULT_MIN_KELVIN
                        else stored_min_kelvin
                    ),
                )
            ] = vol.All(int, vol.Range(min=1000, max=10000))
            schema_fields[
                vol.Optional(
                    "max_kelvin",
                    default=(
                        model_max_kelvin
                        if stored_max_kelvin == DEFAULT_MAX_KELVIN
                        else stored_max_kelvin
                    ),
                )
            ] = vol.All(int, vol.Range(min=1000, max=10000))
        schema_fields[vol.Optional("enable_debug", default=self._config_entry.options.get("enable_debug", False))] = bool

        return vol.Schema(schema_fields)
