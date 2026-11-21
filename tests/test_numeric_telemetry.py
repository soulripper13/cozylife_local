"""Numeric telemetry must not report invalid payloads as measurements."""

import ast
import asyncio
from types import SimpleNamespace
from math import isfinite
from typing import Optional
from unittest.mock import AsyncMock

from test_sensor_transport import COMPONENT_DIR


def test_binary_configuration_does_not_interpret_unknown_as_enabled():
    tree = ast.parse((COMPONENT_DIR / "switch.py").read_text())
    entity = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                  and node.name == "CozyLifeDpidSwitch")
    getter = next(node for node in entity.body if isinstance(node, ast.FunctionDef)
                  and node.name == "is_on")
    getter.decorator_list = []
    scope = {}
    exec(compile(ast.Module(body=[getter], type_ignores=[]), "switch.py", "exec"), scope)
    data = {}
    switch = SimpleNamespace(coordinator=SimpleNamespace(data=data))
    for dpid in ("2", "9"):
        switch._dpid = dpid
        for raw, expected in ((0, False), ("off", False), (1, True), ("true", True),
                              (2, None), ("offline", None), (None, None)):
            data[dpid] = raw
            assert scope["is_on"](switch) is expected


def test_contact_motion_and_video_reports_use_their_own_enums():
    tree = ast.parse((COMPONENT_DIR / "binary_sensor.py").read_text())
    getter = next(node for entity in tree.body if isinstance(entity, ast.ClassDef)
                  for node in entity.body
                  if isinstance(node, ast.FunctionDef) and node.name == "is_on")
    getter.decorator_list = []
    scope = {}
    exec(compile(ast.Module(body=[getter], type_ignores=[]), "binary_sensor.py", "exec"), scope)
    data = {}
    instance = SimpleNamespace(coordinator=SimpleNamespace(data=data))
    for dpid in ("101", "103", "106"):
        instance._description = SimpleNamespace(dpid=dpid)
        for raw, expected in ((1, True), ("1", True), (2, False), ("2", False),
                              (0, False), (None, None), ("offline", None)):
            data[dpid] = raw
            assert scope["is_on"](instance) is expected

    for dpid in ("6", "7"):
        instance._description = SimpleNamespace(dpid=dpid)
        for raw, expected in ((1, True), ("1", True), (0, False), ("0", False),
                              (2, None), (-1, None), (None, None)):
            data[dpid] = raw
            assert scope["is_on"](instance) is expected


def test_overcurrent_protection_decodes_the_documented_binary_values():
    tree = ast.parse((COMPONENT_DIR / "switch.py").read_text())
    entity = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                  and node.name == "CozyLifePlugBooleanSwitch")
    getter = next(node for node in entity.body
                  if isinstance(node, ast.FunctionDef) and node.name == "is_on")
    getter.decorator_list = []
    scope = {"PLUG_OVERCURRENT_PROTECTION": "32"}
    exec(compile(ast.Module(body=[getter], type_ignores=[]), "switch.py", "exec"), scope)
    state = {}
    instance = SimpleNamespace(coordinator=SimpleNamespace(data=state))
    for value, expected in ((0, False), ("0", False), (False, False),
                            (1, True), ("1", True), (True, True),
                            (None, None), (2, None), ("offline", None)):
        state["32"] = value
        assert scope["is_on"](instance) is expected


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
        _gang_bit=0, _total_entities=2, async_write_ha_state=lambda: None,
    )
    instance.coordinator.relay_write_lock = asyncio.Lock()
    instance._async_write_gang_mask = lambda enabled: scope["_async_write_gang_mask"](instance, enabled)
    assert scope["is_on"](instance) is None
    asyncio.run(scope["_async_set_gang_state"](instance, True))
    sender.assert_not_awaited()
    for raw in (None, "offline", -1, {}, 1.5, True, False):
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
                                 _total_entities=2,
                                 async_write_ha_state=lambda: None)
        second._async_write_gang_mask = lambda enabled: scope["_async_write_gang_mask"](second, enabled)
        await asyncio.gather(scope["_async_set_gang_state"](instance, True),
                             scope["_async_set_gang_state"](second, True))
        assert writes == [1, 3]
        assert state["1"] == 3

    asyncio.run(simultaneous())

    single_sender = AsyncMock(return_value=True)
    instance._total_entities = 1
    instance.coordinator.device.async_set_state = single_sender
    state.clear()
    asyncio.run(scope["_async_set_gang_state"](instance, False))
    single_sender.assert_awaited_once_with({"1": 0})
    state["1"] = True
    assert scope["is_on"](instance) is True


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
    scope = {"Optional": Optional, "isfinite": isfinite,
             "PLUG_ADDED_ENERGY": "26", "PLUG_ENERGY": "42",
             "PLUG_CURRENT": "27", "PLUG_POWER": "28", "PLUG_VOLTAGE": "29"}
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
    for raw in (True, False, float("inf"), float("nan"), "offline", -1, 86401, 0.5):
        state["13"] = raw
        assert scope["native_value"](instance) is None
    for raw, expected in ((0, 0), ("3600", 3600), (3660, 3660), (86400, 86400)):
        state["13"] = raw
        assert scope["native_value"](instance) == expected


