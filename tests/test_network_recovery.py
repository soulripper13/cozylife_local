"""Real TCP peers reproducing silent sessions after network interruption."""

import asyncio
import ast
import json
import time
import types
from unittest.mock import patch

import pytest

from test_sensor_transport import COMPONENT_DIR, _load_api, _load_coordinator


def test_silent_powered_writes_expire_and_real_state_restores_health():
    from unittest.mock import AsyncMock

    Coordinator = _load_coordinator()

    async def exercise():
        coordinator = object.__new__(Coordinator)
        coordinator._uses_sensor_polling = False
        coordinator.device = types.SimpleNamespace(
            pid="v3sjfa", async_request_properties=AsyncMock(return_value=True)
        )
        coordinator.data = {"16": "007803e8ffff"}
        coordinator.last_update_success = True

        def publish(data):
            coordinator.data = data
            coordinator.last_update_success = True

        coordinator.async_set_updated_data = publish
        clock = types.SimpleNamespace(monotonic=lambda: 100.0)
        with patch("cozylife_local.coordinator.time", clock):
            await coordinator._async_get_local_state()
            coordinator.last_update_success = False
            with pytest.raises(RuntimeError, match="No recent CozyLife device state"):
                await coordinator._async_get_local_state()
            assert not coordinator.last_update_success
            await coordinator._async_handle_device_message({"msg": {"data": {"1": 1}}})
            assert coordinator.last_update_success
            clock.monotonic = lambda: 161.0
            for _ in range(2):
                with pytest.raises(RuntimeError, match="No recent CozyLife device state"):
                    await coordinator._async_get_local_state()
            await coordinator._async_handle_device_message({"msg": {"data": {"3": 500}}})
            assert await coordinator._async_get_local_state() == {"1": 1, "3": 500}
            await coordinator._async_handle_device_message(
                {"msg": {"data": {"16": "007803e8ffff"}}}
            )
            assert coordinator.data["16"] == "007803e8ffff"

    asyncio.run(exercise())


def test_initial_network_failure_requests_setup_retry():
    """Incomplete cached entries must not become permanent setup failures."""
    from unittest.mock import AsyncMock, Mock

    tree = ast.parse((COMPONENT_DIR / "__init__.py").read_text())
    setup = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef)
                 and node.name == "_async_setup_device_entry")

    class NotReady(Exception):
        pass

    namespace = {"ConfigEntryNotReady": NotReady, "_LOGGER": Mock(),
                 "_remove_debug_entry": Mock()}
    exec(compile(ast.Module(body=[setup], type_ignores=[]), "setup-retry", "exec"), namespace)

    async def exercise():
        entry = types.SimpleNamespace(data={"ip_address": "192.0.2.1"}, entry_id="entry",
                                      async_on_unload=Mock(), add_update_listener=Mock())
        device = types.SimpleNamespace(
            async_update_device_info=AsyncMock(side_effect=[False, True]),
            async_close=AsyncMock(), device_id="did", pid="pid",
            device_type_code="00", dpid=["1", "27", "29"])
        coordinator = types.SimpleNamespace(async_start_discovery=AsyncMock(),
                                             async_config_entry_first_refresh=AsyncMock())
        hass = types.SimpleNamespace(data={"cozylife_local": {}},
            async_create_task=asyncio.create_task,
            config_entries=types.SimpleNamespace(async_update_entry=Mock(),
                                                   async_forward_entry_setups=AsyncMock()))
        namespace.update(DOMAIN="cozylife_local", PLATFORMS=[],
            CozyLifeCoordinator=Mock(return_value=coordinator),
            _async_remove_obsolete_schedule_entities=Mock(), async_reload_entry=Mock())
        with pytest.raises(NotReady):
            await namespace["_async_setup_device_entry"](hass, entry, device, None)
        assert "entry" not in hass.data["cozylife_local"]
        assert await namespace["_async_setup_device_entry"](hass, entry, device, None)
        await asyncio.sleep(0)
        coordinator.async_config_entry_first_refresh.assert_awaited_once()
        hass.config_entries.async_forward_entry_setups.assert_awaited_once()
        assert hass.data["cozylife_local"]["entry"] is coordinator

    asyncio.run(exercise())


