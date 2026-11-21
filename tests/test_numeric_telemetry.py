"""Numeric telemetry must not report invalid payloads as measurements."""

import ast
import asyncio
from types import SimpleNamespace
from math import isfinite
from typing import Optional
from unittest.mock import AsyncMock

from test_sensor_transport import COMPONENT_DIR


def test_relay_bitmask_rejects_malformed_reports_and_preserves_other_gangs():
    tree = ast.parse((COMPONENT_DIR / "switch.py").read_text())
    relay = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                 and node.name == "CozyLifeSwitch")
    methods = [node for node in relay.body
               if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
               and node.name in {"is_on", "_async_set_gang_state", "_async_write_gang_mask"}]
    for method in methods:
        method.decorator_list = []
    scope = {"BITMASK_DPID": "1"}
    exec(compile(ast.Module(body=methods, type_ignores=[]), "switch.py", "exec"), scope)
    sender = AsyncMock(return_value=True)
    state = {}
    instance = SimpleNamespace(
        coordinator=SimpleNamespace(data=state, device=SimpleNamespace(async_set_state=sender)),
        _gang_bit=0, async_write_ha_state=lambda: None,
    )
    instance.coordinator.relay_write_lock = asyncio.Lock()
    instance._async_write_gang_mask = lambda enabled: scope["_async_write_gang_mask"](instance, enabled)
    assert scope["is_on"](instance) is None
    for raw in (None, "offline", -1, {}, 1.5):
        state["1"] = raw
        assert scope["is_on"](instance) is None
        asyncio.run(scope["_async_set_gang_state"](instance, True))
    sender.assert_not_awaited()
    state["1"] = 2
    assert scope["is_on"](instance) is False
    asyncio.run(scope["_async_set_gang_state"](instance, True))
    sender.assert_awaited_once_with({"1": 3})
    assert scope["is_on"](instance) is True

    async def simultaneous():
        state["1"] = 0
        writes = []

        async def send(payload):
            writes.append(payload["1"])
            await asyncio.sleep(0)
            return True

        instance.coordinator.device.async_set_state = send
        second = SimpleNamespace(coordinator=instance.coordinator, _gang_bit=1,
                                 async_write_ha_state=lambda: None)
        second._async_write_gang_mask = lambda enabled: scope["_async_write_gang_mask"](second, enabled)
        await asyncio.gather(scope["_async_set_gang_state"](instance, True),
                             scope["_async_set_gang_state"](second, True))
        assert writes == [1, 3]
        assert state["1"] == 3

    asyncio.run(simultaneous())


def test_battery_measurements_and_restored_values_stay_in_percentage_range():
    tree = ast.parse((COMPONENT_DIR / "sensor.py").read_text())
    battery = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                   and node.name == "CozyLifeBatterySensor")
    methods = [node for node in battery.body if isinstance(node, ast.FunctionDef)
               and node.name in {"_native_value_from_data", "_is_valid_native_value"}]
    scope = {"Optional": Optional, "SENSOR_BATTERY": "9"}
    exec(compile(ast.Module(body=methods, type_ignores=[]), "sensor.py", "exec"), scope)
    data = {}
    instance = SimpleNamespace(coordinator=SimpleNamespace(data=data))
    for raw in (True, "offline", -1, 1001, float("inf"), float("nan")):
        data["9"] = raw
        assert scope["_native_value_from_data"](instance) is None
    for raw in (0, 559, 1000):
        data["9"] = raw
        assert scope["_native_value_from_data"](instance) == raw / 10
    for restored in (-1, 101, float("inf"), float("nan")):
        assert not scope["_is_valid_native_value"](instance, restored)


def test_speaker_source_only_accepts_the_panel_enum():
    tree = ast.parse((COMPONENT_DIR / "media_player.py").read_text())
    sources = next(node for node in tree.body if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == "_SOURCES"
                           for target in node.targets))
    getter = next(node for entity in tree.body if isinstance(entity, ast.ClassDef)
                  for node in entity.body
                  if isinstance(node, ast.FunctionDef) and node.name == "source")
    getter.decorator_list = []
    scope = {}
    exec(compile(ast.Module(body=[sources, getter], type_ignores=[]),
                 "media_player.py", "exec"), scope)
    state = {}
    speaker = SimpleNamespace(coordinator=SimpleNamespace(data=state))
    for invalid in (None, -1, "-2", 3, True, 1.5, float("inf"), float("nan")):
        state["6"] = invalid
        assert scope["source"](speaker) is None
    for value, expected in ((0, "Bluetooth"), ("1", "Local playback"), (2, "Line-in")):
        state["6"] = value
        assert scope["source"](speaker) == expected


def test_native_value_handles_missing_malformed_and_valid_telemetry():
    tree = ast.parse((COMPONENT_DIR / "sensor.py").read_text())
    entity = next(node for node in tree.body
                  if isinstance(node, ast.ClassDef) and node.name == "CozyLifePlugSensor")
    getter = next(node for node in entity.body
                  if isinstance(node, ast.FunctionDef) and node.name == "native_value")
    getter.decorator_list = []
    scope = {"Optional": Optional, "isfinite": isfinite}
    exec(compile(ast.Module(body=[getter], type_ignores=[]), "sensor.py", "exec"), scope)
    instance = SimpleNamespace(
        coordinator=SimpleNamespace(data={}),
        _description=SimpleNamespace(dpid="26", scale=1000, offset=0),
    )
    native_value = scope["native_value"]
    assert native_value(instance) is None
    for raw in (True, "offline", {}, [], float("inf"), float("nan")):
        instance.coordinator.data["26"] = raw
        assert native_value(instance) is None
    for raw, expected in ((0, 0.0), (1234, 1.234)):
        instance.coordinator.data["26"] = raw
        assert native_value(instance) == expected


def test_countdown_rejects_invalid_reports_and_preserves_zero_seconds():
    tree = ast.parse((COMPONENT_DIR / "number.py").read_text())
    entity = next(node for node in tree.body
                  if isinstance(node, ast.ClassDef) and node.name == "CozyLifeCountdownNumberBase")
    getter = next(node for node in entity.body
                  if isinstance(node, ast.FunctionDef) and node.name == "native_value")
    getter.decorator_list = []
    scope = {"MAX_COUNTDOWN_SECONDS": 86400}
    exec(compile(ast.Module(body=[getter], type_ignores=[]), "number.py", "exec"), scope)
    state = {}
    instance = SimpleNamespace(
        coordinator=SimpleNamespace(data=state), _countdown_dpid="13"
    )
    for raw in (True, False, float("inf"), float("nan"), "offline"):
        state["13"] = raw
        assert scope["native_value"](instance) is None
    for raw, expected in ((0, 0), ("3600", 3600), (3660, 3660)):
        state["13"] = raw
        assert scope["native_value"](instance) == expected
