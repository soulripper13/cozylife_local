import asyncio
import inspect
import json
import logging
import socket
import time
from typing import Any, Callable, Dict, List, Optional

from .discovery import get_model_info
from .debug import summarize_frame

_LOGGER = logging.getLogger(__name__)
_RAW_LOGGER = logging.getLogger(f"{__name__}.raw")

COZYLIFE_PORT = 5555
COZYLIFE_UDP_DISCOVERY_PORT = 6095
CMD_INFO = 0
CMD_QUERY = 2
CMD_SET = 3
DPID_QUERY_ATTEMPTS = 3
_last_sn = 0


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
        self._state_waiters: list[asyncio.Future[Dict[str, Any]]] = []
        self._message_listener: Optional[Callable[[Dict[str, Any]], Any]] = None
        self._reader_task: Optional[asyncio.Task[None]] = None
        self._connection_lock: Optional[asyncio.Lock] = None
        self._status_lock: Optional[asyncio.Lock] = None
        self._last_transport_error: Optional[str] = None
        self._tcp_user_timeout_ms: int | None = None
        self.last_frame_received_at: float | None = None

    def set_tcp_user_timeout(self, milliseconds: int) -> None:
        """Bound unacknowledged TCP data without timing sensor reports."""
        if milliseconds <= 0:
            raise ValueError("TCP user timeout must be positive")
        self._tcp_user_timeout_ms = milliseconds

    def _encode_message(self, message: Dict[str, Any]) -> bytes:
        """Preserve legacy CRLF framing except for the tested sensor profile.

        h38t1p firmware requires CRLF for queries and writes (issue #91).
        The APK writer's bare JSON is not a universal firmware contract.
        Unknown devices retain legacy framing during identity discovery.
        Z4tRml retains the bare framing used in the ongoing sensor test.
        """
        encoded = json.dumps(message, separators=(",", ":")).encode("utf8")
        return encoded if self._pid == "Z4tRml" else encoded + b"\r\n"

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

    @property
    def last_transport_error(self) -> Optional[str]:
        """Return the latest local TCP failure for diagnostics."""
        return self._last_transport_error

    @property
    def is_connected(self) -> bool:
        """Return whether the persistent local TCP writer remains usable."""
        return self._writer is not None and not self._writer.is_closing()

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
        _LOGGER.info("CozyLife device address refreshed")
        return True

    def restore_from_cache(self, device_id: str, pid: str, device_type_code: str, dpids: List[str]) -> None:
        """Restore local identity and its verified capability inventory."""
        model_info = get_model_info(pid)
        self._device_id = device_id
        self._pid = pid
        self._device_type_code = device_type_code
        self._dpid = self._catalog_backed_dpids(dpids, model_info)
        self._device_model_name = (
            model_info.model_name if model_info else f"CozyLife Device ({pid})"
        )

    @staticmethod
    def _catalog_backed_dpids(
        dpids: List[str],
        model_info: Any,
    ) -> List[str]:
        """Return the complete verified capability inventory for a known PID.

        A local attr:[0] inventory can be partial, and older config entries
        persist that partial list.  The APK model catalogue is the supported
        capability source for a known product; merge it with observed DPIDs so
        platform setup and targeted reads use the same inventory.
        """
        capability_dpids = {str(dpid) for dpid in dpids}
        if model_info is not None:
            capability_dpids.update(str(dpid) for dpid in model_info.dpids)
        return sorted(capability_dpids, key=int)

    async def _connect(self, *, timeout: float | None = None) -> None:
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
                _RAW_LOGGER.debug("Opening persistent CozyLife socket")
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(self._ip_address, COZYLIFE_PORT),
                    timeout=self._timeout if timeout is None else timeout,
                )
            except (asyncio.TimeoutError, ConnectionRefusedError, OSError) as err:
                self._last_transport_error = type(err).__name__
                _RAW_LOGGER.debug("Could not open CozyLife socket: %s", type(err).__name__)
                self._disconnect()
                raise

            self._reader = reader
            if self._tcp_user_timeout_ms is not None:
                tcp_socket = writer.get_extra_info("socket")
                option = getattr(socket, "TCP_USER_TIMEOUT", None)
                if tcp_socket is not None and option is not None:
                    try:
                        tcp_socket.setsockopt(
                            socket.IPPROTO_TCP, option, self._tcp_user_timeout_ms
                        )
                    except OSError:
                        writer.close()
                        self._reader = None
                        raise
            self._writer = writer
            self._reader_task = asyncio.create_task(
                self._read_frames(reader, writer),
                name=f"cozylife-reader-{self._ip_address}",
            )
            self._last_transport_error = None
            _RAW_LOGGER.debug("Connected persistent CozyLife socket")

    def _disconnect(self) -> None:
        """Close the socket and release callers waiting for a response."""
        writer = self._writer
        self._reader = None
        self._writer = None
        if writer:
            try:
                writer.close()
            except Exception:
                pass

        for future in self._pending_responses.values():
            if not future.done():
                future.set_result(None)
        self._pending_responses.clear()
        for future in self._state_waiters:
            if not future.done():
                future.set_result({})
        self._state_waiters.clear()

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
                    _RAW_LOGGER.debug("Persistent CozyLife socket reached EOF")
                    return
                try:
                    frame = json.loads(raw_frame.decode("utf8").strip())
                except (json.JSONDecodeError, UnicodeDecodeError) as err:
                    _LOGGER.warning("Invalid CozyLife frame: %s", type(err).__name__)
                    continue

                _RAW_LOGGER.debug("Received CozyLife frame: %s", summarize_frame(raw_frame))
                self.last_frame_received_at = time.time()
                message = frame.get("msg")
                sent_sn = frame.get("sn")
                future = self._pending_responses.pop(sent_sn, None)
                if future and not future.done():
                    future.set_result(
                        frame.get("msg") if frame.get("res", 0) == 0 else None
                    )

                state_data = message.get("data") if isinstance(message, dict) else None
                if isinstance(state_data, dict):
                    normalized_state = {
                        str(key): value for key, value in state_data.items()
                    }
                    for state_waiter in self._state_waiters:
                        if not state_waiter.done():
                            state_waiter.set_result(normalized_state)
                    self._state_waiters.clear()

                listener = self._message_listener
                if listener is not None:
                    try:
                        result = listener(frame)
                        if inspect.isawaitable(result):
                            await result
                    except Exception:  # noqa: BLE001 - listener is integration code.
                        _LOGGER.exception(
                            "Error dispatching CozyLife frame",
                        )
        except (ConnectionError, OSError) as err:
            _LOGGER.debug("Persistent CozyLife connection ended: %s", type(err).__name__)
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
            encoded_message = self._encode_message(message)
            _RAW_LOGGER.debug("Sending CozyLife frame: %s", summarize_frame(encoded_message))
            writer.write(encoded_message)
            await writer.drain()
            try:
                return await asyncio.wait_for(asyncio.shield(response), timeout=self._timeout)
            except asyncio.TimeoutError:
                _LOGGER.warning("Timeout waiting for a CozyLife response")
                # A persistent socket whose peer stopped answering is not a
                # usable transport for the next request.  Discard it so the
                # next attempt performs a clean local reconnect.
                self._disconnect()
                return None
            finally:
                self._pending_responses.pop(sent_sn, None)
        except (asyncio.TimeoutError, ConnectionRefusedError, OSError) as err:
            _RAW_LOGGER.debug("CozyLife request transport failure: %s", type(err).__name__)
            self._disconnect()
            return None

    async def _async_send(self, cmd: int, payload: Dict[str, Any]) -> bool:
        """Write one APK-shaped local command without requiring an ACK frame.

        CozyLife's local writer marks a command sent after the socket write and
        leaves every later state frame to its address-routed reader.  In
        particular, a CMD_SET does not need a correlated response to be a
        successful device command.
        """
        try:
            await self._connect()
            writer = self._writer
            if writer is None:
                return False
            message = self._create_message(cmd, payload)
            encoded_message = self._encode_message(message)
            _RAW_LOGGER.debug("Sending CozyLife frame: %s", summarize_frame(encoded_message))
            writer.write(encoded_message)
            await asyncio.wait_for(writer.drain(), timeout=self._timeout)
            return True
        except (asyncio.TimeoutError, ConnectionRefusedError, OSError):
            self._disconnect()
            return False

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
            _LOGGER.warning("Device info query failed or omitted required fields")
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
                "DPID query attempt %s/%s failed",
                attempt,
                DPID_QUERY_ATTEMPTS,
            )

        if not query_msg:
            _LOGGER.warning("Failed to query DPID list from CozyLife device")
            # We can still proceed with a minimal setup if this fails
            self._dpid = self._catalog_backed_dpids([], model_info)
            return True # Return true as we have the basic info

        if 'attr' in query_msg:
            observed_dpids = [str(dpid) for dpid in query_msg['attr']]
        elif 'data' in query_msg:
            observed_dpids = [str(dpid) for dpid in query_msg['data']]
        else:
            observed_dpids = []
        self._dpid = self._catalog_backed_dpids(observed_dpids, model_info)

        _LOGGER.info(
            "Successfully discovered CozyLife device locally: PID=%s, Type=%s, DPIDs=%s",
            self.pid,
            self.device_type_code,
            self.dpid,
        )
        return True

    async def async_get_status(self) -> Optional[Dict[str, Any]]:
        """Request APK status and await the next state frame, not a matching SN."""
        return await self._async_get_state([1])

    async def async_get_state(self, dpids: list[str]) -> Optional[Dict[str, Any]]:
        """Read a documented subset of locally advertised DPIDs."""
        return await self._async_get_state([int(dpid) for dpid in dpids])

    async def async_request_properties(self, *, timeout: float | None = None) -> bool:
        """Request powered properties through the APK's listener-based path.

        The CozyLife Wi-Fi client sends ``attr:[0]`` and routes the next local
        state frame through the device listener. The local write is the request
        completion; a later frame updates the coordinator independently.
        """
        if self._status_lock is None:
            self._status_lock = asyncio.Lock()

        async with self._status_lock:
            return (
                await self._async_request_state(
                    [0], wait_for_state=False, transport_timeout=timeout
                )
                is not None
            )

    async def _async_get_state(self, attributes: list[int]) -> Optional[Dict[str, Any]]:
        """Request the selected APK state payload and await its state frame."""
        if self._status_lock is None:
            self._status_lock = asyncio.Lock()

        async with self._status_lock:
            return await self._async_request_state(
                attributes, wait_for_state=True
            )

    async def async_send_status(self) -> bool:
        """Queue one app-shaped status request on the retained local socket.

        The APK's local writer treats a completed socket write as sent and
        leaves later frames to its independent reader. Non-state frames are
        not a reason to disconnect a socket that may still deliver the
        sensor's report while it is awake.
        """
        if self._status_lock is None:
            self._status_lock = asyncio.Lock()

        async with self._status_lock:
            return (
                await self._async_request_state([1], wait_for_state=False)
            ) is not None

    async def _async_request_state(
        self,
        attributes: list[int],
        *,
        wait_for_state: bool,
        transport_timeout: float | None = None,
    ) -> Optional[Dict[str, Any]]:
        """Use the APK write-and-listen state path over one durable socket."""
        try:
            timeout = self._timeout if transport_timeout is None else transport_timeout
            await self._connect(timeout=timeout)
            writer = self._writer
            if writer is None:
                return None

            state_waiter: asyncio.Future[Dict[str, Any]] | None = None
            if wait_for_state:
                state_waiter = asyncio.get_running_loop().create_future()
                self._state_waiters.append(state_waiter)

            message = self._create_message(CMD_QUERY, {"attr": attributes})
            encoded_message = self._encode_message(message)
            _RAW_LOGGER.debug("Sending CozyLife frame: %s", summarize_frame(encoded_message))
            writer.write(encoded_message)
            await asyncio.wait_for(writer.drain(), timeout=timeout)

            if state_waiter is None:
                return {}

            try:
                state_data = await asyncio.wait_for(
                    asyncio.shield(state_waiter), timeout=timeout
                )
                return state_data or None
            except asyncio.TimeoutError:
                # A short no-reply window is the normal sleeping-sensor case,
                # not an integration error. Keep it available for diagnostics
                # without flooding the Home Assistant log at warning level.
                _LOGGER.debug(
                    "Timeout waiting for an APK-style CozyLife state frame",
                )
                return None
            finally:
                if state_waiter in self._state_waiters:
                    self._state_waiters.remove(state_waiter)
        except (asyncio.TimeoutError, ConnectionRefusedError, OSError):
            self._disconnect()
            return None

    async def async_set_state(self, attributes: Dict[str, Any]) -> bool:
        """Write a user command without waiting behind availability work.

        ``StreamWriter.write`` appends one complete framed command before its
        first await, so a CMD_SET cannot interleave bytes with the periodic
        ``attr:[0]`` request.  It must not share the refresh lock: a stalled
        availability connection otherwise delays an interactive ON/OFF action
        for the complete local TCP timeout.
        """
        return await self._async_send(CMD_SET, attributes)
