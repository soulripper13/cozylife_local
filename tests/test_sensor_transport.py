"""Protocol checks for the APK-derived sleeping-sensor transport."""

from __future__ import annotations

import asyncio
import json
import sys
import time
import types
from pathlib import Path


COMPONENT_DIR = Path(__file__).parents[1] / "custom_components" / "cozylife_local"


def _load_api():
    """Load the API module without importing Home Assistant's package setup."""
    homeassistant = types.ModuleType("homeassistant")
    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = object
    sys.modules["homeassistant"] = homeassistant
    sys.modules["homeassistant.core"] = core
    package = types.ModuleType("cozylife_local")
    package.__path__ = [str(COMPONENT_DIR)]
    sys.modules["cozylife_local"] = package
    from cozylife_local.cozylife_api import CozyLifeDevice

    return CozyLifeDevice


def _load_coordinator():
    """Load the coordinator with the small Home Assistant surface it needs."""
    _load_api()
    config_entries = types.ModuleType("homeassistant.config_entries")
    config_entries.ConfigEntry = object
    helpers = types.ModuleType("homeassistant.helpers")
    update_coordinator = types.ModuleType("homeassistant.helpers.update_coordinator")

    class DataUpdateCoordinator:
        @classmethod
        def __class_getitem__(cls, _item):
            return cls

    update_coordinator.DataUpdateCoordinator = DataUpdateCoordinator
    update_coordinator.UpdateFailed = RuntimeError
    util = types.ModuleType("homeassistant.util")
    dt = types.ModuleType("homeassistant.util.dt")
    sys.modules["homeassistant.config_entries"] = config_entries
    sys.modules["homeassistant.helpers"] = helpers
    sys.modules["homeassistant.helpers.update_coordinator"] = update_coordinator
    sys.modules["homeassistant.util"] = util
    sys.modules["homeassistant.util.dt"] = dt
    sys.modules.pop("cozylife_local.coordinator", None)
    from cozylife_local.coordinator import CozyLifeCoordinator

    return CozyLifeCoordinator


def test_status_write_uses_apk_frame_and_dispatches_unsolicited_report() -> None:
    """The local writer sends attr:[1] and the reader keeps every report."""
    CozyLifeDevice = _load_api()

    async def exercise() -> None:
        reports: list[dict] = []

        async def handler(reader, writer) -> None:
            request = json.loads((await reader.readline()).decode())
            assert request["cmd"] == 2
            assert request["pv"] == 0
            assert request["msg"] == {"attr": [1]}
            writer.write(
                b'{"cmd":2,"sn":"device-event","msg":{"data":{"4":48,"8":291}}}\r\n'
            )
            await writer.drain()

        server = await asyncio.start_server(handler, "127.0.0.1", 5555)
        device = CozyLifeDevice("127.0.0.1", timeout=1.0)
        device.set_message_listener(reports.append)
        try:
            assert await device.async_send_status()
            await asyncio.sleep(0.05)
            assert reports == [
                {"cmd": 2, "sn": "device-event", "msg": {"data": {"4": 48, "8": 291}}}
            ]
        finally:
            await device.async_close()
            server.close()
            await server.wait_closed()

    asyncio.run(exercise())


def test_status_socket_is_recycled_on_the_configured_cadence() -> None:
    """A sleeping sensor gets a fresh TCP connection after thirty seconds."""
    CozyLifeDevice = _load_api()

    async def exercise() -> None:
        connections = 0

        async def handler(reader, writer) -> None:
            nonlocal connections
            connections += 1
            while await reader.readline():
                writer.write(b'{"cmd":2,"sn":"report","msg":{"data":{"4":50}}}\r\n')
                await writer.drain()

        server = await asyncio.start_server(handler, "127.0.0.1", 5555)
        device = CozyLifeDevice("127.0.0.1", timeout=1.0)
        try:
            assert await device.async_send_status()
            await asyncio.sleep(0.05)
            device._connected_at = time.monotonic() - 30.1
            assert await device.async_send_status()
            await asyncio.sleep(0.05)
            assert connections == 2
        finally:
            await device.async_close()
            server.close()
            await server.wait_closed()

    asyncio.run(exercise())


def test_concurrent_status_requests_share_one_persistent_socket() -> None:
    """Discovery and wake probing cannot open competing sensor sockets."""
    CozyLifeDevice = _load_api()

    async def exercise() -> None:
        connections = 0
        requests: list[dict] = []

        async def handler(reader, _writer) -> None:
            nonlocal connections
            connections += 1
            for _ in range(2):
                requests.append(json.loads((await reader.readline()).decode()))

        server = await asyncio.start_server(handler, "127.0.0.1", 5555)
        device = CozyLifeDevice("127.0.0.1", timeout=1.0)
        try:
            assert all(await asyncio.gather(
                device.async_send_status(), device.async_send_status()
            ))
            await asyncio.sleep(0.05)
            assert connections == 1
            assert [request["msg"] for request in requests] == [
                {"attr": [1]},
                {"attr": [1]},
            ]
        finally:
            await device.async_close()
            server.close()
            await server.wait_closed()

    asyncio.run(exercise())


def test_status_send_reconnects_after_the_peer_closes() -> None:
    """A closed socket is not reused for a later sleeping-sensor wake."""
    CozyLifeDevice = _load_api()

    async def exercise() -> None:
        connections = 0

        async def handler(reader, writer) -> None:
            nonlocal connections
            connections += 1
            await reader.readline()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(handler, "127.0.0.1", 5555)
        device = CozyLifeDevice("127.0.0.1", timeout=1.0)
        try:
            assert await device.async_send_status()
            await asyncio.sleep(0.05)
            assert await device.async_send_status()
            await asyncio.sleep(0.05)
            assert connections == 2
        finally:
            await device.async_close()
            server.close()
            await server.wait_closed()

    asyncio.run(exercise())


