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

    # Create a CozyLifeDevice instance for this entry
    device = CozyLifeDevice(ip_address)
    
    # Get full device info locally
    if not await device.async_update_device_info():
        _LOGGER.error(f"Failed to get full device information for {ip_address}")
        return False

    # Create a coordinator for this device
    coordinator = CozyLifeCoordinator(hass, device)
    
    # Fetch initial data so we have something to work with
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Set up platforms (light, switch, etc.)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # Unload platforms
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        # Remove coordinator from hass.data
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
