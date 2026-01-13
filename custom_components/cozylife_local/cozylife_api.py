import asyncio
import json
import logging
import socket
import time
from typing import Any, Dict, List, Optional

import aiohttp

_LOGGER = logging.getLogger(__name__)

COZYLIFE_PORT = 5555
CMD_INFO = 0
CMD_QUERY = 2
CMD_SET = 3

import asyncio
import json
import logging
import time
from typing import Any, Dict, List, Optional

import aiohttp

_LOGGER = logging.getLogger(__name__)

COZYLIFE_PORT = 5555
CMD_INFO = 0
CMD_QUERY = 2
CMD_SET = 3

def _get_sn() -> str:
    """Generates a unique sequence number based on current timestamp."""
    return str(int(time.time() * 1000))

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
        self._lock = asyncio.Lock()  # Prevent concurrent network operations

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

    async def _connect(self):
        """Establishes an asynchronous TCP connection to the device."""
        if self._reader and self._writer:
            return

        try:
            self._reader, self._writer = await asyncio.open_connection(
                self._ip_address, COZYLIFE_PORT
            )
            _LOGGER.debug(f"Connected to CozyLife device at {self._ip_address}:{COZYLIFE_PORT}")
        except (asyncio.TimeoutError, ConnectionRefusedError, OSError) as e:
            _LOGGER.error(f"Failed to connect to CozyLife device at {self._ip_address}: {e}")
            self._disconnect()
            raise

    def _disconnect(self):
        """Closes the TCP connection."""
        if self._writer:
            try:
                self._writer.close()
            except Exception:
                pass
        self._reader = None
        self._writer = None
        _LOGGER.debug(f"Disconnected from CozyLife device at {self._ip_address}")

    async def _send_receive(self, cmd: int, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Sends a command and awaits a matching response.
        This method will loop and read messages from the socket until a response
        with a matching sequence number (sn) is found or a timeout occurs.
        """
        async with self._lock:  # Ensure only one network operation at a time
            try:
                await self._connect()
                message = self._create_message(cmd, payload)
                sent_sn = message.get('sn')
                encoded_message = (json.dumps(message, separators=(',', ':')) + "\r\n").encode('utf8')

                _LOGGER.debug(f"Sending to {self._ip_address}: {encoded_message!r}")
                self._writer.write(encoded_message)
                await self._writer.drain()

                end_time = time.monotonic() + self._timeout
                while time.monotonic() < end_time:
                    try:
                        remaining_time = end_time - time.monotonic()
                        if remaining_time <= 0:
                            raise asyncio.TimeoutError

                        response_data = await asyncio.wait_for(self._reader.readline(), timeout=remaining_time)

                        if not response_data:
                            _LOGGER.debug(f"Connection closed by {self._ip_address} while waiting for response.")
                            self._disconnect()
                            return None

                        _LOGGER.debug(f"Received from {self._ip_address}: {response_data!r}")
                        resp_json = json.loads(response_data.decode('utf8').strip())

                        if resp_json.get('sn') == sent_sn:
                            if resp_json.get('res') == 0:
                                return resp_json.get('msg')
                            else:
                                _LOGGER.warning(f"Received error response for sn {sent_sn}: {resp_json}")
                                return None
                        else:
                            _LOGGER.debug(f"Discarding message with mismatched sn: {resp_json}")

                    except asyncio.TimeoutError:
                        _LOGGER.warning(f"Timeout waiting for response with sn {sent_sn} from {self._ip_address}")
                        return None
                    except (json.JSONDecodeError, UnicodeDecodeError) as e:
                        _LOGGER.warning(f"Error decoding response from {self._ip_address}: {e} - Data: {response_data!r}")

                _LOGGER.warning(f"Outer loop timeout for sn {sent_sn}, no matching response received.")
                return None

            except (ConnectionRefusedError, OSError) as e:
                _LOGGER.error(f"Communication error with {self._ip_address}: {e}")
                self._disconnect()
                return None
            finally:
                self._disconnect()

    def _create_message(self, cmd: int, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Helper to create the JSON message structure."""
        sn = _get_sn()
        if cmd == CMD_SET:
            return {
                'pv': 0, 'cmd': cmd, 'sn': sn,
                'msg': {'attr': [int(k) for k in payload.keys()], 'data': payload}
            }
        elif cmd == CMD_QUERY:
            return {'pv': 0, 'cmd': cmd, 'sn': sn, 'msg': {'attr': [0]}}
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
        # Use a generic model name as the friendly name is no longer available from the cloud
        self._device_model_name = f"CozyLife Device ({self._pid})"

        # Step 2: Get the full list of supported DPIDs
        query_msg = await self._send_receive(CMD_QUERY, {})
        if not query_msg or 'attr' not in query_msg:
            _LOGGER.warning(f"Failed to query DPID list from device: {query_msg}")
            # We can still proceed with a minimal setup if this fails
            self._dpid = []
            return True # Return true as we have the basic info

        self._dpid = [str(dpid) for dpid in query_msg['attr']]

        _LOGGER.info(
            f"Device query successful - {self._ip_address}: "
            f"DID={self.device_id}, PID={self.pid}, Type={self.device_type_code}, DPIDs={self.dpid}"
        )
        return True

    async def async_get_state(self) -> Optional[Dict[str, Any]]:
        """Queries the current state of the device."""
        msg = await self._send_receive(CMD_QUERY, {})
        if msg and 'data' in msg:
            return msg['data']
        return None

    async def async_set_state(self, attributes: Dict[str, Any]) -> bool:
        """Sets attributes on the device."""
        msg = await self._send_receive(CMD_SET, attributes)
        if msg and 'data' in msg:
            return True
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
