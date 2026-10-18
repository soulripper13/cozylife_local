import asyncio
import inspect
import json
import logging
import socket
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional

from .discovery import get_model_info

_LOGGER = logging.getLogger(__name__)
_RAW_LOGGER = logging.getLogger(f"{__name__}.raw")
_RAW_LOGGER.setLevel(logging.INFO)

COZYLIFE_PORT = 5555
COZYLIFE_UDP_DISCOVERY_PORT = 6095
SENSOR_SOCKET_RECYCLE_SECONDS = 30.0
CMD_INFO = 0
CMD_QUERY = 2
CMD_SET = 3
DPID_QUERY_ATTEMPTS = 3
_last_sn = 0


class _CozyLifeDiscoveryProtocol(asyncio.DatagramProtocol):
    """Receive Cozy Life's local UDP discovery replies."""

    def __init__(self, device_id: str, found: Callable[[str], Awaitable[None]]) -> None:
        self._device_id = device_id
        self._found = found

    def datagram_received(self, data: bytes, _addr: tuple[str, int]) -> None:
        """Accept only the reply for the configured Cozy Life device."""
        try:
            frame = json.loads(data.decode("utf-8").split("\r\n", 1)[0])
            message = frame["msg"]
            if message.get("did") != self._device_id:
                return
            ip_address = message.get("ip")
            if not isinstance(ip_address, str) or not ip_address:
                return
        except (KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
            return

        asyncio.create_task(self._found(ip_address))


class CozyLifeUdpDiscovery:
    """Keep the APK's local UDP discovery path available for a device.

    The APK broadcasts ``cmd: 0`` to UDP/6095 and matches reply ``msg.did`` to
    refresh the local TCP address.  The APK's ``UdpBoardcastChecker`` starts
    this discovery cycle from its 10-second application daemon.  The Home
    Assistant integration retains its configured address for local probes, so
    discovery replies are used to verify or refresh that address rather than
    being a prerequisite for every TCP attempt.
    """

    def __init__(
        self,
        device_id: str,
        found: Callable[[str], Awaitable[None]],
        interval: float,
    ) -> None:
        self._device_id = device_id
        self._found = found
        self._interval = interval
        self._socket: socket.socket | None = None
        self._task: asyncio.Task[None] | None = None
        self._receive_task: asyncio.Task[None] | None = None

    async def async_start(self) -> None:
        """Open one non-blocking ephemeral UDP socket, as the APK does."""
        loop = asyncio.get_running_loop()
        udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        udp_socket.bind(("0.0.0.0", 0))
        udp_socket.setblocking(False)
        self._socket = udp_socket
        self._task = asyncio.create_task(
            self._async_broadcast_loop(),
            name=f"cozylife-discovery-{self._device_id}",
        )
        self._receive_task = asyncio.create_task(
            self._async_receive_loop(loop),
            name=f"cozylife-discovery-receiver-{self._device_id}",
        )

    async def async_stop(self) -> None:
        """Stop discovery and release its UDP socket."""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._receive_task:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
            self._receive_task = None
        if self._socket:
            self._socket.close()
            self._socket = None

    async def _async_broadcast_loop(self) -> None:
        """Send the exact APK UDP discovery request on its daemon cadence."""
        while True:
            udp_socket = self._socket
            if udp_socket is None:
                return
            message = {"cmd": 0, "pv": 0, "sn": _get_sn(), "msg": {}}
            udp_socket.sendto(
                (json.dumps(message, separators=(",", ":")) + "\r\n").encode(),
                ("255.255.255.255", COZYLIFE_UDP_DISCOVERY_PORT),
            )
            await asyncio.sleep(self._interval)

    async def _async_receive_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Process matching replies from the same ephemeral discovery socket."""
        while self._socket is not None:
            try:
                data, _address = await loop.sock_recvfrom(self._socket, 4096)
            except (OSError, asyncio.CancelledError):
                return
            _CozyLifeDiscoveryProtocol(self._device_id, self._found).datagram_received(
                data, _address
            )


def _get_sn() -> str:
    """Generate a unique, numeric sequence number for request correlation."""
    global _last_sn
    timestamp_ms = int(time.time() * 1000)
    _last_sn = max(timestamp_ms, _last_sn + 1)
    return str(_last_sn)

class CozyLifeDevice:
    """Represents a CozyLife device and handles its TCP communication."""

    def __init__(self, ip_address: str, timeout: float = 3.0):
        self._ip_address = ip_address
        self._timeout = timeout
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._device_id: Optional[str] = None
        self._pid: Optional[str] = None
        self._device_model_name: Optional[str] = None
        self._dpid: Optional[List[str]] = None
        self._device_type_code: Optional[str] = None
        self._pending_responses: Dict[str, asyncio.Future[Optional[Dict[str, Any]]]] = {}
        self._message_listener: Optional[Callable[[Dict[str, Any]], Any]] = None
        self._reader_task: Optional[asyncio.Task[None]] = None
        self._connection_lock: Optional[asyncio.Lock] = None
        self._status_lock: Optional[asyncio.Lock] = None
        self._connected_at: Optional[float] = None

    @property
    def ip_address(self) -> str:
        return self._ip_address

    @property
    def device_id(self) -> Optional[str]:
        return self._device_id

    @property
    def pid(self) -> Optional[str]:
        return self._pid

    @property
    def device_model_name(self) -> Optional[str]:
        return self._device_model_name

    @property
    def dpid(self) -> Optional[List[str]]:
        return self._dpid

    @property
    def device_type_code(self) -> Optional[str]:
        return self._device_type_code

    def set_timeout(self, timeout: float) -> None:
        """Set the bounded duration for one local request attempt."""
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self._timeout = timeout

    def set_message_listener(self, listener: Callable[[Dict[str, Any]], Any]) -> None:
        """Receive every frame delivered by the device's persistent socket."""
        self._message_listener = listener

    async def async_update_ip_address(self, ip_address: str) -> bool:
        """Replace a discovered address and discard only the old TCP socket."""
        if ip_address == self._ip_address:
            return False
        await self.async_close()
        self._ip_address = ip_address
        _LOGGER.info("CozyLife device %s moved to %s", self._device_id, ip_address)
        return True

    def restore_from_cache(self, device_id: str, pid: str, device_type_code: str, dpids: List[str]) -> None:
        """Restore device info from cached config entry data without contacting the device."""
        model_info = get_model_info(pid)
        self._device_id = device_id
        self._pid = pid
        self._device_type_code = device_type_code
        self._dpid = dpids
        self._device_model_name = (
            model_info.model_name if model_info else f"CozyLife Device ({pid})"
        )

    async def _connect(self) -> None:
        """Open one socket and retain it for the device's receive stream.

        Cozy Life's Android app has a dedicated TCP reader over its socket pool.
        Reconnecting for each request loses frames that arrive between queries.
        """
        if self._writer and not self._writer.is_closing():
            return

        if self._connection_lock is None:
            self._connection_lock = asyncio.Lock()

        async with self._connection_lock:
            if self._writer and not self._writer.is_closing():
                return
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(self._ip_address, COZYLIFE_PORT),
                    timeout=self._timeout,
                )
            except (asyncio.TimeoutError, ConnectionRefusedError, OSError):
                self._disconnect()
                raise

            self._reader = reader
            self._writer = writer
            self._reader_task = asyncio.create_task(
                self._read_frames(reader, writer),
                name=f"cozylife-reader-{self._ip_address}",
            )
            _RAW_LOGGER.debug(
                "Connected persistent CozyLife socket at %s:%s",
                self._ip_address,
                COZYLIFE_PORT,
            )

    def _disconnect(self) -> None:
        """Close the socket and release callers waiting for a response."""
        writer = self._writer
        self._reader = None
        self._writer = None
        self._connected_at = None
        if writer:
            try:
                writer.close()
            except Exception:
                pass

        for future in self._pending_responses.values():
            if not future.done():
                future.set_result(None)
        self._pending_responses.clear()

    async def async_close(self) -> None:
        """Release the persistent TCP reader when its config entry unloads."""
        reader_task = self._reader_task
        self._reader_task = None
        self._disconnect()
        if reader_task and reader_task is not asyncio.current_task():
            reader_task.cancel()
            try:
                await reader_task
            except asyncio.CancelledError:
                pass

    async def _read_frames(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Continuously dispatch CRLF-framed device traffic from one socket."""
        try:
            while True:
                raw_frame = await reader.readline()
                if not raw_frame:
                    return
                try:
                    frame = json.loads(raw_frame.decode("utf8").strip())
                except (json.JSONDecodeError, UnicodeDecodeError) as err:
                    _LOGGER.warning("Invalid frame from %s: %s", self._ip_address, err)
                    continue

                _RAW_LOGGER.debug("Received from %s: %r", self._ip_address, raw_frame)
                sent_sn = frame.get("sn")
                future = self._pending_responses.pop(sent_sn, None)
                if future and not future.done():
                    future.set_result(frame.get("msg") if frame.get("res") == 0 else None)

                listener = self._message_listener
                if listener is not None:
                    result = listener(frame)
                    if inspect.isawaitable(result):
                        await result
        except (ConnectionError, OSError) as err:
            _LOGGER.debug("Persistent connection to %s ended: %s", self._ip_address, err)
        finally:
            if self._reader is reader and self._writer is writer:
                self._disconnect()

    async def _send_receive(self, cmd: int, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Send a frame through the persistent reader-owned connection."""
        try:
            await self._connect()
            message = self._create_message(cmd, payload)
            sent_sn = message["sn"]
            writer = self._writer
            if writer is None:
                return None
            response = asyncio.get_running_loop().create_future()
            self._pending_responses[sent_sn] = response
            encoded_message = (json.dumps(message, separators=(",", ":")) + "\r\n").encode("utf8")
            _RAW_LOGGER.debug("Sending to %s: %r", self._ip_address, encoded_message)
            writer.write(encoded_message)
            await writer.drain()
            try:
                return await asyncio.wait_for(asyncio.shield(response), timeout=self._timeout)
            except asyncio.TimeoutError:
                _LOGGER.warning("Timeout waiting for response with sn %s from %s", sent_sn, self._ip_address)
                # A persistent socket whose peer stopped answering is not a
                # usable transport for the next request.  Discard it so the
                # next attempt performs a clean local reconnect.
                self._disconnect()
                return None
            finally:
                self._pending_responses.pop(sent_sn, None)
        except (asyncio.TimeoutError, ConnectionRefusedError, OSError):
            self._disconnect()
            return None

    def _create_message(self, cmd: int, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Helper to create the JSON message structure."""
        sn = _get_sn()
        if cmd == CMD_SET:
            return {
                'pv': 0, 'cmd': cmd, 'sn': sn,
                'msg': {'attr': [int(k) for k in payload.keys()], 'data': payload}
            }
        elif cmd == CMD_QUERY:
            return {
                'pv': 0, 'cmd': cmd, 'sn': sn,
                'msg': {'attr': payload.get('attr', [0])}
            }
        elif cmd == CMD_INFO:
            return {'pv': 0, 'cmd': cmd, 'sn': sn, 'msg': {}}
        else:
            raise ValueError(f"Unknown command: {cmd}")

    async def async_update_device_info(self) -> bool:
        """
        Fetches and updates device information locally.
        Gets DID, PID, and Device Type from CMD_INFO.
        Gets the full DPID list from CMD_QUERY.
        This method is now fully local and does not require a cloud connection.
        """
        # Step 1: Get basic device info (did, pid, dtp)
        info_msg = await self._send_receive(CMD_INFO, {})
        if not info_msg or not all(k in info_msg for k in ['did', 'pid', 'dtp']):
            _LOGGER.warning(f"Device info query failed or missing critical fields: {info_msg}")
            return False

        self._device_id = info_msg['did']
        self._pid = info_msg['pid']
        self._device_type_code = info_msg['dtp']
        model_info = get_model_info(self._pid)
        self._device_model_name = (
            model_info.model_name if model_info else f"CozyLife Device ({self._pid})"
        )

        # Step 2: Get the full list of supported DPIDs. Some metered plugs can
        # drop the first query during setup, so retry before falling back.
        query_msg = None
        for attempt in range(1, DPID_QUERY_ATTEMPTS + 1):
            query_msg = await self._send_receive(CMD_QUERY, {})
            if query_msg and ('attr' in query_msg or 'data' in query_msg):
                break
            _LOGGER.debug(
                "DPID query attempt %s/%s failed for %s: %s",
                attempt,
                DPID_QUERY_ATTEMPTS,
                self._ip_address,
                query_msg,
            )

        if not query_msg:
            _LOGGER.warning(f"Failed to query DPID list from device: {query_msg}")
            # We can still proceed with a minimal setup if this fails
            self._dpid = []
            return True # Return true as we have the basic info

        if 'attr' in query_msg:
            self._dpid = [str(dpid) for dpid in query_msg['attr']]
        elif 'data' in query_msg:
            self._dpid = [str(dpid) for dpid in query_msg['data']]
        else:
            self._dpid = []

        _LOGGER.info(
            f"Successfully discovered device {self._ip_address} locally: "
            f"DID={self.device_id}, PID={self.pid}, Type={self.device_type_code}, DPIDs={self.dpid}"
        )
        return True

    async def async_get_state(self, dpids: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
        """Queries the current state of the device."""
        payload = {}
        if dpids:
            payload["attr"] = [int(dpid) for dpid in dpids]

        msg = await self._send_receive(CMD_QUERY, payload)
        if msg and 'data' in msg:
            return {str(k): v for k, v in msg['data'].items()}
        return None

    async def async_get_status(self) -> Optional[Dict[str, Any]]:
        """Use the APK's local refresh request: cmd 2 with ``attr:[1]``."""
        msg = await self._send_receive(CMD_QUERY, {"attr": [1]})
        if msg and "data" in msg:
            return {str(key): value for key, value in msg["data"].items()}
        return None

    async def async_send_status(self) -> bool:
        """Write the APK status request without waiting for an accepted reply.

        Cozy Life's ``WifiTcpMessage.expectAccept`` is false.  Its reader,
        rather than the writer, receives the asynchronous sensor report.
        """
        if self._status_lock is None:
            self._status_lock = asyncio.Lock()

        # The discovery callback and the wake-window loop can both request a
        # status frame.  Keep their connect/write operations strictly serial:
        # one durable socket is preferable to briefly opening two competing
        # sockets while the device is awake.
        async with self._status_lock:
            return await self._async_send_status()

    async def _async_send_status(self) -> bool:
        """Send one status frame while the status transport lock is held."""
        # The APK's shared TCP pool is cleaned by its 10-second app daemon.
        # Recycle the local sensor socket on the same cadence so a later wake
        # window gets a new TCP connection rather than writes to a stale one.
        if (
            self._writer
            and not self._writer.is_closing()
            and self._connected_at is not None
            and time.monotonic() - self._connected_at >= SENSOR_SOCKET_RECYCLE_SECONDS
        ):
            if self._connection_lock is None:
                self._connection_lock = asyncio.Lock()
            async with self._connection_lock:
                if (
                    self._writer
                    and not self._writer.is_closing()
                    and self._connected_at is not None
                    and time.monotonic() - self._connected_at
                    >= SENSOR_SOCKET_RECYCLE_SECONDS
                ):
                    self._disconnect()

        # A waking battery sensor can accept a connection only briefly. Do
        # not serialize a new TCP connect behind an earlier one that is still
        # waiting for its full APK-derived deadline; install whichever probe
        # reaches the device first, then retain that socket for the reader.
        if not self._writer or self._writer.is_closing():
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(self._ip_address, COZYLIFE_PORT),
                    timeout=self._timeout,
                )
            except (asyncio.TimeoutError, ConnectionRefusedError, OSError):
                return False

            if self._connection_lock is None:
                self._connection_lock = asyncio.Lock()
            async with self._connection_lock:
                if self._writer and not self._writer.is_closing():
                    writer.close()
                    try:
                        await writer.wait_closed()
                    except OSError:
                        pass
                else:
                    self._reader = reader
                    self._writer = writer
                    self._connected_at = time.monotonic()
                    self._reader_task = asyncio.create_task(
                        self._read_frames(reader, writer),
                        name=f"cozylife-reader-{self._ip_address}",
                    )
        try:
            writer = self._writer
            if writer is None:
                return False
            message = self._create_message(CMD_QUERY, {"attr": [1]})
            writer.write(
                (json.dumps(message, separators=(",", ":")) + "\r\n").encode("utf8")
            )
            await writer.drain()
            return True
        except (asyncio.TimeoutError, ConnectionRefusedError, OSError):
            self._disconnect()
            return False

    async def async_set_state(self, attributes: Dict[str, Any]) -> bool:
        """Sets attributes on the device."""
        msg = await self._send_receive(CMD_SET, attributes)
        # For CMD_SET, the response msg contains the set attributes if successful.
        # We can compare it with the sent attributes for confirmation.
        if msg and 'data' in msg:
            # A more robust check would compare the sent attributes with the received
            # for exact match, but for now, just checking 'data' is present
            return True
        return False
