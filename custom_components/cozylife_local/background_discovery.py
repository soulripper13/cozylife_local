"""APK-derived, integration-wide Cozy Life UDP discovery."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant import config_entries
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import discovery_flow

from .const import DOMAIN
from .cozylife_api import COZYLIFE_UDP_DISCOVERY_PORT, _get_sn

_LOGGER = logging.getLogger(__name__)

DATA_DISCOVERY_SERVICE = "background_udp_discovery"
DATA_DISCOVERY_LOCK = "background_udp_discovery_lock"
DATA_DISCOVERY_STOP_UNSUB = "background_udp_discovery_stop_unsub"
# The APK's AppDeamon invokes UdpBoardcastChecker every 10 seconds. It opens a
# short Wi-Fi UDP search window (nominally five seconds) and emits one command-0
# discovery broadcast. This shared listener deliberately remains open for Home
# Assistant's configured devices, but preserves the APK's broadcast cadence.
# It is discovery lifecycle timing only; it is not a sensor wake schedule.
DISCOVERY_BROADCAST_INTERVAL_SECONDS = 10.0
APK_NETWORK_CHANGE_BROADCAST_COUNT = 1
# WifiUdpServer.isLocalhostOnline() expires an announced device after 15 s.
# Keep the same presence boundary before issuing a powered-device health read.
APK_LOCAL_PRESENCE_CACHE_SECONDS = 15.0


@dataclass(frozen=True)
class UdpDiscoveredDevice:
    """Identity advertised by a Cozy Life UDP/6095 reply."""

    device_id: str
    ip_address: str
    pid: str | None
    device_type_code: str | None


class _ApkUdpDiscoveryProtocol(asyncio.DatagramProtocol):
    """Parse the UDP reply shape cached by the Cozy Life APK."""

    def __init__(self, found: Callable[[UdpDiscoveredDevice], None]) -> None:
        self._found = found

    def datagram_received(self, data: bytes, _addr: tuple[str, int]) -> None:
        """Pass a valid DID/IP announcement to the shared cache."""
        try:
            frame = json.loads(data.decode("utf-8").split("\r\n", 1)[0])
            message = frame["msg"]
            device_id = message["did"]
            ip_address = message["ip"]
            if not isinstance(device_id, str) or not device_id:
                return
            if not isinstance(ip_address, str):
                return
            ipaddress.IPv4Address(ip_address)
        except (KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            return

        pid = message.get("pid")
        device_type_code = message.get("dtp")
        self._found(
            UdpDiscoveredDevice(
                device_id=device_id,
                ip_address=ip_address,
                pid=pid if isinstance(pid, str) else None,
                device_type_code=(
                    device_type_code if isinstance(device_type_code, str) else None
                ),
            )
        )


class CozyLifeBackgroundDiscovery:
    """One APK-style UDP socket shared by all Cozy Life config entries."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._transport: asyncio.DatagramTransport | None = None
        self._broadcast_task: asyncio.Task[None] | None = None
        self._devices: dict[str, tuple[UdpDiscoveredDevice, float]] = {}
        # Each callback is owned by a config entry.  Keeping that ownership is
        # important when a user removes and immediately re-adds the same
        # device: completion of the old entry's unload must not unregister the
        # callback just installed by the new entry.
        self._entry_callbacks: dict[
            str, tuple[str, Callable[[str], Awaitable[None]]]
        ] = {}
        self._address_callbacks: dict[str, Callable[[str], Awaitable[None]]] = {}
        # UDP replies arrive repeatedly.  One native discovery card per DID is
        # sufficient until it is either configured (which clears this set in
        # register_device) or Home Assistant restarts.
        self._pending_discovery_ids: set[str] = set()

    async def async_start(self) -> None:
        """Open the APK-style receive socket and begin its Wi-Fi cadence."""
        if self._broadcast_task is not None:
            return

        await self._async_open_receive_socket()
        self._broadcast_task = self.hass.async_create_background_task(
            self._async_broadcast_loop(),
            "cozylife UDP discovery broadcaster",
        )
        _LOGGER.info(
            "CozyLife UDP discovery opened its APK-style receive socket; sent %s "
            "startup discovery request",
            APK_NETWORK_CHANGE_BROADCAST_COUNT,
        )

    async def _async_open_receive_socket(self) -> None:
        """Open the APK-style ephemeral UDP socket and retain its receive loop."""
        if self._transport is not None:
            return
        loop = asyncio.get_running_loop()
        transport, _protocol = await loop.create_datagram_endpoint(
            lambda: _ApkUdpDiscoveryProtocol(self._handle_device),
            local_addr=("0.0.0.0", 0),
            allow_broadcast=True,
        )
        self._transport = transport
        self._send_broadcasts(APK_NETWORK_CHANGE_BROADCAST_COUNT)

    def _close_receive_socket(self) -> None:
        """Close the shared ephemeral discovery socket when the service stops."""
        if self._transport is not None:
            self._transport.close()
            self._transport = None

    async def async_stop(self) -> None:
        """Stop the shared socket when Home Assistant stops."""
        if self._broadcast_task is not None:
            self._broadcast_task.cancel()
            try:
                await self._broadcast_task
            except asyncio.CancelledError:
                pass
            self._broadcast_task = None
        self._close_receive_socket()

    @property
    def is_running(self) -> bool:
        """Return whether the shared APK-style discovery lifecycle is active."""
        return self._broadcast_task is not None or self._transport is not None

    def register_device(
        self,
        device_id: str,
        entry_id: str,
        callback: Callable[[str], Awaitable[None]],
    ) -> None:
        """Route later APK UDP replies for one configured DID to its entry."""
        self._pending_discovery_ids.discard(device_id)
        self._entry_callbacks[device_id] = (entry_id, callback)
        cached = getattr(self, "_devices", {}).get(device_id)
        if cached is not None:
            self.hass.async_create_task(callback(cached[0].ip_address))

    def is_device_present(self, device_id: str | None, ip_address: str) -> bool:
        """Return whether an APK-style UDP announcement is still fresh.

        The Android app only queues a local Wi-Fi TCP message while its
        WifiUdpServer cache contains the device.  That cache expires entries
        15 seconds after their most recent UDP announcement.
        """
        if not device_id:
            return False
        cached = self._devices.get(device_id)
        if cached is None:
            return False
        device, received_at = cached
        if (
            device.ip_address != ip_address
            or time.monotonic() - received_at > APK_LOCAL_PRESENCE_CACHE_SECONDS
        ):
            self._devices.pop(device_id, None)
            return False
        return True

    def unregister_device(self, device_id: str, entry_id: str) -> None:
        """Stop routing replies only when this entry owns the registration."""
        registered = self._entry_callbacks.get(device_id)
        if registered is not None and registered[0] == entry_id:
            self._entry_callbacks.pop(device_id, None)

    def register_address(
        self,
        ip_address: str,
        callback: Callable[[str], Awaitable[None]],
    ) -> None:
        """Route a provisional sleeping sensor's verified address reply."""
        self._address_callbacks[ip_address] = callback

    def unregister_address(self, ip_address: str) -> None:
        """Stop routing replies by a provisional sensor address."""
        self._address_callbacks.pop(ip_address, None)

    async def _async_broadcast_loop(self) -> None:
        """Repeat the APK daemon's command-0 broadcast every ten seconds."""
        while True:
            await asyncio.sleep(DISCOVERY_BROADCAST_INTERVAL_SECONDS)
            self._send_broadcasts(1)

    def _send_broadcasts(self, count: int) -> None:
        """Send APK command-0 frames through the listener's own socket."""
        transport = self._transport
        if transport is None:
            return
        for _ in range(count):
            request = {"cmd": 0, "pv": 0, "sn": _get_sn(), "msg": {}}
            transport.sendto(
                (json.dumps(request, separators=(",", ":")) + "\r\n").encode(),
                ("255.255.255.255", COZYLIFE_UDP_DISCOVERY_PORT),
            )

    def _handle_device(self, device: UdpDiscoveredDevice) -> None:
        """Cache an APK reply, refresh known entries, or start one discovery flow."""
        self._devices[device.device_id] = (device, time.monotonic())
        registration = self._entry_callbacks.get(device.device_id)
        callback = registration[1] if registration is not None else None
        route = "device ID"
        if callback is None:
            callback = self._address_callbacks.get(device.ip_address)
            route = "provisional address"
        if callback is not None:
            _LOGGER.debug(
                "Routing CozyLife UDP announcement by %s",
                route,
            )
            self.hass.async_create_task(callback(device.ip_address))
            return

        if self._is_configured(device.device_id, device.ip_address):
            self._pending_discovery_ids.discard(device.device_id)
            _LOGGER.debug(
                "Ignoring CozyLife UDP announcement for an already configured device",
            )
            return
        if device.device_id in self._pending_discovery_ids:
            _LOGGER.debug(
                "Ignoring repeated CozyLife discovery announcement for a pending device",
            )
            return
        self._pending_discovery_ids.add(device.device_id)
        _LOGGER.debug(
            "Creating CozyLife discovery flow",
        )
        self._async_start_discovery_flow(device)

    def _is_configured(self, device_id: str, ip_address: str) -> bool:
        """Avoid cards for configured DIDs, including provisional sensor IDs."""
        return any(
            entry.data.get("device_id") == device_id
            or entry.unique_id == device_id
            # A sleeping sensor can be configured before it has supplied its
            # real DID. Its static configured address still identifies the
            # announcement as belonging to an existing entry.
            or entry.data.get("ip_address") == ip_address
            for entry in self.hass.config_entries.async_entries(DOMAIN)
        )

    @callback
    def _async_start_discovery_flow(self, device: UdpDiscoveredDevice) -> None:
        """Offer an unconfigured DID through Home Assistant's discovery UI."""
        try:
            discovery_flow.async_create_flow(
                self.hass,
                DOMAIN,
                context={"source": config_entries.SOURCE_INTEGRATION_DISCOVERY},
                data={
                    "device_id": device.device_id,
                    "ip_address": device.ip_address,
                    "pid": device.pid,
                    "device_type_code": device.device_type_code,
                },
            )
        except Exception:  # pragma: no cover - Home Assistant owns flow errors.
            _LOGGER.exception(
                "Unable to start a CozyLife discovery flow",
            )