def test_select_reports_require_an_exact_enum_value():
    tree = ast.parse((COMPONENT_DIR / "select.py").read_text())
    decoder = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                   and node.name == "_option_from_report")
    scope = {}
    exec(compile(ast.Module(body=[decoder], type_ignores=[]), "select.py", "exec"), scope)
    decode = scope["_option_from_report"]
    for options in ({0: "Off", 1: "On", 2: "Previous state"},
                    {0: "Off", 1: "State", 2: "Locator"},
                    {1: "Happy", 2: "Sad"}):
        for value, label in options.items():
            assert decode(value, options) == label
            assert decode(str(value), options) == label
        for invalid in (True, False, 1.5, float("inf"), float("nan"), 99, "offline", None):
            assert decode(invalid, options) is None


def test_unsigned_plug_measurements_preserve_scale_and_reject_negative_values():
    tree = ast.parse((COMPONENT_DIR / "sensor.py").read_text())
    entity = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                  and node.name == "CozyLifePlugSensor")
    getter = next(node for node in entity.body if isinstance(node, ast.FunctionDef)
                  and node.name == "native_value")
    getter.decorator_list = []
    scope = {"Optional": Optional, "isfinite": isfinite,
             "PLUG_ADDED_ENERGY": "26", "PLUG_ENERGY": "42",
             "PLUG_CURRENT": "27", "PLUG_POWER": "28", "PLUG_VOLTAGE": "29"}
    exec(compile(ast.Module(body=[getter], type_ignores=[]), "sensor.py", "exec"), scope)
    for dpid in ("26", "42"):
        sensor = SimpleNamespace(
            coordinator=SimpleNamespace(data={}),
            _description=SimpleNamespace(dpid=dpid, scale=1000, offset=0),
        )
        for raw, expected in ((904, 0.904), (1, 0.001), (0, 0.0), (-1, None)):
            sensor.coordinator.data[dpid] = raw
            assert scope["native_value"](sensor) == expected

    for dpid, raw in (("27", 1250), ("28", 133.5), ("29", 113)):
        sensor = SimpleNamespace(
            coordinator=SimpleNamespace(data={dpid: raw}),
            _description=SimpleNamespace(dpid=dpid, scale=1, offset=0),
        )
        assert scope["native_value"](sensor) == raw
        sensor.coordinator.data[dpid] = 0
        assert scope["native_value"](sensor) == 0
        sensor.coordinator.data[dpid] = -1
        assert scope["native_value"](sensor) is None

    sensor = SimpleNamespace(
        coordinator=SimpleNamespace(data={}),
        _description=SimpleNamespace(dpid="102", scale=10, offset=0),
    )
    for raw, expected in ((0, 0), (857, 85.7), (1000, 100), (-1, None), (1001, None)):
        sensor.coordinator.data["102"] = raw
        assert scope["native_value"](sensor) == expected


def test_contact_profiles_expose_only_the_verified_battery_measurement():
    tree = ast.parse((COMPONENT_DIR / "sensor.py").read_text())
    setup = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef)
                 and node.name == "async_setup_entry")
    models = next(node for node in tree.body if isinstance(node, ast.Assign)
                  and any(isinstance(target, ast.Name)
                          and target.id == "_CONTACT_BATTERY_PIDS" for target in node.targets))
    scope = {
        "DOMAIN": "cozylife_local", "SENSOR_BATTERY": "9",
        "HomeAssistant": object, "ConfigEntry": object,
        "CozyLifeIPAddressSensor": lambda *_: "IP address",
        "CozyLifeBatterySensor": lambda *_: "Battery",
    }
    exec(compile(ast.Module(body=[models, setup], type_ignores=[]), "sensor.py", "exec"), scope)
    entry = SimpleNamespace(entry_id="entry")
    coordinator = SimpleNamespace(
        classification=SimpleNamespace(
            is_local_transport=True, is_environment_sensor=False,
            is_multi_sensor=False, supports_plug_metering=False,
            is_energy_storage=False, is_video_intercom=False,
        ),
        device=SimpleNamespace(pid="0iDvw9", dpid=["7", "9"]),
    )
    hass = SimpleNamespace(data={"cozylife_local": {"entry": coordinator}})
    for pid, dpids, expected in (
        ("0iDvw9", ["7", "9"], ["IP address", "Battery"]),
        ("o64g7o", ["7", "9"], ["IP address", "Battery"]),
        ("t7x7Uz", ["7"], ["IP address"]),
        ("unverified", ["7", "9"], ["IP address"]),
    ):
        coordinator.device.pid = pid
        coordinator.device.dpid = dpids
        entities = []
        asyncio.run(scope["async_setup_entry"](hass, entry, entities.extend))
        assert entities == expected
