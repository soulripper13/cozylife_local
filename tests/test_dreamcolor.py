"""Exercise the actual light entity with APK-shaped state and commands."""

import ast
import asyncio
import colorsys
from pathlib import Path
import re
import types
from typing import Any, Dict, Optional
from unittest.mock import AsyncMock

import pytest


def _light_module():
    """Load entity code with small HA interface doubles, without HA startup."""
    root = Path(__file__).parents[1] / "custom_components" / "cozylife_local"

    class CoordinatorEntity:
        @classmethod
        def __class_getitem__(cls, _item):
            return cls

        def __init__(self, coordinator):
            self.coordinator = coordinator

        def async_write_ha_state(self):
            pass

    class LightEntity:
        pass

    def rgb(h, s):
        return tuple(round(x * 255) for x in colorsys.hsv_to_rgb(h / 360, s / 100, 1))

    def hs(r, g, b):
        h, s, _ = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        return h * 360, s * 100

    namespace = dict(Any=Any, Dict=Dict, Optional=Optional, re=re,
        logging=__import__("logging"), CoordinatorEntity=CoordinatorEntity,
        LightEntity=LightEntity, CozyLifeCoordinator=object, ConfigEntry=object,
        DeviceInfo=dict, callback=lambda f: f,
        ColorMode=types.SimpleNamespace(HS="hs", COLOR_TEMP="color_temp",
                                       BRIGHTNESS="brightness", ONOFF="onoff"),
        color_hs_to_RGB=rgb, color_RGB_to_hs=hs,
        ATTR_HS_COLOR="hs_color", ATTR_BRIGHTNESS="brightness",
        ATTR_COLOR_TEMP_KELVIN="color_temp_kelvin")
    exec((root / "const.py").read_text(), namespace)
    tree = ast.parse((root / "light.py").read_text())
    nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef, ast.Assign))]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(root / "light.py"), "exec"), namespace)
    return types.SimpleNamespace(**namespace)


def _entity(module, state, dpids=("1", "2", "3", "4", "5", "6", "16")):
    device = types.SimpleNamespace(pid="v3sjfa", dpid=list(dpids), device_id="test",
                                   async_set_state=AsyncMock(return_value=True))
    coordinator = types.SimpleNamespace(device=device, data=dict(state))
    return module.CozyLifeLight(coordinator, types.SimpleNamespace(data={}, options={}))


@pytest.mark.parametrize("value", [None, 16, "", "000003ac", "000003acfff",
    "000003acffff00", "000003acgggg", "+00103acffff", " 00003acffff",
    "016903e8ffff", "000003e9ffff", "ffffffffffff", "000003acffff" * 2])
def test_malformed_segment_and_sentinel_values_are_not_whole_colour(value):
    assert _light_module()._parse_color_hex(value) is None


@pytest.mark.parametrize("value, expected", [
    ("000003acffff", (0, 94.0)), ("007803e8ffff", (120, 100)),
    ("016803E8FFFF", (360, 100)), ("00000000ffff", (0, 0))])
def test_reported_colour_overrides_stale_legacy_and_temperature(value, expected):
    module = _light_module()
    light = _entity(module, {"16": value, "3": 500, "5": 240, "6": 1000})
    assert light.hs_color == expected
    assert light.color_mode == module.ColorMode.HS


@pytest.mark.parametrize("hs, encoded", [((0, 94), "000003acffff"),
    ((120, 100), "007803e8ffff"), ((360, 100), "016803e8ffff"),
    ((0, 0), "00000000ffff")])
def test_colour_write_is_one_apk_shaped_command(hs, encoded):
    module = _light_module()
    light = _entity(module, {"16": "007803e8ffff"})
    asyncio.run(light.async_turn_on(hs_color=hs, brightness=128))
    light.coordinator.device.async_set_state.assert_awaited_once_with(
        {"1": 1, "2": 0, "4": 502, "16": encoded})
    assert light.hs_color == hs


@pytest.mark.parametrize("extra", [{}, {"16": "invalid"}, {"16": "ffffffffffff"},
                                  {"16": "000003acffff" * 2}])
def test_ordinary_lights_keep_the_legacy_read_and_write_path(extra):
    module = _light_module()
    light = _entity(module, {"5": 240, "6": 1000, **extra})
    assert light.hs_color == (240, 100)
    asyncio.run(light.async_turn_on(hs_color=(120, 100)))
    light.coordinator.device.async_set_state.assert_awaited_once_with(
        {"1": 1, "2": 0, "5": 120, "6": 1000})


def test_temperature_brightness_onoff_and_failed_writes_are_preserved():
    module = _light_module()
    light = _entity(module, {"3": 500}, ("1", "2", "3", "4"))
    assert light.color_mode == module.ColorMode.COLOR_TEMP
    assert light.hs_color is None
    asyncio.run(light.async_turn_on(color_temp_kelvin=light.min_color_temp_kelvin))
    light.coordinator.device.async_set_state.assert_awaited_once_with({"1": 1, "2": 0, "3": 0})
    brightness = _entity(module, {"4": 1000}, ("1", "4"))
    assert brightness.color_mode == module.ColorMode.BRIGHTNESS
    assert brightness.brightness == 255
    onoff = _entity(module, {}, ("1",))
    assert onoff.supported_color_modes == {module.ColorMode.ONOFF}
    asyncio.run(onoff.async_turn_off())
    onoff.coordinator.device.async_set_state.assert_awaited_once_with({"1": 0})
    dream = _entity(module, {"16": "000003acffff"})
    dream.coordinator.device.async_set_state.return_value = False
    before = dict(dream.coordinator.data)
    asyncio.run(dream.async_turn_on(hs_color=(120, 100)))
    assert dream.coordinator.data == before