async def async_get_background_discovery(
    hass: HomeAssistant,
    *,
    start: bool = True,
) -> CozyLifeBackgroundDiscovery:
    """Return the domain's shared discovery service, optionally starting it."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    lock = domain_data.get(DATA_DISCOVERY_LOCK)
    if lock is None:
        lock = asyncio.Lock()
        domain_data[DATA_DISCOVERY_LOCK] = lock

    async with lock:
        service = domain_data.get(DATA_DISCOVERY_SERVICE)
        if service is None:
            service = CozyLifeBackgroundDiscovery(hass)
            domain_data[DATA_DISCOVERY_SERVICE] = service
        if DATA_DISCOVERY_STOP_UNSUB not in domain_data:
            # Config flows can start discovery before a config entry exists,
            # so tie cleanup to the service rather than entry setup.
            @callback
            def _async_stop_discovery(_event) -> None:
                active_service = domain_data.get(DATA_DISCOVERY_SERVICE)
                if active_service is not None:
                    hass.async_create_task(active_service.async_stop())

            domain_data[DATA_DISCOVERY_STOP_UNSUB] = hass.bus.async_listen_once(
                EVENT_HOMEASSISTANT_STOP,
                _async_stop_discovery,
            )
        if start and service._transport is None:
            await service.async_start()
    return service