def test_powered_coordinator_recovers_state_after_network_outage():
    """Trace requests, a refused reconnect, incoming state, and coordinator data."""
    Device = _load_api()
    Coordinator = _load_coordinator()

    async def exercise():
        queue = asyncio.Queue()
        clock = [0.0]
        phase = ["healthy"]
        tasks = []
        writers = []
        state = {"1": 1, "2": 0, "18": 2, "19": 1, "26": 904,
                 "27": 1250, "28": 133, "29": 2300, "32": 1, "42": 1414}

        async def peer(reader, writer):
            tasks.append(asyncio.current_task())
            writers.append(writer)
            try:
                while raw := await reader.readline():
                    json.loads(raw)
                    if phase[0] != "silent":
                        writer.write((json.dumps({"cmd": 2, "msg": {"data": state}}) + "\r\n").encode())
                        await writer.drain()
            except ConnectionError:
                pass
            finally:
                writer.close()

        server = await asyncio.start_server(peer, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        device = Device("127.0.0.1", timeout=0.2)
        device.restore_from_cache("test-did", "test-powered", "00", list(state))
        coordinator = object.__new__(Coordinator)
        coordinator.device = device
        coordinator._uses_sensor_polling = False
        coordinator._closed = False
        coordinator._sensor_status_task = None
        coordinator._identity_refresh_task = None
        coordinator._powered_followup_task = None
        coordinator.classification = types.SimpleNamespace(
            is_environment_sensor=False, supports_switch_entities=True,
            supports_plug_metering=True, is_light=False)
        coordinator._entry = types.SimpleNamespace(data={"ip_address": "127.0.0.1"})
        coordinator.data = {}
        coordinator.last_update_success = True

        def publish(data):
            coordinator.data = data
            coordinator.last_update_success = True
            queue.put_nowait(dict(data))

        coordinator.async_set_updated_data = publish
        device.set_message_listener(coordinator._async_handle_device_message)
        fake_time = types.SimpleNamespace(time=time.time, monotonic=lambda: clock[0])
        try:
            with patch("cozylife_local.cozylife_api.COZYLIFE_PORT", port), patch("cozylife_local.cozylife_api.time", fake_time):
                await coordinator._async_update_data()
                assert (await asyncio.wait_for(queue.get(), 1))["29"] == 2300
                phase[0] = "silent"
                await coordinator._async_update_data()
                # New connections are refused while the old session is silent.
                server.close()
                clock[0] = 31
                with pytest.raises(RuntimeError):
                    await coordinator._async_update_data()
                await server.wait_closed()
                coordinator.last_update_success = False
                assert coordinator.data["26"] == 904
                phase[0] = "restored"
                state.update({"1": 0, "27": 0, "28": 0, "29": 2310})
                server = await asyncio.start_server(peer, "127.0.0.1", port)
                try:
                    await coordinator._async_update_data()
                except RuntimeError as err:
                    # The reader may receive state after the write completes.
                    assert str(err) == "No recent CozyLife device state"
                recovered = await asyncio.wait_for(queue.get(), 1)
                assert coordinator.last_update_success
                assert recovered["1"] == 0
                assert recovered["27"] == recovered["28"] == 0
                assert recovered["29"] == 2310
                assert len(writers) == 2
        finally:
            await coordinator.async_close()
            server.close()
            await server.wait_closed()
            for writer in writers:
                writer.close()
            await asyncio.gather(*tasks)

    asyncio.run(exercise())


@pytest.mark.parametrize("operation", ["properties", "control", "identity"])
def test_old_write_failure_cannot_disconnect_recovered_session(operation):
    """A late failure belongs to its old socket, not the replacement."""
    Device = _load_api()

    async def exercise():
        started = asyncio.Event()
        fail = asyncio.Event()

        class Writer:
            closed = False

            def is_closing(self):
                return self.closed

            def close(self):
                self.closed = True

            def write(self, _data):
                pass

            async def drain(self):
                started.set()
                await fail.wait()
                raise ConnectionResetError("old network session failed")

        device = Device("192.0.2.1", timeout=1)
        old = Writer()
        device._writer = old
        if operation == "properties":
            request = device.async_request_properties()
        elif operation == "control":
            request = device.async_set_state({"1": 1})
        else:
            request = device._send_receive(0, {})
        task = asyncio.create_task(request)
        await asyncio.wait_for(started.wait(), 1)
        # EOF/network recovery retires the old session while drain is pending.
        device._disconnect()
        replacement = Writer()
        device._writer = replacement
        waiter = asyncio.get_running_loop().create_future()
        device._state_waiters.append(waiter)
        fail.set()
        await asyncio.wait_for(task, 1)
        assert device._writer is replacement
        assert not replacement.closed
        assert not waiter.done()
        await device.async_close()

    asyncio.run(exercise())


@pytest.mark.parametrize("old_reply", [None, {}, {"res": 1, "msg": {"data": {"1": 1}}}])
@pytest.mark.parametrize("connection_refused", [False, True])
def test_unanswered_powered_connection_is_replaced(old_reply, connection_refused):
    """Writable but silent/empty/error sessions cannot persist indefinitely."""
    Device = _load_api()

    async def exercise():
        connections = []
        requests = []
        tasks = []
        received = asyncio.Queue()
        reports = asyncio.Queue()
        clock = [0.0]
        open_connection = asyncio.open_connection
        connect_attempts = 0

        async def connect(*args, **kwargs):
            nonlocal connect_attempts
            connect_attempts += 1
            if connection_refused and connect_attempts == 2:
                raise ConnectionRefusedError("network not yet restored")
            return await open_connection(*args, **kwargs)

        async def peer(reader, writer):
            tasks.append(asyncio.current_task())
            connections.append(writer)
            number = len(connections)
            try:
                while raw := await reader.readline():
                    assert raw.endswith(b"\r\n")
                    frame = json.loads(raw)
                    requests.append((number, frame))
                    if number > 1:
                        reply = {"cmd": 2, "msg": {"data": {"1": 0, "29": 2300}}}
                    else:
                        reply = old_reply
                    if reply is not None:
                        writer.write((json.dumps(reply) + "\r\n").encode())
                        await writer.drain()
                    await received.put(number)
            except ConnectionError:
                pass
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except ConnectionError:
                    pass

        server = await asyncio.start_server(peer, "127.0.0.1", 0)
        device = Device("127.0.0.1", timeout=0.2)
        device.set_message_listener(reports.put_nowait)
        fake_time = types.SimpleNamespace(time=time.time, monotonic=lambda: clock[0])
        try:
            with patch("cozylife_local.cozylife_api.COZYLIFE_PORT", server.sockets[0].getsockname()[1]), patch("cozylife_local.cozylife_api.time", fake_time), patch("asyncio.open_connection", connect):
                assert await device.async_request_properties()
                assert await asyncio.wait_for(received.get(), 1) == 1
                clock[0] = 10
                assert await device.async_request_properties()
                assert await asyncio.wait_for(received.get(), 1) == 1
                # Repeated discovery writes must not postpone recovery.
                clock[0] = 31
                if connection_refused:
                    assert not await device.async_request_properties()
                assert await device.async_request_properties()
                assert await asyncio.wait_for(received.get(), 1) == 2
                while True:
                    frame = await asyncio.wait_for(reports.get(), 1)
                    if frame.get("msg", {}).get("data", {}).get("29") == 2300:
                        break
                assert device._properties_requested_at is None
                assert await device.async_set_state({"1": 1})
                assert await asyncio.wait_for(received.get(), 1) == 2
                assert sum(frame["cmd"] == 3 for _, frame in requests) == 1
                assert len(connections) == 2
        finally:
            await device.async_close()
            server.close()
            await server.wait_closed()
            for writer in connections:
                writer.close()
            await asyncio.gather(*tasks)

    asyncio.run(exercise())


def test_valid_partial_zero_report_keeps_powered_connection():
    """Asynchronous, uncorrelated, partial reports are real receive evidence."""
    Device = _load_api()

    async def exercise():
        clock = [0.0]
        queue = asyncio.Queue()
        connections = []
        tasks = []

        async def peer(reader, writer):
            connections.append(writer)
            tasks.append(asyncio.current_task())
            try:
                while await reader.readline():
                    # No matching SN; a zero reading still confirms reception.
                    writer.write(b'{"cmd":10,"msg":{"data":{"28":0}}}\r\n')
                    await writer.drain()
            except ConnectionError:
                pass
            finally:
                writer.close()

        server = await asyncio.start_server(peer, "127.0.0.1", 0)
        device = Device("127.0.0.1", timeout=0.2)
        device.set_message_listener(queue.put_nowait)
        fake_time = types.SimpleNamespace(time=time.time, monotonic=lambda: clock[0])
        try:
            with patch("cozylife_local.cozylife_api.COZYLIFE_PORT", server.sockets[0].getsockname()[1]), patch("cozylife_local.cozylife_api.time", fake_time):
                for now in (0, 31, 120):
                    clock[0] = now
                    assert await device.async_request_properties()
                    assert (await asyncio.wait_for(queue.get(), 1))["msg"]["data"] == {"28": 0}
                    assert device._properties_requested_at is None
                assert len(connections) == 1
        finally:
            await device.async_close()
            server.close()
            await server.wait_closed()
            await asyncio.gather(*tasks)

    asyncio.run(exercise())


def test_sleeping_sensor_silence_does_not_start_powered_watchdog():
    """The sensor's independent wake-catching path retains its own policy."""
    Device = _load_api()

    async def exercise():
        device = Device("192.0.2.1")

        class Writer:
            def is_closing(self):
                return False

            def write(self, data):
                assert not data.endswith(b"\r\n")

            async def drain(self):
                pass

            def close(self):
                pass

        writer = Writer()
        device._pid = "Z4tRml"
        device._writer = writer
        try:
            for _ in range(3):
                assert await device.async_send_status()
                assert device._writer is writer
                assert device._properties_requested_at is None
        finally:
            await device.async_close()

    asyncio.run(exercise())


def test_separate_white_reports_supersede_cached_packed_white():
    Coordinator = _load_coordinator()
    coordinator = object.__new__(Coordinator)
    coordinator._uses_sensor_polling = False
    coordinator.data = {"16": "FFFFFFFF01F4"}
    coordinator.async_set_updated_data = lambda data: setattr(coordinator, "data", data)

    async def exercise():
        async def report(data):
            await coordinator._async_handle_device_message({"msg": {"data": data}})

        await report({"3": 65535})
        assert coordinator.data["16"] == "FFFFFFFF01F4"
        await report({"3": 0})
        assert coordinator.data == {"3": 0}
        await report({"16": "ffffffff03e8"})
        assert coordinator.data == {"3": 0, "16": "ffffffff03e8"}
        await report({"3": 100, "16": "ffffffff0200"})
        assert coordinator.data == {"3": 100, "16": "ffffffff0200"}

    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("delay", "should_connect"),
    [(1.3, True), (3.3, False)],
)
def test_powered_refresh_connect_budget(delay, should_connect):
    Device = _load_api()
    Coordinator = _load_coordinator()

    async def exercise():
        commands = asyncio.Queue()
        reports = asyncio.Queue()
        release_reply = asyncio.Event()
        peer_done = asyncio.Event()
        open_connection = asyncio.open_connection

        async def connect(*args, **kwargs):
            await asyncio.sleep(delay)
            return await open_connection(*args, **kwargs)

        async def peer(reader, writer):
            try:
                await commands.put(json.loads(await reader.readline()))
                await release_reply.wait()
                writer.write(b'{"cmd":2,"res":0,"msg":{"data":{"1":1}}}\r\n')
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()
                peer_done.set()

        listener = await asyncio.start_server(peer, "127.0.0.1", 0)
        device = Device("127.0.0.1")
        device.set_message_listener(reports.put_nowait)
        coordinator = object.__new__(Coordinator)
        coordinator.device = device
        coordinator._uses_sensor_polling = False
        coordinator.data = {"1": 0}
        try:
            with patch("cozylife_local.cozylife_api.COZYLIFE_PORT",
                       listener.sockets[0].getsockname()[1]), patch(
                           "asyncio.open_connection", connect
                       ):
                if should_connect:
                    assert await coordinator._async_get_local_state() == {"1": 0}
                    frame = await asyncio.wait_for(commands.get(), 1)
                    assert frame["cmd"] == 2
                    assert frame["msg"] == {"attr": [0]}
                    assert reports.empty()
                    release_reply.set()
                    report = await asyncio.wait_for(reports.get(), 1)
                    assert report["msg"]["data"] == {"1": 1}
                    await asyncio.wait_for(peer_done.wait(), 1)
                else:
                    with pytest.raises(RuntimeError, match=r"TCP connect: TimeoutError"):
                        await coordinator._async_get_local_state()
                    assert commands.empty()
        finally:
            release_reply.set()
            await device.async_close()
            listener.close()
            await listener.wait_closed()

    asyncio.run(exercise())


