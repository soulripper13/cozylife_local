import logging
import asyncio
import ipaddress
from typing import Any, Dict, List, Optional

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult

from .const import DOMAIN
from .cozylife_api import CozyLifeDevice

_LOGGER = logging.getLogger(__name__)

IP_RANGE_SCHEMA = vol.Schema({
    vol.Required("ip_range", description={"suggested_value": "192.168.1.1-192.168.1.254"}): str,
    vol.Optional("skip_validation", default=False): bool,
})

# Temporary storage for discovered devices during the config flow
# Storing device_id -> CozyLifeDevice instance
_DISCOVERED_DEVICES: Dict[str, CozyLifeDevice] = {}

def _parse_ip_range(ip_range_str: str) -> List[str]:
    """Parses an IP range string (e.g., '192.168.1.1-192.168.1.254') into a list of IPs."""
    ips = []
    if '-' in ip_range_str:
        start_ip_str, end_ip_str = ip_range_str.split('-')
        try:
            start_ip = ipaddress.IPv4Address(start_ip_str.strip())
            end_ip = ipaddress.IPv4Address(end_ip_str.strip())
            if start_ip > end_ip: # Swap if order is incorrect
                start_ip, end_ip = end_ip, start_ip 

            # Limit the number of IPs to scan to prevent abuse or excessively long scans
            MAX_SCAN_IPS = 256 # Example: a /24 subnet
            current_ip = start_ip
            while current_ip <= end_ip and len(ips) < MAX_SCAN_IPS:
                ips.append(str(current_ip))
                current_ip += 1
            if len(ips) >= MAX_SCAN_IPS:
                _LOGGER.warning(f"IP range {ip_range_str} too large, scanning first {MAX_SCAN_IPS} IPs.")

        except ipaddress.AddressValueError:
            _LOGGER.warning(f"Invalid IP range format: {ip_range_str}")
    else:
        try:
            # Validate single IP
            ipaddress.IPv4Address(ip_range_str.strip())
            ips.append(ip_range_str.strip())
        except ipaddress.AddressValueError:
            _LOGGER.warning(f"Invalid single IP address: {ip_range_str}")
    return ips

class CozyLifeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for CozyLife integration."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_POLL

    def __init__(self):
        """Initialize the config flow."""
        super().__init__()
        self.discovered_devices: List[Dict[str, Any]] = [] # List of {device_id, ip_address, model_name}

    async def async_step_user(self, user_input: Optional[Dict[str, Any]] = None) -> FlowResult:
        """Handle the initial step - asking for IP or IP range."""
        errors: Dict[str, str] = {}

        if user_input is not None:
            ip_range_str = user_input["ip_range"]
            skip_validation = user_input.get("skip_validation", False)
            ip_addresses = _parse_ip_range(ip_range_str)

            if not ip_addresses:
                errors["base"] = "invalid_ip_range"
            elif len(ip_addresses) == 1:
                ip_address = ip_addresses[0]
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
            else:
                 if skip_validation:
                    errors["base"] = "scan_and_skip_validation_exclusive"
                    return self.async_show_form(step_id="user", data_schema=IP_RANGE_SCHEMA, errors=errors)
                 else:
                    # User entered an IP range, proceed to scan step
                    self.data = user_input # Store for later use
                    return await self.async_step_scan()

        return self.async_show_form(
            step_id="user", data_schema=IP_RANGE_SCHEMA, errors=errors
        )
    
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
            step_id="user", data_schema=IP_RANGE_SCHEMA, errors=errors # Re-show user form on error
        )

    async def async_step_scan(self, user_input: Optional[Dict[str, Any]] = None) -> FlowResult:
        """Scan the provided IP range for devices."""
        if user_input is None: # Only run scan once
            ip_range_str = self.data["ip_range"]
            ip_addresses = _parse_ip_range(ip_range_str)
            
            self.discovered_devices = []
            scan_tasks = []

            _LOGGER.debug(f"Scanning IP range: {ip_range_str}")
            semaphore = asyncio.Semaphore(10) 

            async def semaphored_scan(ip: str):
                async with semaphore:
                    return await _scan_for_device(ip)

            for ip in ip_addresses:
                scan_tasks.append(self.hass.async_create_task(
                    semaphored_scan(ip)
                ))
            
            results = await asyncio.gather(*scan_tasks, return_exceptions=True)

            for device_result in results:
                if isinstance(device_result, CozyLifeDevice):
                    await self.async_set_unique_id(device_result.device_id, raise_on_progress=False)
                    if self._is_unique_id_configured(device_result.device_id):
                        _LOGGER.debug(f"Device {device_result.device_id} at {device_result.ip_address} already configured, skipping.")
                        continue

                    self.discovered_devices.append({
                        "device_id": device_result.device_id,
                        "ip_address": device_result.ip_address,
                        "model_name": device_result.device_model_name or "Unknown CozyLife Device",
                    })
                    _DISCOVERED_DEVICES[device_result.device_id] = device_result
                elif device_result is not None and not isinstance(device_result, asyncio.CancelledError):
                    _LOGGER.debug(f"Scan result with exception: {device_result}")
            
            if not self.discovered_devices:
                return self.async_abort(reason="no_devices_found")
            
            return await self.async_step_discover_devices()
        
        return self.async_show_progress(
            step_id="scan",
            title="Scanning for CozyLife devices...",
            message="This may take a moment, please wait.",
            progress_action="wait_for_scan"
        )
    
    async def async_step_discover_devices(self, user_input: Optional[Dict[str, Any]] = None) -> FlowResult:
        """Allow the user to select discovered devices."""
        if user_input is not None:
            selected_device_ids = user_input["devices"]
            
            for device_id in selected_device_ids:
                device = _DISCOVERED_DEVICES.get(device_id)
                if device:
                    self.hass.async_create_task(
                        self.hass.config_entries.flow.async_init(
                            DOMAIN,
                            context={"source": config_entries.SOURCE_USER},
                            data={"ip_address": device.ip_address, "device_id": device.device_id}
                        )
                    )
            
            for device_id in selected_device_ids:
                _DISCOVERED_DEVICES.pop(device_id, None)

            return self.async_abort(reason="devices_added")

        devices_options = {
            device["device_id"]: f"{device['model_name']} ({device['ip_address']})"
            for device in self.discovered_devices
        }

        if not devices_options:
            return self.async_abort(reason="no_devices_found")

        return self.async_show_form(
            step_id="discover_devices",
            data_schema=vol.Schema({
                vol.Required("devices", description={"suggested_value": list(devices_options.keys())}): vol.All(
                    vol.Set(list(devices_options.keys())),
                    vol.Length(min=1)
                )
            }),
            description_placeholders={"devices_list": ", ".join(devices_options.values())},
            last_step=True
        )

    @callback
    def _is_unique_id_configured(self, unique_id: str) -> bool:
        """Check if a unique_id is already configured."""
        return any(
            entry.unique_id == unique_id
            for entry in self._async_current_entries()
        )

async def _scan_for_device(ip_address: str) -> Optional[CozyLifeDevice]:
    """Attempts to connect to an IP and fetch device info."""
    try:
        device = CozyLifeDevice(ip_address, timeout=2)
        if await device.async_update_device_info():
            _LOGGER.debug(f"Discovered CozyLife device at {ip_address}: {device.device_id}")
            return device
    except (asyncio.TimeoutError, ConnectionRefusedError, OSError, Exception) as e:
        _LOGGER.debug(f"No CozyLife device or error at {ip_address}: {e}")
    return None

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