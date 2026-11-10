"""Real TCP peers reproducing silent sessions after network interruption."""

import asyncio
import json
import time
import types
from unittest.mock import patch

import pytest

from test_sensor_transport import _load_api


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