def test_address_change_retires_a_socket_opened_during_discovery():
    """A completed dial to the old address must not survive an address change."""
    Device = _load_api()

    async def exercise():
        dial_started = asyncio.Event()
        finish_dial = asyncio.Event()
        dial_addresses = []
        writers = []
        open_connection = asyncio.open_connection

        async def connect(address, port):
            dial_addresses.append(address)
            if len(dial_addresses) == 1:
                dial_started.set()
                await finish_dial.wait()
            reader, writer = await open_connection("127.0.0.1", port)
            writers.append(writer)
            return reader, writer

        async def peer(reader, writer):
            try:
                await reader.read()
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(peer, "127.0.0.1", 0)
        device = Device("192.0.2.10")
        try:
            with (
                patch("asyncio.open_connection", connect),
                patch("cozylife_local.cozylife_api.COZYLIFE_PORT",
                      server.sockets[0].getsockname()[1]),
            ):
                opening = asyncio.create_task(device._connect())
                await asyncio.wait_for(dial_started.wait(), 1)
                moving = asyncio.create_task(device.async_update_ip_address("192.0.2.11"))
                await asyncio.sleep(0)
                finish_dial.set()
                await opening
                assert await moving
                await device._connect()
                assert dial_addresses == ["192.0.2.10", "192.0.2.11"]
                assert writers[0].is_closing()
                assert device.is_connected
        finally:
            await device.async_close()
            server.close()
            await server.wait_closed()

    asyncio.run(exercise())


