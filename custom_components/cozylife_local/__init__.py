"""The CozyLife (New) integration."""
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN, PLATFORMS
from .cozylife_api import CozyLifeDevice
from .coordinator import CozyLifeCoordinator

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up CozyLife (New) from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    ip_address = entry.data["ip_address"]

    _LOGGER.warning(f"╔══════════════════════════════════════════════════════════════════")
    _LOGGER.warning(f"║ CozyLife Device Setup Starting")
    _LOGGER.warning(f"║ IP Address: {ip_address}")
    _LOGGER.warning(f"╚══════════════════════════════════════════════════════════════════")

    # Create a CozyLifeDevice instance for this entry
    device = CozyLifeDevice(ip_address)

    # Get full device info locally
    if not await device.async_update_device_info():
        _LOGGER.error(f"❌ Failed to get device information for {ip_address}")
        _LOGGER.error(f"   Please check:")
        _LOGGER.error(f"   - Device is powered on and connected to network")
        _LOGGER.error(f"   - IP address is correct")
        _LOGGER.error(f"   - No firewall is blocking communication")
        return False

    # Log complete device information
    _LOGGER.warning(f"╔══════════════════════════════════════════════════════════════════")
    _LOGGER.warning(f"║ Device Discovery Successful!")
    _LOGGER.warning(f"║ ")
    _LOGGER.warning(f"║ Device Model: {device.device_model_name or 'Unknown'}")
    _LOGGER.warning(f"║ Device ID (DID): {device.device_id}")
    _LOGGER.warning(f"║ Product ID (PID): {device.pid}")
    _LOGGER.warning(f"║ Device Type Code: {device.device_type_code}")
    _LOGGER.warning(f"║ IP Address: {ip_address}")
    _LOGGER.warning(f"║ ")
    _LOGGER.warning(f"║ Supported DPIDs: {device.dpid}")
    _LOGGER.warning(f"║ ")

    # Decode device type
    device_type_name = "Unknown"
    if device.device_type_code == "00":
        device_type_name = "Switch"
    elif device.device_type_code == "01":
        device_type_name = "Light"
    elif device.device_type_code == "02":
        device_type_name = "RGB Light"
    _LOGGER.warning(f"║ Device Category: {device_type_name}")
    _LOGGER.warning(f"║ ")

    # Decode DPIDs for user understanding
    if device.dpid:
        _LOGGER.warning(f"║ DPID Capabilities Detected:")
        dpid_explanations = {
            '1': 'Power Switch',
            '2': 'Work Mode (Color/White)',
            '3': 'Color Temperature',
            '4': 'Brightness',
            '5': 'Hue (Color)',
            '6': 'Saturation (Color)',
            '7': 'Color',
            '8': 'Scene',
        }
        for dpid in device.dpid:
            explanation = dpid_explanations.get(dpid, 'Unknown function')
            _LOGGER.warning(f"║   - DPID {dpid}: {explanation}")

    _LOGGER.warning(f"╚══════════════════════════════════════════════════════════════════")

    # Create a coordinator for this device
    coordinator = CozyLifeCoordinator(hass, device)

    # Fetch initial data so we have something to work with
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = coordinator

    _LOGGER.warning(f"📋 Setting up platforms for {device.device_model_name or ip_address}...")

    # Set up platforms (light, switch, etc.)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _LOGGER.warning(f"✅ CozyLife device setup complete: {device.device_model_name or ip_address}")

    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # Unload platforms
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        # Remove coordinator from hass.data
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
