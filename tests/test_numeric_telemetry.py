"""Numeric telemetry must not report invalid payloads as measurements."""

import ast
from types import SimpleNamespace
from math import isfinite
from typing import Optional

from test_sensor_transport import COMPONENT_DIR


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