def test_close_does_not_allow_an_inflight_dial_to_restore_the_socket():
    """The entry may unload while its TCP connection is still opening."""
    Device = _load_api()

    async def exercise():
        dial_started = asyncio.Event()
        finish_dial = asyncio.Event()
        writer = types.SimpleNamespace(closed=False)
        writer.close = lambda: setattr(writer, "closed", True)

        async def connect(*_args):
            dial_started.set()
            await finish_dial.wait()
            return object(), writer

        device = Device("192.0.2.10")
        with patch("asyncio.open_connection", connect):
            opening = asyncio.create_task(device._connect())
            await asyncio.wait_for(dial_started.wait(), 1)
            await device.async_close()
            finish_dial.set()
            await opening
            assert writer.closed
            assert device._writer is None
            assert device._reader_task is None

    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("stage", "error", "detail"),
    [
        ("connect", ConnectionRefusedError, "TCP connect: ConnectionRefusedError"),
        ("write", BrokenPipeError, "TCP write: BrokenPipeError"),
        ("write", TimeoutError, "TCP write: TimeoutError"),
    ],
)
def test_powered_request_logs_safe_failure_stage(stage, error, detail):
    """Surface the reason through HA's coordinator without logging exception text."""
    Device = _load_api()
    Coordinator = _load_coordinator()

    async def exercise():
        address = "192.0.2.42"
        device = Device(address)
        coordinator = object.__new__(Coordinator)
        coordinator.device = device
        coordinator._uses_sensor_polling = False
        coordinator.data = {"1": 1}
        fail = True

        class Writer:
            def write(self, _data):
                pass

            async def drain(self):
                if stage == "write" and fail:
                    raise error(f"private endpoint {address}")

            def close(self):
                pass

        async def connect():
            if stage == "connect" and fail:
                raise error(f"private endpoint {address}")
            device._writer = Writer()

        with patch.object(device, "_connect", connect):
            with pytest.raises(RuntimeError) as caught:
                await coordinator._async_get_local_state()
            assert str(caught.value) == (
                f"Failed to request state from CozyLife device ({detail})"
            )
            fail = False
            assert await coordinator._async_get_local_state() == {"1": 1}
            assert device.last_properties_request_failure is None

    asyncio.run(exercise())


