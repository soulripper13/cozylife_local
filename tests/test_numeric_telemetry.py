"""Numeric telemetry must not report invalid payloads as measurements."""

import ast
from types import SimpleNamespace
from math import isfinite
from typing import Optional

from test_sensor_transport import COMPONENT_DIR


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
