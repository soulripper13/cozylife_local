import logging
import asyncio
import ipaddress
from typing import Any, Dict, Optional

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult

from .const import (
    DOMAIN,
    DEFAULT_MAX_KELVIN,
    DEFAULT_MIN_KELVIN,
    DEFAULT_SENSOR_REPORT_INTERVAL,
    LIGHT_KELVIN_RANGES,
    MIN_SENSOR_REPORT_INTERVAL,
    STANDARD_SENSOR_REPORT_INTERVAL,
    SENSOR_BATTERY,
    SENSOR_HUMIDITY,
    SENSOR_HUMIDITY_SENSITIVITY_DPID,
    SENSOR_REPORT_INTERVAL_DPID,
    SENSOR_TEMPERATURE,
    SENSOR_TEMP_SENSITIVITY_DPID,
    SENSOR_TYPE_CODE,
)
from .cozylife_api import CozyLifeDevice
from .discovery import async_load_model_catalog, classify_device
from .network_discovery import (
    AUTO_NETWORK,
    DiscoveredDevice,
    NetworkScanTooLarge,
    NoNetworkAvailable,
    async_discover_devices,
)

_LOGGER = logging.getLogger(__name__)

DATA_SCHEMA = vol.Schema({
    vol.Optional("ip_address", default="", description={"suggested_value": "192.168.1.100"}): str,
    vol.Optional("network_cidr", default=AUTO_NETWORK): str,
    vol.Optional("sleeping_sensor", default=False): bool,
    vol.Optional("skip_validation", default=False): bool,
})

SLEEPING_SENSOR_PID = "Z4tRml"
SLEEPING_SENSOR_DPIDS = [
    SENSOR_HUMIDITY,
    SENSOR_TEMPERATURE,
    SENSOR_BATTERY,
    SENSOR_REPORT_INTERVAL_DPID,
    SENSOR_HUMIDITY_SENSITIVITY_DPID,
    SENSOR_TEMP_SENSITIVITY_DPID,
]

EXPERIMENTAL_SHORT_INTERVAL_KEY = "experimental_short_interval"

def _report_interval_schema() -> vol.All:
    """Return report interval validation for sleeping environment sensors."""
    return vol.All(int, vol.Range(min=MIN_SENSOR_REPORT_INTERVAL, max=3600))


def _normalize_report_interval(report_interval: int, experimental: bool) -> int:
    """Clamp report interval to the supported range for the selected mode."""
    minimum = (
        MIN_SENSOR_REPORT_INTERVAL
        if experimental
        else STANDARD_SENSOR_REPORT_INTERVAL
    )
    return min(3600, max(minimum, int(report_interval)))


def _experimental_short_interval_default(
    config_entry: config_entries.ConfigEntry,
) -> bool:
    """Return whether experimental short intervals are enabled."""
    return bool(
        config_entry.options.get(
            EXPERIMENTAL_SHORT_INTERVAL_KEY,
            config_entry.data.get(EXPERIMENTAL_SHORT_INTERVAL_KEY, False),
        )
    )


def _kelvin_range_for_pid(pid: str | None) -> tuple[int, int]:
    """Return the default Kelvin range for a light model."""
    return LIGHT_KELVIN_RANGES.get(
        pid or "",
        (DEFAULT_MIN_KELVIN, DEFAULT_MAX_KELVIN),
    )


def _device_schema_light(ip_address: str, pid: str | None = None) -> vol.Schema:
    """Return a light setup schema with the selected IP carried forward."""
    min_kelvin, max_kelvin = _kelvin_range_for_pid(pid)
    return vol.Schema({
        vol.Required("ip_address", default=ip_address): str,
        vol.Optional("min_kelvin", default=min_kelvin): vol.All(int, vol.Range(min=1000, max=10000)),
        vol.Optional("max_kelvin", default=max_kelvin): vol.All(int, vol.Range(min=1000, max=10000)),
        vol.Optional("skip_validation", default=False): bool,
    })


def _device_schema_sensor(ip_address: str) -> vol.Schema:
    """Return an environment sensor setup schema with the selected IP carried forward."""
    return vol.Schema({
        vol.Required("ip_address", default=ip_address): str,
        vol.Optional(EXPERIMENTAL_SHORT_INTERVAL_KEY, default=False): bool,
        vol.Optional(
            "report_interval",
            default=DEFAULT_SENSOR_REPORT_INTERVAL,
        ): _report_interval_schema(),
        vol.Optional("skip_validation", default=False): bool,
    })