def test_powered_poll_reconnects_when_existing_socket_drops_during_write():
    """A peer closing during a poll must not force a false availability failure."""
    Device = _load_api()

    async def exercise():
        connections = asyncio.Queue()
        reports = asyncio.Queue()

        async def peer(reader, writer):
            try:
                await connections.put(writer)
                while line := await reader.readline():
                    assert json.loads(line)["msg"] == {"attr": [0]}
                    writer.write(b'{"cmd":2,"msg":{"data":{"1":1}}}\r\n')
                    await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(peer, "127.0.0.1", 0)
        device = Device("127.0.0.1")
        device.set_message_listener(reports.put_nowait)
        try:
            with patch("cozylife_local.cozylife_api.COZYLIFE_PORT",
                       server.sockets[0].getsockname()[1]):
                assert await device.async_request_properties()
                first_peer = await asyncio.wait_for(connections.get(), 1)
                await asyncio.wait_for(reports.get(), 1)
                first_writer = device._writer
                first_reader_task = device._reader_task

                async def drop_during_write():
                    first_peer.close()
                    await first_peer.wait_closed()
                    await asyncio.wait_for(first_reader_task, 1)
                    raise ConnectionResetError("peer dropped during write")

                with patch.object(first_writer, "drain", drop_during_write):
                    assert await device.async_request_properties()
                assert await asyncio.wait_for(connections.get(), 1) is not first_peer
                assert (await asyncio.wait_for(reports.get(), 1))["msg"]["data"] == {"1": 1}
                assert device.last_properties_request_failure is None
        finally:
            await device.async_close()
            server.close()
            await server.wait_closed()

    asyncio.run(exercise())
