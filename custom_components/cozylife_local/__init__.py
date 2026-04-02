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

    # Check for debug logging option
    pkg_logger = logging.getLogger(__package__)
    if entry.options.get("enable_debug", False):
        pkg_logger.setLevel(logging.DEBUG)
        _LOGGER.debug(f"Debug logging enabled via options for {entry.title}")
    else:
        pkg_logger.setLevel(logging.NOTSET)

    hass.data.setdefault(DOMAIN, {})

    ip_address = entry.data["ip_address"]

    # Create a CozyLifeDevice instance for this entry
    device = CozyLifeDevice(ip_address)

    # Get full device info locally
    if not await device.async_update_device_info():
        _LOGGER.error(f"Failed to get full device information for {ip_address}")
        return False

    # Create a coordinator for this device
    coordinator = CozyLifeCoordinator(hass, device, entry)

    if coordinator.is_sensor:
        # Sensors sleep for 30+ min — don't block setup on first refresh.
        # Seed with empty data so polling starts, entities update on first successful poll.
        coordinator.async_set_updated_data({})
    else:
        await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Set up platforms (light, switch, sensor, etc.)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Reload entry when options change
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    return True


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