def _sleeping_sensor_schema(ip_address: str) -> vol.Schema:
    """Return sleeping environment sensor setup schema."""
    return vol.Schema({
        vol.Required("ip_address", default=ip_address): str,
        vol.Optional("sleeping_sensor", default=True): bool,
        vol.Optional(EXPERIMENTAL_SHORT_INTERVAL_KEY, default=False): bool,
        vol.Optional(
            "report_interval",
            default=DEFAULT_SENSOR_REPORT_INTERVAL,
        ): _report_interval_schema(),
    })


def _report_interval_default(config_entry: config_entries.ConfigEntry) -> int:
    """Return a valid report interval default for the options form."""
    experimental = _experimental_short_interval_default(config_entry)
    return _normalize_report_interval(
        int(
            config_entry.options.get(
                "report_interval",
                config_entry.data.get("report_interval", DEFAULT_SENSOR_REPORT_INTERVAL),
            )
        ),
        experimental,
    )

class CozyLifeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for CozyLife integration."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_POLL

    async def async_step_user(self, user_input: Optional[Dict[str, Any]] = None) -> FlowResult:
        """Handle the initial step."""
        errors: Dict[str, str] = {}

        if user_input is not None:
            ip_address = user_input.get("ip_address", "").strip()
            network_cidr = user_input.get("network_cidr", AUTO_NETWORK)
            sleeping_sensor = user_input.get("sleeping_sensor", False)
            skip_validation = user_input.get("skip_validation", False)

            if not ip_address:
                if sleeping_sensor:
                    errors["base"] = "ip_required_for_sleeping_sensor"
                    return self.async_show_form(
                        step_id="user",
                        data_schema=DATA_SCHEMA,
                        errors=errors,
                    )
                if skip_validation:
                    errors["base"] = "ip_required_for_skip_validation"
                    return self.async_show_form(
                        step_id="user",
                        data_schema=DATA_SCHEMA,
                        errors=errors,
                    )
                return await self._async_discover_devices(network_cidr)

            try:
                ipaddress.ip_address(ip_address)
            except ValueError:
                errors["ip_address"] = "invalid_ip"
                return self.async_show_form(
                    step_id="user",
                    data_schema=DATA_SCHEMA,
                    errors=errors,
                )

            if self._is_device_configured(ip_address):
                return self.async_abort(reason="already_configured")

            if sleeping_sensor:
                if "report_interval" not in user_input:
                    return self.async_show_form(
                        step_id="user",
                        data_schema=_sleeping_sensor_schema(ip_address),
                        errors={},
                        description_placeholders={"ip_address": ip_address},
                    )
                return await self._async_create_sleeping_sensor_entry(
                    ip_address,
                    user_input,
                )

            if skip_validation:
                _LOGGER.info(f"Skipping validation for {ip_address} as requested for development.")
                await self.async_set_unique_id(ip_address)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"CozyLife Dev @ {ip_address}",
                    data={
                        "ip_address": ip_address,
                        "min_kelvin": user_input.get("min_kelvin", DEFAULT_MIN_KELVIN),
                        "max_kelvin": user_input.get("max_kelvin", DEFAULT_MAX_KELVIN),
                        "report_interval": user_input.get("report_interval", DEFAULT_SENSOR_REPORT_INTERVAL),
                    }
                )
            else:
                return await self._async_create_entry_from_ip(
                    ip_address,
                    user_input.get("min_kelvin", DEFAULT_MIN_KELVIN),
                    user_input.get("max_kelvin", DEFAULT_MAX_KELVIN),
                    user_input.get("report_interval", DEFAULT_SENSOR_REPORT_INTERVAL),
                    user_input,
                )

        return self.async_show_form(
            step_id="user", data_schema=DATA_SCHEMA, errors=errors
        )

    async def _async_create_sleeping_sensor_entry(
        self,
        ip_address: str,
        user_input: Dict[str, Any],
    ) -> FlowResult:
        """Create a temp/humidity sensor entry without waking the device."""
        device_id = f"sleeping_sensor_{ip_address}"
        await self.async_set_unique_id(device_id)
        self._abort_if_unique_id_configured()

        return self.async_create_entry(
            title=f"CozyLife Temp/Humidity @ {ip_address}",
            data={
                "ip_address": ip_address,
                "device_id": device_id,
                "pid": SLEEPING_SENSOR_PID,
                "device_type_code": SENSOR_TYPE_CODE,
                "dpids": SLEEPING_SENSOR_DPIDS,
                EXPERIMENTAL_SHORT_INTERVAL_KEY: user_input.get(
                    EXPERIMENTAL_SHORT_INTERVAL_KEY,
                    False,
                ),
                "report_interval": _normalize_report_interval(
                    user_input.get(
                        "report_interval",
                        DEFAULT_SENSOR_REPORT_INTERVAL,
                    ),
                    user_input.get(EXPERIMENTAL_SHORT_INTERVAL_KEY, False),
                ),
            },
        )

    async def async_step_select_device(
        self,
        user_input: Optional[Dict[str, Any]] = None,
    ) -> FlowResult:
        """Let the user choose one of the discovered CozyLife devices."""
        discovered_devices = getattr(self, "_discovered_devices", {})
        if not discovered_devices:
            return self.async_show_form(
                step_id="user",
                data_schema=DATA_SCHEMA,
                errors={"base": "no_devices_found"},
            )

        if user_input is not None:
            ip_address = user_input["device"]
            device = discovered_devices.get(ip_address)
            if self._is_device_configured(
                ip_address,
                device.device_id if device else None,
            ):
                return self.async_abort(reason="already_configured")

            return await self._async_create_entry_from_ip(
                ip_address,
                DEFAULT_MIN_KELVIN,
                DEFAULT_MAX_KELVIN,
                DEFAULT_SENSOR_REPORT_INTERVAL,
                {"ip_address": ip_address},
            )

        return self._show_discovered_devices_form(discovered_devices)
    
    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return CozyLifeOptionsFlow(config_entry)

    async def _async_discover_devices(self, network_cidr: str) -> FlowResult:
        """Scan the network and show discovered CozyLife devices."""
        errors: Dict[str, str] = {}
        try:
            devices = await async_discover_devices(self.hass, network_cidr)
        except NetworkScanTooLarge:
            errors["base"] = "network_too_large"
        except NoNetworkAvailable:
            errors["base"] = "no_network"
        except ValueError:
            errors["base"] = "invalid_network"
        except Exception:
            _LOGGER.exception("Unexpected error during CozyLife network discovery")
            errors["base"] = "unknown"

        if errors:
            return self.async_show_form(
                step_id="user",
                data_schema=DATA_SCHEMA,
                errors=errors,
            )

        discovered_devices = {
            device.ip_address: device for device in devices
        }
        self._add_configured_devices_to_discovery(discovered_devices)

        if not discovered_devices:
            return self.async_show_form(
                step_id="user",
                data_schema=DATA_SCHEMA,
                errors={"base": "no_devices_found"},
            )

        self._discovered_devices = discovered_devices
        return self._show_discovered_devices_form(self._discovered_devices)

    def _show_discovered_devices_form(
        self,
        discovered_devices: Dict[str, Any],
    ) -> FlowResult:
        """Show a selector for devices found by network discovery."""
        configured_ips, configured_device_ids = self._configured_device_keys()
        device_options = {
            ip_address: (
                f"{device.label} - already added"
                if (
                    ip_address in configured_ips
                    or (
                        device.device_id is not None
                        and device.device_id in configured_device_ids
                    )
                )
                else device.label
            )
            for ip_address, device in discovered_devices.items()
        }
        return self.async_show_form(
            step_id="select_device",
            data_schema=vol.Schema({vol.Required("device"): vol.In(device_options)}),
            errors={},
            description_placeholders={
                "count": str(len(discovered_devices)),
            },
        )

    def _add_configured_devices_to_discovery(
        self,
        discovered_devices: Dict[str, DiscoveredDevice],
    ) -> None:
        """Include existing CozyLife entries in the discovery selector."""
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            ip_address = entry.data.get("ip_address")
            if not ip_address or ip_address in discovered_devices:
                continue

            dpids = entry.data.get("dpids") or ()
            discovered_devices[ip_address] = DiscoveredDevice(
                ip_address=ip_address,
                device_id=entry.data.get("device_id"),
                pid=entry.data.get("pid"),
                device_type_code=entry.data.get("device_type_code"),
                device_model_name=entry.title or "CozyLife Device",
                dpids=tuple(str(dpid) for dpid in dpids),
            )

    def _configured_device_keys(self) -> tuple[set[str], set[str]]:
        """Return configured IP addresses and device IDs for this integration."""
        configured_ips: set[str] = set()
        configured_device_ids: set[str] = set()

        for entry in self.hass.config_entries.async_entries(DOMAIN):
            if ip_address := entry.data.get("ip_address"):
                configured_ips.add(ip_address)
            if device_id := entry.data.get("device_id"):
                configured_device_ids.add(device_id)
            if entry.unique_id:
                configured_device_ids.add(entry.unique_id)

        return configured_ips, configured_device_ids

    def _is_device_configured(
        self,
        ip_address: str | None,
        device_id: str | None = None,
    ) -> bool:
        """Return true if an IP address or device ID is already configured."""
        configured_ips, configured_device_ids = self._configured_device_keys()
        return (
            ip_address in configured_ips
            or (
                device_id is not None
                and device_id in configured_device_ids
            )
        )

    async def _async_create_entry_from_ip(
        self,
        ip_address: str,
        min_kelvin: int,
        max_kelvin: int,
        report_interval: int,
        user_input: Dict[str, Any],
    ) -> FlowResult:
        """Helper to create a config entry from a single IP address."""
        errors: Dict[str, str] = {}
        try:
            await async_load_model_catalog(self.hass)
            device = CozyLifeDevice(ip_address)
            if not await device.async_update_device_info():
                 errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(device.device_id)
                self._abort_if_unique_id_configured()

                dpids = device.dpid or []
                classification = classify_device(device.pid, device.device_type_code, dpids)
                is_sensor = classification.is_environment_sensor
                is_light = classification.is_light
                if is_light:
                    model_min_kelvin, model_max_kelvin = _kelvin_range_for_pid(device.pid)
                    if (
                        "min_kelvin" not in user_input
                        or min_kelvin == DEFAULT_MIN_KELVIN
                    ):
                        min_kelvin = model_min_kelvin
                    if (
                        "max_kelvin" not in user_input
                        or max_kelvin == DEFAULT_MAX_KELVIN
                    ):
                        max_kelvin = model_max_kelvin

                # If this is a light and kelvin fields weren't provided yet, re-show with light schema
                if is_light and "min_kelvin" not in user_input:
                    self._ip_address = ip_address
                    self._device_id = device.device_id
                    self._device_model_name = device.device_model_name
                    return self.async_show_form(
                        step_id="user",
                        data_schema=_device_schema_light(ip_address, device.pid),
                        errors={},
                        description_placeholders={"ip_address": ip_address},
                    )

                # If this is a sensor and report_interval wasn't provided yet, re-show with sensor schema
                if is_sensor and "report_interval" not in user_input:
                    self._ip_address = ip_address
                    self._device_id = device.device_id
                    self._device_model_name = device.device_model_name
                    return self.async_show_form(
                        step_id="user",
                        data_schema=_device_schema_sensor(ip_address),
                        errors={},
                        description_placeholders={"ip_address": ip_address},
                    )

                return self.async_create_entry(
                    title=device.device_model_name or ip_address,
                    data={
                        "ip_address": ip_address,
                        "device_id": device.device_id,
                        "pid": device.pid,
                        "device_type_code": device.device_type_code,
                        "dpids": dpids,
                        "min_kelvin": (
                            min_kelvin if is_light else DEFAULT_MIN_KELVIN
                        ),
                        "max_kelvin": (
                            max_kelvin if is_light else DEFAULT_MAX_KELVIN
                        ),
                        EXPERIMENTAL_SHORT_INTERVAL_KEY: (
                            user_input.get(EXPERIMENTAL_SHORT_INTERVAL_KEY, False)
                            if is_sensor
                            else False
                        ),
                        "report_interval": (
                            _normalize_report_interval(
                                report_interval,
                                user_input.get(EXPERIMENTAL_SHORT_INTERVAL_KEY, False),
                            )
                            if is_sensor
                            else DEFAULT_SENSOR_REPORT_INTERVAL
                        ),
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
            dpids = self._config_entry.data.get("dpids") or []
            classification = classify_device(
                self._config_entry.data.get("pid", ""),
                self._config_entry.data.get("device_type_code"),
                dpids,
            )
            if classification.is_environment_sensor:
                user_input = dict(user_input)
                user_input["report_interval"] = _normalize_report_interval(
                    user_input.get(
                        "report_interval",
                        DEFAULT_SENSOR_REPORT_INTERVAL,
                    ),
                    user_input.get(EXPERIMENTAL_SHORT_INTERVAL_KEY, False),
                )
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
        is_sensor = classification.is_environment_sensor
        is_light = classification.is_light

        schema_fields: Dict[Any, Any] = {}
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
        if is_sensor:
            experimental = _experimental_short_interval_default(self._config_entry)
            schema_fields[
                vol.Optional(
                    EXPERIMENTAL_SHORT_INTERVAL_KEY,
                    default=experimental,
                )
            ] = bool
            schema_fields[
                vol.Optional(
                    "report_interval",
                    default=_report_interval_default(self._config_entry),
                )
            ] = _report_interval_schema()
            schema_fields[vol.Optional("temp_sensitivity", default=self._config_entry.options.get("temp_sensitivity", 5))] = vol.All(int, vol.Range(min=5, max=30))
            schema_fields[vol.Optional("humidity_sensitivity", default=self._config_entry.options.get("humidity_sensitivity", 5))] = vol.All(int, vol.Range(min=5, max=30))
        schema_fields[vol.Optional("enable_debug", default=self._config_entry.options.get("enable_debug", False))] = bool

        return vol.Schema(schema_fields)
