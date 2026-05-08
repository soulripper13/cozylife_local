"""Network scanning helpers for CozyLife Local."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
from dataclasses import dataclass

from homeassistant.core import HomeAssistant

from .cozylife_api import COZYLIFE_PORT, CozyLifeDevice
from .discovery import async_load_model_catalog

_LOGGER = logging.getLogger(__name__)

AUTO_NETWORK = "auto"
MAX_SCAN_HOSTS = 512
SCAN_CONNECT_TIMEOUT = 0.75
DEVICE_INFO_TIMEOUT = 1.5
SCAN_CONCURRENCY = 64


@dataclass(frozen=True)
class DiscoveredDevice:
    """A CozyLife device discovered on the local network."""

    ip_address: str
    device_id: str | None
    pid: str | None
    device_type_code: str | None
    device_model_name: str | None
    dpids: tuple[str, ...]

    @property
    def label(self) -> str:
        """Return a human-readable label for the config flow selector."""
        model = self.device_model_name or "CozyLife Device"
        pid = self.pid or "unknown PID"
        type_code = self.device_type_code or "unknown type"
        return f"{model} ({self.ip_address}) - PID {pid}, Type {type_code}"


class NetworkScanTooLarge(ValueError):
    """Raised when the requested network is too large to scan safely."""


class NoNetworkAvailable(ValueError):
    """Raised when no local IPv4 network can be detected."""


async def async_discover_devices(
    hass: HomeAssistant,
    network_cidr: str = AUTO_NETWORK,
) -> list[DiscoveredDevice]:
    """Scan the requested network and return discovered CozyLife devices."""
    await async_load_model_catalog(hass)
    networks = await _async_get_scan_networks(hass, network_cidr)
    hosts = _hosts_for_networks(networks)

    semaphore = asyncio.Semaphore(SCAN_CONCURRENCY)

    async def scan_host(ip_address: str) -> DiscoveredDevice | None:
        async with semaphore:
            return await _async_discover_host(ip_address)

    results = await asyncio.gather(*(scan_host(ip_address) for ip_address in hosts))
    devices = [device for device in results if device is not None]
    devices.sort(key=lambda device: tuple(int(part) for part in device.ip_address.split(".")))
    _LOGGER.info(
        "CozyLife network discovery found %s device(s) on %s",
        len(devices),
        ", ".join(str(network) for network in networks),
    )
    return devices


async def _async_get_scan_networks(
    hass: HomeAssistant,
    network_cidr: str,
) -> list[ipaddress.IPv4Network]:
    """Return IPv4 networks to scan."""
    requested = (network_cidr or AUTO_NETWORK).strip().lower()
    if requested != AUTO_NETWORK:
        try:
            network = ipaddress.ip_network(requested, strict=False)
        except ValueError as err:
            raise ValueError("invalid_network") from err

        if network.version != 4:
            raise ValueError("invalid_network")

        return [_validate_network_size(network)]

    networks = await _async_get_auto_networks(hass)
    if not networks:
        raise NoNetworkAvailable("no_network")
    return networks


async def _async_get_auto_networks(hass: HomeAssistant) -> list[ipaddress.IPv4Network]:
    """Build safe scan networks from Home Assistant's enabled adapters."""
    try:
        from homeassistant.components import network as ha_network
    except ImportError:
        try:
            from homeassistant.helpers import network as ha_network
        except ImportError:
            return []

    async_get_adapters = getattr(ha_network, "async_get_adapters", None)
    if async_get_adapters is None:
        return []

    adapters = await async_get_adapters(hass)
    networks: list[ipaddress.IPv4Network] = []
    seen: set[str] = set()

    for adapter in adapters:
        if not adapter.get("enabled", True):
            continue

        for ipv4 in adapter.get("ipv4", []):
            address = ipv4.get("address")
            prefix = ipv4.get("network_prefix")
            if not address or prefix is None:
                continue

            try:
                ip_addr = ipaddress.ip_address(address)
                if ip_addr.is_loopback or ip_addr.is_link_local:
                    continue

                interface = ipaddress.ip_interface(f"{address}/{prefix}")
                network = interface.network
                if network.version != 4:
                    continue

                # A /24 keeps auto-discovery responsive even on broad LAN masks.
                if network.prefixlen < 24:
                    network = ipaddress.ip_network(f"{address}/24", strict=False)

                network = _validate_network_size(network)
            except ValueError:
                continue

            key = str(network)
            if key not in seen:
                seen.add(key)
                networks.append(network)

    return networks


def _validate_network_size(
    network: ipaddress.IPv4Network,
) -> ipaddress.IPv4Network:
    """Reject networks that are too large for an interactive config flow."""
    if network.num_addresses > MAX_SCAN_HOSTS + 2:
        raise NetworkScanTooLarge(str(network))
    return network


def _hosts_for_networks(networks: list[ipaddress.IPv4Network]) -> list[str]:
    """Return de-duplicated host addresses for all networks."""
    hosts: list[str] = []
    seen: set[str] = set()

    for network in networks:
        for host in network.hosts():
            ip_address = str(host)
            if ip_address not in seen:
                seen.add(ip_address)
                hosts.append(ip_address)

    if len(hosts) > MAX_SCAN_HOSTS:
        raise NetworkScanTooLarge("combined networks")

    return hosts


async def _async_discover_host(ip_address: str) -> DiscoveredDevice | None:
    """Return device details if the host speaks the CozyLife local protocol."""
    if not await _async_is_port_open(ip_address):
        return None

    device = CozyLifeDevice(ip_address, timeout=DEVICE_INFO_TIMEOUT)
    if not await device.async_update_device_info():
        return None

    return DiscoveredDevice(
        ip_address=ip_address,
        device_id=device.device_id,
        pid=device.pid,
        device_type_code=device.device_type_code,
        device_model_name=device.device_model_name,
        dpids=tuple(device.dpid or ()),
    )


async def _async_is_port_open(ip_address: str) -> bool:
    """Return true if the CozyLife TCP port accepts a connection."""
    writer: asyncio.StreamWriter | None = None
    try:
        _reader, writer = await asyncio.wait_for(
            asyncio.open_connection(ip_address, COZYLIFE_PORT),
            timeout=SCAN_CONNECT_TIMEOUT,
        )
        return True
    except (asyncio.TimeoutError, ConnectionRefusedError, OSError):
        return False
    finally:
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