def test_discovery_accepts_only_the_matching_device_and_valid_address() -> None:
    """UDP discovery must not route another CozyLife device into this entry."""
    _load_api()
    from cozylife_local.cozylife_api import _CozyLifeDiscoveryProtocol

    async def exercise() -> None:
        found: list[str] = []

        async def remember(ip_address: str) -> None:
            found.append(ip_address)

        protocol = _CozyLifeDiscoveryProtocol("target-device", remember)
        protocol.datagram_received(
            b'{"msg":{"did":"other-device","ip":"10.0.0.99"}}\r\n',
            ("10.0.0.99", 6095),
        )
        protocol.datagram_received(
            b'{"msg":{"did":"target-device","ip":""}}\r\n',
            ("10.0.0.115", 6095),
        )
        protocol.datagram_received(
            b'{"msg":{"did":"target-device","ip":"10.0.0.115"}}\r\n',
            ("10.0.0.115", 6095),
        )
        await asyncio.sleep(0)
        assert found == ["10.0.0.115"]

    asyncio.run(exercise())


def test_persistent_transport_preserves_existing_info_state_and_set_commands() -> None:
    """The reader-based transport still supports ordinary light/switch calls."""
    CozyLifeDevice = _load_api()

    async def exercise() -> None:
        requests: list[dict] = []

        async def handler(reader, writer) -> None:
            while raw_request := await reader.readline():
                request = json.loads(raw_request.decode())
                requests.append(request)
                if request["cmd"] == 0:
                    message = {"did": "device-1", "pid": "unknown", "dtp": "00"}
                elif request["cmd"] == 2 and request["msg"]["attr"] == [0]:
                    message = {"attr": [1, 11, 18, 19, 20]}
                elif request["cmd"] == 2:
                    message = {"data": {"1": True, "11": 42}}
                else:
                    message = {"data": request["msg"]["data"]}
                response = {"sn": request["sn"], "res": 0, "msg": message}
                writer.write((json.dumps(response) + "\r\n").encode())
                await writer.drain()

        server = await asyncio.start_server(handler, "127.0.0.1", 5555)
        device = CozyLifeDevice("127.0.0.1", timeout=1.0)
        try:
            assert await device.async_update_device_info()
            assert device.device_id == "device-1"
            assert device.dpid == ["1", "11", "18", "19", "20"]
            assert await device.async_get_state(["1", "11"]) == {"1": True, "11": 42}
            assert await device.async_set_state({"1": False})
            assert len(requests) == 4
            assert requests[-1]["cmd"] == 3
            assert requests[-1]["msg"] == {"attr": [1], "data": {"1": False}}
        finally:
            await device.async_close()
            server.close()
            await server.wait_closed()

    asyncio.run(exercise())


def test_request_sequence_numbers_are_unique_within_one_millisecond() -> None:
    """Concurrent pending requests must never share the reply correlation key."""
    _load_api()
    from cozylife_local import cozylife_api

    original_time = cozylife_api.time.time
    original_last_sn = cozylife_api._last_sn
    try:
        cozylife_api.time.time = lambda: 1_700_000_000.0
        cozylife_api._last_sn = 0
        sequence_numbers = [cozylife_api._get_sn() for _ in range(3)]
    finally:
        cozylife_api.time.time = original_time
        cozylife_api._last_sn = original_last_sn

    assert sequence_numbers == ["1700000000000", "1700000000001", "1700000000002"]


def test_state_request_reconnects_after_a_response_timeout() -> None:
    """A timed-out persistent request does not leave a stale socket in use."""
    CozyLifeDevice = _load_api()

    async def exercise() -> None:
        connections = 0

        async def handler(reader, writer) -> None:
            nonlocal connections
            connections += 1
            request = json.loads((await reader.readline()).decode())
            if connections == 1:
                await asyncio.sleep(0.15)
                return
            writer.write(
                (json.dumps({
                    "sn": request["sn"],
                    "res": 0,
                    "msg": {"data": {"1": True}},
                }) + "\r\n").encode()
            )
            await writer.drain()

        server = await asyncio.start_server(handler, "127.0.0.1", 5555)
        device = CozyLifeDevice("127.0.0.1", timeout=0.05)
        try:
            assert await device.async_get_state() is None
            assert await device.async_get_state() == {"1": True}
            assert connections == 2
        finally:
            await device.async_close()
            server.close()
            await server.wait_closed()

    asyncio.run(exercise())


def test_coordinator_publishes_partial_inbound_reports_without_a_refresh() -> None:
    """An unsolicited device report updates only the DPIDs it contains."""
    CozyLifeCoordinator = _load_coordinator()

    class FakeCoordinator:
        data = {"1": False, "11": 42}

        class Device:
            ip_address = "127.0.0.1"

        device = Device()

        def __init__(self) -> None:
            self.published: list[dict] = []

        def async_set_updated_data(self, data: dict) -> None:
            self.published.append(data)
            self.data = data

    async def exercise() -> None:
        coordinator = FakeCoordinator()
        await CozyLifeCoordinator._async_handle_device_message(
            coordinator,
            {"cmd": 2, "msg": {"data": {"1": True}}},
        )
        await CozyLifeCoordinator._async_handle_device_message(
            coordinator,
            {"cmd": 2, "msg": {"attr": [1]}},
        )
        assert coordinator.published == [{"1": True, "11": 42}]

    asyncio.run(exercise())
