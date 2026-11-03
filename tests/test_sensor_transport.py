"""Protocol checks for the APK-derived sleeping-sensor transport."""

from __future__ import annotations

import asyncio
import ast
import importlib.util
import json
import socket
import sys
import types
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

COMPONENT_DIR = Path(__file__).parents[1] / "custom_components" / "cozylife_local"


async def _read_apk_tcp_request(reader: asyncio.StreamReader) -> dict:
    """Read one bare-JSON client command like the APK's local TCP server."""
    buffer = getattr(reader, "_cozylife_test_buffer", b"")
    decoder = json.JSONDecoder()
    while True:
        try:
            decoded, index = decoder.raw_decode(buffer.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            chunk = await reader.read(4096)
            if not chunk:
                raise EOFError("socket closed before a complete CozyLife command")
            buffer += chunk
            continue
        reader._cozylife_test_buffer = buffer[index:].lstrip()
        return decoded


def _load_api():
    """Load the API module without importing Home Assistant's package setup."""
    homeassistant = types.ModuleType("homeassistant")
    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = object
    core.callback = lambda func: func
    sys.modules["homeassistant"] = homeassistant
    sys.modules["homeassistant.core"] = core
    package = types.ModuleType("cozylife_local")
    package.__path__ = [str(COMPONENT_DIR)]
    sys.modules["cozylife_local"] = package
    from cozylife_local.cozylife_api import CozyLifeDevice

    return CozyLifeDevice


def test_connection_refusal_is_retained_for_diagnostics() -> None:
    """A local refusal remains inspectable after the failed request returns."""
    CozyLifeDevice = _load_api()

    async def exercise() -> None:
        device = CozyLifeDevice("192.0.2.50", timeout=0.1)

        async def refuse(*_args, **_kwargs):
            raise ConnectionRefusedError(111, "Connection refused")

        with patch("asyncio.open_connection", refuse):
            assert await device.async_send_status() is False

        assert device.last_transport_error == "ConnectionRefusedError"

    asyncio.run(exercise())


def test_crlf_plug_queries_controls_and_identity_use_one_encoder():
    """Reproduce #91 with a peer that acts only on complete CRLF lines."""
    Device = _load_api()

    async def exercise():
        state = {"1": 1, "18": 2, "19": 1}
        requests = []
        completed = asyncio.Event()
        async def handler(reader, writer):
            try:
                while raw := await reader.readline():
                    assert raw.endswith(b"\r\n")
                    frame = json.loads(raw)
                    requests.append(frame)
                    if frame["cmd"] == 3:
                        state.update(frame["msg"]["data"])
                    response = {"cmd": frame["cmd"], "sn": frame["sn"], "res": 0,
                                "msg": {"data": dict(state)}}
                    writer.write((json.dumps(response) + "\r\n").encode())
                    await writer.drain()
                    if len(requests) == 5:
                        completed.set()
            finally:
                writer.close()
                await writer.wait_closed()
        server = await asyncio.start_server(handler, "127.0.0.1", 0)
        device = Device("127.0.0.1", timeout=1)
        device._pid = "h38t1p"
        try:
            with patch("cozylife_local.cozylife_api.COZYLIFE_PORT", server.sockets[0].getsockname()[1]):
                assert await device._send_receive(0, {})
                assert await device.async_set_state({"1": 0})
                assert (await device.async_get_state(["1", "18", "19"]))["1"] == 0
                assert await device.async_set_state({"1": 1})
                assert (await device.async_get_state(["1", "18", "19"])) == state
                await asyncio.wait_for(completed.wait(), 2)
                assert state == {"1": 1, "18": 2, "19": 1}
        finally:
            await device.async_close()
            server.close()
            await server.wait_closed()
            await asyncio.sleep(0)
    asyncio.run(exercise())


def test_framing_preserves_legacy_default_and_tested_sensor():
    Device = _load_api()
    device = Device("192.0.2.1")
    frame = {"pv": 0, "cmd": 2, "sn": "1", "msg": {"attr": [1]}}
    for pid in (None, "h38t1p", "P6CiQT", "0nd7WQ"):
        device._pid = pid
        assert device._encode_message(frame).endswith(b"\r\n")
    device._pid = "Z4tRml"
    assert device._encode_message(frame) == json.dumps(frame, separators=(",", ":")).encode()


def test_median_filter_rejects_spikes_and_adapts_to_sustained_changes():
    _load_api()
    from cozylife_local.measurement_filter import MeasurementFilter
    filter_ = MeasurementFilter()
    radii = {"8": 20, "4": 10}
    assert filter_.filter("1", {"8": 250, "4": 48}, radii) == {"8": 250, "4": 48}
    assert filter_.filter("2", {"8": 0, "4": 49}, radii) == {"4": 49}
    assert filter_.filter("2", {"8": 0}, radii) == {}
    assert len(filter_.windows["8"]) == 4
    assert filter_.filter(None, {"8": 0}, radii) == {}
    for sn in ("3", "4", "5", "6"):
        result = filter_.filter(sn, {"8": 0}, radii)
    assert result == {"8": 0}
    near = MeasurementFilter()
    assert near.filter("1", {"8": -10}, radii) == {"8": -10}
    assert near.filter("2", {"8": 0}, radii) == {"8": 0}


def test_optional_filter_receive_path_defaults_off_and_rejects_replay():
    Coordinator = _load_coordinator()

    async def exercise(enabled):
        coordinator = object.__new__(Coordinator)
        coordinator._uses_sensor_polling = True
        coordinator.classification = types.SimpleNamespace(is_environment_sensor=True)
        coordinator.data = {"8": 250, "4": 48}
        coordinator._entry = types.SimpleNamespace(
            options={"filter_outliers": True} if enabled else {}, data={})
        updates = []
        def publish(data):
            coordinator.data = data
            updates.append(data)
        coordinator.async_set_updated_data = publish
        for sn, values in (("1", {"8": 250}), ("2", {"8": 0}),
                           ("2", {"8": 0}), ("3", {"8": 240})):
            await coordinator._async_handle_device_message(
                {"cmd": 10, "sn": sn, "msg": {"data": values}})
        assert len(updates) == (2 if enabled else 4)
        if not enabled:
            assert updates[1]["8"] == 0
        assert updates[-1]["8"] == 240
        assert updates[-1]["4"] == 48
        if not enabled:
            assert not hasattr(coordinator, "_measurement_filter")
    asyncio.run(exercise(False))
    asyncio.run(exercise(True))


def test_setup_failure_and_cancellation_release_device_and_debug_state() -> None:
    """Exercise the setup boundary with failures before/after coordinator creation."""
    from unittest.mock import AsyncMock, Mock

    tree = ast.parse((COMPONENT_DIR / "__init__.py").read_text())
    setup = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef)
                 and node.name == "async_setup_entry")
    setup.returns = None
    for arg in setup.args.args:
        arg.annotation = None

    async def exercise(error, with_coordinator):
        discovery = types.SimpleNamespace(is_running=True, unregister_device=Mock(),
                                          unregister_address=Mock())
        device = types.SimpleNamespace(device_id="test", async_close=AsyncMock())
        coordinator = types.SimpleNamespace(async_close=AsyncMock())
        hass = types.SimpleNamespace(data={"cozylife_local": {"discovery": discovery}})
        entry = types.SimpleNamespace(options={}, data={"ip_address": "192.0.2.1"},
                                      entry_id="entry", title="test")
        async def fail(*args):
            if with_coordinator:
                hass.data["cozylife_local"]["entry"] = coordinator
            raise error
        remove = Mock()
        namespace = dict(DOMAIN="cozylife_local", DATA_DISCOVERY_SERVICE="discovery",
                         _DEBUG_ENTRY_IDS="debug", DISCOVERY_CONTROLLER_ENTRY_TYPE="controller",
                         _refresh_debug_logging=Mock(), _remove_debug_entry=remove,
                         _LOGGER=Mock(), async_load_model_catalog=AsyncMock(),
                         CozyLifeDevice=Mock(return_value=device), _async_setup_device_entry=fail)
        exec(compile(ast.Module(body=[setup], type_ignores=[]), "setup-test", "exec"), namespace)
        try:
            await namespace["async_setup_entry"](hass, entry)
            assert False, "setup should propagate failure"
        except type(error):
            pass
        assert "entry" not in hass.data["cozylife_local"]
        discovery.unregister_device.assert_called_once_with("test", "entry")
        remove.assert_called_once_with(hass, "entry")
        (coordinator.async_close if with_coordinator else device.async_close).assert_awaited_once()

    for error in (RuntimeError("setup failed"), asyncio.CancelledError()):
        for with_coordinator in (False, True):
            asyncio.run(exercise(error, with_coordinator))


def test_unload_cancels_followup_and_ignores_queued_discovery() -> None:
    """An old coordinator cannot reopen its transport after unloading."""
    Coordinator = _load_coordinator()

    async def exercise():
        coordinator = object.__new__(Coordinator)
        coordinator._sensor_status_task = None
        coordinator._identity_refresh_task = None
        cancelled = asyncio.Event()

        async def followup():
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        coordinator._powered_followup_task = asyncio.create_task(followup())
        await asyncio.sleep(0)
        coordinator.device = types.SimpleNamespace(async_close=lambda: asyncio.sleep(0))
        await coordinator.async_close()
        assert cancelled.is_set()
        assert coordinator._powered_followup_task is None
        # No address updater exists: a queued callback must exit before access.
        await coordinator.async_handle_discovered_address("192.0.2.1")
        await coordinator._async_handle_device_message({"msg": {"data": {"8": 200}}})

    asyncio.run(exercise())


def test_sensor_delivery_timeout_preserves_acked_silent_connection() -> None:
    """Configure kernel delivery recovery without closing a quiet reader."""
    if not hasattr(socket, "TCP_USER_TIMEOUT"):
        return
    CozyLifeDevice = _load_api()

    async def exercise() -> None:
        accepted = []
        release = asyncio.Event()

        async def handle(reader, writer):
            accepted.append(writer)
            await release.wait()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        device = CozyLifeDevice("127.0.0.1")
        device.set_tcp_user_timeout(1000)
        try:
            with patch("cozylife_local.cozylife_api.COZYLIFE_PORT", port):
                assert await device.async_send_status()
                writer = device._writer
                tcp_socket = writer.get_extra_info("socket")
                assert tcp_socket.getsockopt(socket.IPPROTO_TCP, socket.TCP_USER_TIMEOUT) == 1000
                await asyncio.sleep(1.1)
                assert await device.async_send_status()
                assert device._writer is writer
                assert len(accepted) == 1
        finally:
            await device.async_close()
            release.set()
            server.close()
            await server.wait_closed()
            await asyncio.sleep(0)
        assert device._reader_task is None

    asyncio.run(exercise())


def test_cached_known_product_uses_complete_apk_capability_inventory() -> None:
    """A partial legacy cache cannot remove catalogue-backed plug entities."""
    CozyLifeDevice = _load_api()
    device = CozyLifeDevice("192.0.2.50")

    device.restore_from_cache(
        "plug-1",
        "y98c24",
        "00",
        ["1", "2", "26", "28"],
    )

    assert {"27", "29"} <= set(device.dpid or [])


def test_debug_frame_summary_excludes_identity_and_values() -> None:
    """Public debug output keeps protocol shape but no private frame content."""
    _load_api()
    from cozylife_local.debug import summarize_frame

    summary = summarize_frame(
        b'{"cmd":2,"sn":"private-sequence","did":"private-device-id",'
        b'"ip":"192.168.1.23","token":"private-token",'
        b'"msg":{"attr":[0],"data":{"27":240,"29":230}}}'
    )

    assert summary == {
        "cmd": 2,
        "msg": {"attr": [0], "data_dpids": ["27", "29"]},
    }
    assert "private" not in repr(summary)
    assert "192.168.1.23" not in repr(summary)
    assert "240" not in repr(summary)


def _load_coordinator():
    """Load coordinator logic with the minimal Home Assistant interfaces it uses."""
    _load_api()
    config_entries = types.ModuleType("homeassistant.config_entries")
    config_entries.ConfigEntry = object
    config_entries.SOURCE_INTEGRATION_DISCOVERY = "integration_discovery"
    helpers = types.ModuleType("homeassistant.helpers")
    entity_registry = types.ModuleType("homeassistant.helpers.entity_registry")
    update_coordinator = types.ModuleType("homeassistant.helpers.update_coordinator")

    class DataUpdateCoordinator:
        def __init__(self, *_args, update_interval=None, **_kwargs) -> None:
            self.update_interval = update_interval

        @classmethod
        def __class_getitem__(cls, _item):
            return cls
    background_discovery = types.ModuleType("cozylife_local.background_discovery")

    async def async_get_background_discovery(_hass):
        return None

    background_discovery.async_get_background_discovery = async_get_background_discovery
    sys.modules["cozylife_local.background_discovery"] = background_discovery

    update_coordinator.DataUpdateCoordinator = DataUpdateCoordinator
    update_coordinator.UpdateFailed = RuntimeError
    sys.modules["homeassistant.config_entries"] = config_entries
    sys.modules["homeassistant.helpers"] = helpers
    sys.modules["homeassistant.helpers.entity_registry"] = entity_registry
    sys.modules["homeassistant.helpers.update_coordinator"] = update_coordinator
    from cozylife_local.coordinator import CozyLifeCoordinator

    return CozyLifeCoordinator


def _load_background_discovery():
    """Load the shared UDP service with the minimal HA symbols it uses."""
    _load_api()
    homeassistant_const = types.ModuleType("homeassistant.const")
    homeassistant_const.EVENT_HOMEASSISTANT_STOP = "homeassistant_stop"
    sys.modules["homeassistant.const"] = homeassistant_const
    config_entries = types.ModuleType("homeassistant.config_entries")
    config_entries.SOURCE_INTEGRATION_DISCOVERY = "integration_discovery"
    sys.modules["homeassistant.config_entries"] = config_entries
    helpers = types.ModuleType("homeassistant.helpers")
    discovery_flow = types.ModuleType("homeassistant.helpers.discovery_flow")
    discovery_flow.async_create_flow = lambda *_args, **_kwargs: None
    sys.modules["homeassistant.helpers"] = helpers
    sys.modules["homeassistant.helpers.discovery_flow"] = discovery_flow
    sys.modules.pop("cozylife_local.background_discovery", None)
    from cozylife_local import background_discovery

    return background_discovery


def _load_config_flow():
    """Load the config flow with only its startup interfaces stubbed."""
    _load_api()
    config_entries = types.ModuleType("homeassistant.config_entries")

    class ConfigFlow:
        def __init_subclass__(cls, **_kwargs) -> None:
            return None

    config_entries.ConfigEntry = object
    config_entries.ConfigFlow = ConfigFlow
    config_entries.OptionsFlow = object
    config_entries.CONN_CLASS_LOCAL_POLL = "local_poll"
    sys.modules["homeassistant.config_entries"] = config_entries
    data_entry_flow = types.ModuleType("homeassistant.data_entry_flow")
    data_entry_flow.FlowResult = dict
    sys.modules["homeassistant.data_entry_flow"] = data_entry_flow
    vol = types.ModuleType("voluptuous")
    vol.Schema = lambda value: value
    vol.Optional = lambda value, **_kwargs: value
    vol.Required = lambda value, **_kwargs: value
    vol.All = lambda *values: values
    vol.Range = lambda **_kwargs: object()
    vol.In = lambda value: value
    sys.modules["voluptuous"] = vol
    background_discovery = types.ModuleType("cozylife_local.background_discovery")

    async def async_get_background_discovery(_hass):
        return None

    background_discovery.async_get_background_discovery = async_get_background_discovery
    sys.modules["cozylife_local.background_discovery"] = background_discovery
    discovery = types.ModuleType("cozylife_local.discovery")
    discovery.async_load_model_catalog = lambda _hass: None
    discovery.classify_device = lambda *_args: None
    discovery.get_model_info = lambda _pid: None
    discovery.supports_local_entities = lambda *_args: True
    sys.modules["cozylife_local.discovery"] = discovery
    sys.modules.pop("cozylife_local.config_flow", None)
    from cozylife_local import config_flow

    # The config-flow module has captured these references. Remove the
    # temporary modules so later tests import the actual catalog and UDP code.
    sys.modules.pop("cozylife_local.background_discovery", None)
    sys.modules.pop("cozylife_local.discovery", None)
    return config_flow


def test_config_flow_creates_a_no_fields_discovery_controller() -> None:
    """Adding the integration creates its persistent discovery controller."""
    config_flow = _load_config_flow()

    async def exercise() -> None:
        flow = object.__new__(config_flow.CozyLifeConfigFlow)
        flow.hass = object()
        flow.async_show_form = lambda **kwargs: kwargs
        unique_ids: list[str] = []
        entries: list[dict] = []

        async def set_unique_id(unique_id: str) -> None:
            unique_ids.append(unique_id)

        flow.async_set_unique_id = set_unique_id
        flow._abort_if_unique_id_configured = lambda: None
        flow.async_create_entry = lambda **kwargs: entries.append(kwargs) or kwargs

        form = await flow.async_step_user()
        result = await flow.async_step_user({})

        assert form["step_id"] == "user"
        assert form["data_schema"] == {}
        assert unique_ids == ["cozylife_local_discovery_controller"]
        assert entries == [
            {
                "title": "CozyLife Local",
                "data": {"entry_type": "discovery_controller"},
            }
        ]
        assert result == entries[0]

    asyncio.run(exercise())


def test_config_flow_import_creates_a_discovery_controller() -> None:
    """A legacy device-only setup receives one controller on upgrade."""
    config_flow = _load_config_flow()

    async def exercise() -> None:
        flow = object.__new__(config_flow.CozyLifeConfigFlow)
        unique_ids: list[str] = []
        entries: list[dict] = []

        async def set_unique_id(unique_id: str) -> None:
            unique_ids.append(unique_id)

        flow.async_set_unique_id = set_unique_id
        flow._abort_if_unique_id_configured = lambda: None
        flow.async_create_entry = lambda **kwargs: entries.append(kwargs) or kwargs

        result = await flow.async_step_import({"entry_type": "discovery_controller"})

        assert unique_ids == ["cozylife_local_discovery_controller"]
        assert entries == [
            {
                "title": "CozyLife Local",
                "data": {"entry_type": "discovery_controller"},
            }
        ]
        assert result == entries[0]

    asyncio.run(exercise())


def test_udp_discovery_shows_normal_confirmation_before_adding() -> None:
    """A UDP candidate follows Home Assistant's ordinary Add-device flow."""
    config_flow = _load_config_flow()

    async def exercise() -> None:
        flow = object.__new__(config_flow.CozyLifeConfigFlow)
        unique_ids: list[str] = []
        forms: list[dict] = []

        async def set_unique_id(unique_id: str) -> None:
            unique_ids.append(unique_id)

        flow.async_set_unique_id = set_unique_id
        flow._abort_if_unique_id_configured = lambda: None
        flow.hass = object()
        flow.async_show_form = lambda **kwargs: forms.append(kwargs) or kwargs

        async def load_catalog(_hass) -> None:
            return None

        with (
            patch.object(config_flow, "async_load_model_catalog", load_catalog),
            patch.object(config_flow, "get_model_info", lambda _pid: None),
        ):
            result = await flow.async_step_integration_discovery(
                {"device_id": "plug-1", "ip_address": "10.0.0.44"}
            )

        assert unique_ids == ["plug-1"]
        assert flow._udp_discovery_info == {
            "device_id": "plug-1", "ip_address": "10.0.0.44"
        }
        assert result["step_id"] == "confirm_discovery"
        assert forms == [result]

    asyncio.run(exercise())


def test_confirming_udp_catalogued_plug_needs_no_tcp_inventory() -> None:
    """A catalogued plug is added from its announced local identity."""
    config_flow = _load_config_flow()

    async def exercise() -> None:
        flow = object.__new__(config_flow.CozyLifeConfigFlow)
        flow.hass = object()
        entries: list[dict] = []
        cards: list[dict] = []
        unique_ids: list[str] = []

        async def set_unique_id(unique_id: str) -> None:
            unique_ids.append(unique_id)

        async def load_catalog(_hass) -> None:
            return None

        flow.async_set_unique_id = set_unique_id
        flow._abort_if_unique_id_configured = lambda: None
        flow.async_create_entry = lambda **kwargs: entries.append(kwargs) or kwargs
        flow.async_show_form = lambda **kwargs: cards.append(kwargs) or kwargs
        model = types.SimpleNamespace(
            model_name="Smart Plug",
            type_code="01",
            dpids=frozenset({"1", "11", "18", "19", "20"}),
        )
        classification = types.SimpleNamespace(
            is_environment_sensor=False,
            is_local_transport=True,
            is_light=False,
            supports_switch_entities=True,
        )

        with (
            patch.object(config_flow, "async_load_model_catalog", load_catalog),
            patch.object(config_flow, "get_model_info", lambda _pid: model),
            patch.object(
                config_flow,
                "classify_device",
                lambda *_args: classification,
            ),
        ):
            discovery_card = await flow.async_step_integration_discovery(
                {
                    "device_id": "plug-1", "ip_address": "10.0.0.44",
                    "pid": "plug-pid", "device_type_code": "01",
                }
            )
            result = await flow.async_step_confirm_discovery({})

        assert unique_ids == ["plug-1"]
        assert discovery_card["step_id"] == "confirm_discovery"
        assert cards == [discovery_card]
        assert entries == [
            {
                "title": "Smart Plug",
                "data": {
                    "ip_address": "10.0.0.44",
                    "device_id": "plug-1",
                    "pid": "plug-pid",
                    "device_type_code": "01",
                    "dpids": ["1", "11", "18", "19", "20"],
                    "min_kelvin": 2000,
                    "max_kelvin": 6500,
                    "announced_via_udp": True,
                },
            }
        ]
        assert result == entries[0]

    asyncio.run(exercise())


def test_status_write_uses_apk_frame_and_dispatches_unsolicited_report() -> None:
    """The local writer sends attr:[1] and the reader keeps every report."""
    CozyLifeDevice = _load_api()

    async def exercise() -> None:
        reports: list[dict] = []

        async def handler(reader, writer) -> None:
            request = await _read_apk_tcp_request(reader)
            assert request["cmd"] == 2
            assert request["pv"] == 0
            assert request["msg"] == {"attr": [1]}
            writer.write(
                b'{"cmd":2,"sn":"device-event","msg":{"data":{"4":48,"8":291}}}\r\n'
            )
            await writer.drain()
            writer.close()
            await writer.wait_closed()

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


def test_local_tcp_writes_use_the_apk_bare_json_framing() -> None:
    """Local requests are JSON bytes, unlike CRLF-delimited UDP and replies."""
    CozyLifeDevice = _load_api()

    async def exercise() -> None:
        async def handler(reader, writer) -> None:
            raw_request = await asyncio.wait_for(reader.read(4096), timeout=0.5)
            assert raw_request
            assert not raw_request.endswith(b"\r\n")
            request = json.loads(raw_request.decode())
            assert request["cmd"] == 2
            assert request["msg"] == {"attr": [1]}
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(handler, "127.0.0.1", 5555)
        device = CozyLifeDevice("127.0.0.1", timeout=1.0)
        try:
            assert await device.async_send_status()
        finally:
            await device.async_close()
            server.close()
            await server.wait_closed()

    asyncio.run(exercise())


def test_status_write_dispatches_a_delayed_report_without_waiting() -> None:
    """A delayed local frame remains valid after the write itself succeeds."""
    CozyLifeDevice = _load_api()

    async def exercise() -> None:
        reports: list[dict] = []

        async def handler(reader, writer) -> None:
            await _read_apk_tcp_request(reader)
            await asyncio.sleep(0.25)
            writer.write(b'{"cmd":2,"sn":"delayed","msg":{"data":{"1":true}}}\r\n')
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(handler, "127.0.0.1", 5555)
        device = CozyLifeDevice("127.0.0.1", timeout=0.2)
        device.set_message_listener(reports.append)
        try:
            assert await device.async_send_status()
            await asyncio.sleep(0.3)
            assert reports == [
                {"cmd": 2, "sn": "delayed", "msg": {"data": {"1": True}}}
            ]
        finally:
            await device.async_close()
            server.close()
            await server.wait_closed()

    asyncio.run(exercise())


def test_listener_error_does_not_stop_the_persistent_reader() -> None:
    """One bad entity callback must not discard a healthy device socket."""
    CozyLifeDevice = _load_api()

    async def exercise() -> None:
        reports: list[dict] = []
        callback_count = 0

        def listener(frame: dict) -> None:
            nonlocal callback_count
            callback_count += 1
            if callback_count == 1:
                raise ValueError("deliberate callback failure")
            reports.append(frame)

        async def handler(reader, writer) -> None:
            await _read_apk_tcp_request(reader)
            writer.write(b'{"cmd":2,"sn":"first","msg":{"data":{"4":48}}}\r\n')
            await writer.drain()
            await asyncio.sleep(0.01)
            writer.write(b'{"cmd":2,"sn":"second","msg":{"data":{"4":49}}}\r\n')
            await writer.drain()
            await asyncio.sleep(0.05)
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(handler, "127.0.0.1", 5555)
        device = CozyLifeDevice("127.0.0.1", timeout=1.0)
        device.set_message_listener(listener)
        try:
            assert await device.async_send_status()
            await asyncio.sleep(0.1)
            assert callback_count == 2
            assert reports == [
                {"cmd": 2, "sn": "second", "msg": {"data": {"4": 49}}}
            ]
        finally:
            await device.async_close()
            server.close()
            await server.wait_closed()

    asyncio.run(exercise())


def test_status_socket_is_retained_across_the_discovery_interval() -> None:
    """A retained socket does not reconnect merely because ten seconds pass."""
    CozyLifeDevice = _load_api()

    async def exercise() -> None:
        connections = 0

        async def handler(reader, writer) -> None:
            nonlocal connections
            connections += 1
            while True:
                try:
                    await _read_apk_tcp_request(reader)
                except EOFError:
                    break
                writer.write(b'{"cmd":2,"sn":"report","msg":{"data":{"4":50}}}\r\n')
                await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(handler, "127.0.0.1", 5555)
        device = CozyLifeDevice("127.0.0.1", timeout=1.0)
        try:
            assert await device.async_send_status()
            await asyncio.sleep(0.05)
            assert await device.async_send_status()
            await asyncio.sleep(0.05)
            assert connections == 1
        finally:
            await device.async_close()
            server.close()
            await server.wait_closed()

    asyncio.run(exercise())


def test_status_request_keeps_the_reader_owned_socket() -> None:
    """A prior write does not force-close the APK-style local socket."""
    CozyLifeDevice = _load_api()

    async def exercise() -> None:
        device = CozyLifeDevice("127.0.0.1", timeout=1.0)
        disconnected = 0

        def disconnect() -> None:
            nonlocal disconnected
            disconnected += 1

        async def request_state(*_args, **_kwargs):
            return {}

        device._disconnect = disconnect
        device._async_request_state = request_state

        assert await device.async_send_status()
        assert disconnected == 0

    asyncio.run(exercise())


def test_status_request_keeps_the_apk_status_attribute_after_rejection() -> None:
    """An error response does not invent a non-APK property-query fallback."""
    CozyLifeDevice = _load_api()

    async def exercise() -> None:
        connections = 0
        requests: list[dict] = []
        first_received = asyncio.Event()
        second_received = asyncio.Event()

        async def handler(reader, writer) -> None:
            nonlocal connections
            connections += 1
            requests.append(await _read_apk_tcp_request(reader))
            writer.write(b'{"cmd":2,"res":2,"msg":{}}\r\n')
            await writer.drain()
            first_received.set()
            requests.append(await _read_apk_tcp_request(reader))
            second_received.set()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(handler, "127.0.0.1", 5555)
        device = CozyLifeDevice("127.0.0.1", timeout=1.0)
        try:
            assert await device.async_send_status()
            await asyncio.wait_for(first_received.wait(), timeout=2)
            assert await device.async_send_status()
            await asyncio.wait_for(second_received.wait(), timeout=2)
            assert connections == 1
            assert [request["msg"]["attr"] for request in requests] == [[1], [1]]
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
            await _read_apk_tcp_request(reader)
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


def test_sensor_status_write_keeps_the_reader_for_an_async_report() -> None:
    """The APK status action leaves its reader open for a later state frame."""
    CozyLifeDevice = _load_api()

    async def exercise() -> None:
        reports: list[dict] = []

        async def handler(reader, writer) -> None:
            request = await _read_apk_tcp_request(reader)
            assert request["msg"]["attr"] == [1]
            await asyncio.sleep(0.03)
            writer.write(b'{"cmd":10,"res":0,"msg":{"data":{"8":254,"4":44}}}\r\n')
            await writer.drain()
            await reader.read()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(handler, "127.0.0.1", 5555)
        device = CozyLifeDevice("127.0.0.1", timeout=1.0)
        device.set_message_listener(lambda frame: reports.append(frame))
        try:
            assert await device.async_send_status()
            await asyncio.sleep(0.05)
            assert reports and reports[0]["cmd"] == 10
            assert device.is_connected
        finally:
            await device.async_close()
            server.close()
            await server.wait_closed()

    asyncio.run(exercise())


def test_sensor_transport_keeps_discovery_and_status_requests_separate() -> None:
    """The sensor retry path is separate from the UDP discovery service."""
    _load_coordinator()
    from cozylife_local.coordinator import CozyLifeCoordinator

    assert hasattr(CozyLifeCoordinator, "_async_request_sensor_status")


def test_partial_powered_report_reads_only_missing_advertised_dpids() -> None:
    """A partial cached inventory cannot suppress supported plug option reads."""
    CozyLifeCoordinator = _load_coordinator()
    import cozylife_local.coordinator as coordinator_module

    async def exercise() -> None:
        requested: list[list[str]] = []

        async def async_get_state(dpids: list[str]) -> None:
            requested.append(dpids)

        coordinator = object.__new__(CozyLifeCoordinator)
        coordinator._uses_sensor_polling = False
        coordinator.classification = types.SimpleNamespace(
            supports_switch_entities=True,
            supports_plug_metering=True,
            is_light=False,
        )
        coordinator.device = types.SimpleNamespace(
            pid="known-metering-plug",
            dpid=["1", "2", "26", "28", "42", "72"],
            async_get_state=async_get_state,
        )
        coordinator._powered_followup_task = None
        coordinator.hass = types.SimpleNamespace(
            async_create_background_task=lambda coroutine, _name: asyncio.create_task(
                coroutine
            )
        )

        with patch.object(
            coordinator_module,
            "get_model_info",
            lambda _pid: types.SimpleNamespace(
                dpids=frozenset(
                    {"1", "2", "18", "19", "26", "27", "28", "29", "32", "42", "72"}
                )
            ),
        ):
            coordinator._async_request_missing_powered_state(
                {"1": True, "2": 0, "26": 1, "28": 3, "42": 2, "72": 0}
            )
        await asyncio.sleep(0)

        assert requested == [["18", "19", "27", "29", "32"]]
        assert coordinator._powered_followup_task is None

    asyncio.run(exercise())


def test_partial_light_report_reads_catalogued_countdown() -> None:
    """A light's retained countdown entity receives its missing state."""
    CozyLifeCoordinator = _load_coordinator()

    async def exercise() -> None:
        requested: list[list[str]] = []

        async def async_get_state(dpids: list[str]) -> None:
            requested.append(dpids)

        coordinator = object.__new__(CozyLifeCoordinator)
        coordinator._uses_sensor_polling = False
        coordinator.classification = types.SimpleNamespace(
            supports_switch_entities=False,
            supports_plug_metering=False,
            is_light=True,
        )
        coordinator.device = types.SimpleNamespace(
            pid="light-1",
            dpid=["1", "4", "13"],
            async_get_state=async_get_state,
        )
        coordinator._powered_followup_task = None
        coordinator.hass = types.SimpleNamespace(
            async_create_background_task=lambda coroutine, _name: asyncio.create_task(
                coroutine
            )
        )

        coordinator._async_request_missing_powered_state({"1": True, "4": 300})
        await asyncio.sleep(0)

        assert requested == [["13"]]
        assert coordinator._powered_followup_task is None

    asyncio.run(exercise())


def test_shared_udp_discovery_uses_the_apk_daemon_cadence() -> None:
    """Shared UDP discovery follows the APK daemon, not sensor wake probing."""
    background_discovery = _load_background_discovery()
    assert background_discovery.DISCOVERY_BROADCAST_INTERVAL_SECONDS == 10.0

    class FakeTransport:
        def __init__(self) -> None:
            self.sent: list[tuple[bytes, tuple[str, int]]] = []

        def sendto(self, data: bytes, address: tuple[str, int]) -> None:
            self.sent.append((data, address))

    transport = FakeTransport()
    service = object.__new__(background_discovery.CozyLifeBackgroundDiscovery)
    service._transport = transport
    service._send_broadcasts(
        background_discovery.APK_NETWORK_CHANGE_BROADCAST_COUNT
    )
    assert len(transport.sent) == 1
    for frame, address in transport.sent:
        assert address == ("255.255.255.255", 6095)
        assert frame.endswith(b"\r\n")
        request = json.loads(frame.decode())
        assert request["cmd"] == 0
        assert request["pv"] == 0
        assert isinstance(request["sn"], str)
        assert request["msg"] == {}


def test_shared_apk_udp_cache_keeps_valid_device_identity_and_address() -> None:
    """The shared listener accepts the APK's ``did``/``ip`` reply shape."""
    background_discovery = _load_background_discovery()
    found = []
    protocol = background_discovery._ApkUdpDiscoveryProtocol(found.append)
    protocol.datagram_received(
        b'{"msg":{"did":"plug-1","ip":"10.0.0.41","pid":"hjk1jk","dtp":"05"}}\r\n',
        ("10.0.0.41", 6095),
    )
    protocol.datagram_received(
        b'{"msg":{"did":"plug-1","ip":"10.0.0.42"}}\r\n',
        # The APK caches msg.ip, not the UDP sender address.
        ("10.0.0.99", 6095),
    )
    protocol.datagram_received(
        b'{"msg":{"did":"bad","ip":"not-an-address"}}\r\n',
        ("10.0.0.99", 6095),
    )

    # The callback receives every valid reply; the service itself indexes the
    # cache by DID and replaces stale addresses.
    assert [(item.device_id, item.ip_address) for item in found] == [
        ("plug-1", "10.0.0.41"),
        ("plug-1", "10.0.0.42"),
    ]


def test_shared_udp_listener_routes_a_known_did_without_a_second_socket() -> None:
    """Known entries receive the APK-advertised address from the one cache."""
    background_discovery = _load_background_discovery()

    async def exercise() -> None:
        received: list[str] = []

        async def handle_address(ip_address: str) -> None:
            received.append(ip_address)

        service = object.__new__(background_discovery.CozyLifeBackgroundDiscovery)
        service._devices = {}
        service._entry_callbacks = {"plug-1": ("entry-1", handle_address)}
        service._address_callbacks = {}
        service._pending_discovery_ids = set()
        service.hass = types.SimpleNamespace(async_create_task=asyncio.create_task)

        service._handle_device(
            background_discovery.UdpDiscoveredDevice(
                device_id="plug-1",
                ip_address="10.0.0.42",
                pid="hjk1jk",
                device_type_code="05",
            )
        )
        await asyncio.sleep(0)

        assert received == ["10.0.0.42"]
        assert service._devices["plug-1"][0].ip_address == "10.0.0.42"

    asyncio.run(exercise())


def test_registering_a_known_device_routes_the_cached_address() -> None:
    """A just-configured device can use the UDP presence already received."""
    background_discovery = _load_background_discovery()

    async def exercise() -> None:
        received: list[str] = []

        async def handle_address(ip_address: str) -> None:
            received.append(ip_address)

        service = object.__new__(background_discovery.CozyLifeBackgroundDiscovery)
        service.hass = types.SimpleNamespace(async_create_task=asyncio.create_task)
        service._devices = {
            "plug-1": (
                background_discovery.UdpDiscoveredDevice(
                    device_id="plug-1",
                    ip_address="10.0.0.42",
                    pid="hjk1jk",
                    device_type_code="05",
                ),
                0.0,
            )
        }
        service._entry_callbacks = {}
        service._pending_discovery_ids = set()

        service.register_device("plug-1", "entry-1", handle_address)
        await asyncio.sleep(0)

        assert received == ["10.0.0.42"]

    asyncio.run(exercise())


def test_powered_udp_presence_matches_the_apk_fifteen_second_cache() -> None:
    """A powered-device read is gated by the APK cache lifetime."""
    background_discovery = _load_background_discovery()
    service = object.__new__(background_discovery.CozyLifeBackgroundDiscovery)
    service._devices = {
        "plug-1": (
            background_discovery.UdpDiscoveredDevice(
                device_id="plug-1",
                ip_address="10.0.0.42",
                pid="hjk1jk",
                device_type_code="05",
            ),
            100.0,
        )
    }

    with patch.object(background_discovery.time, "monotonic", return_value=115.0):
        assert service.is_device_present("plug-1", "10.0.0.42")
    with patch.object(background_discovery.time, "monotonic", return_value=115.01):
        assert not service.is_device_present("plug-1", "10.0.0.42")
    assert service._devices == {}


def test_shared_udp_listener_creation_is_serialized() -> None:
    """Concurrent entry setup must not create two UDP receive sockets."""
    background_discovery = _load_background_discovery()

    async def exercise() -> None:
        stop_callbacks: list[object] = []
        hass = types.SimpleNamespace(
            data={},
            bus=types.SimpleNamespace(
                async_listen_once=lambda _event, callback: stop_callbacks.append(callback)
            ),
        )
        starts: list[object] = []

        async def start_once(service) -> None:
            starts.append(service)
            await asyncio.sleep(0)
            service._transport = object()

        with patch.object(
            background_discovery.CozyLifeBackgroundDiscovery,
            "async_start",
            start_once,
        ):
            first, second = await asyncio.gather(
                background_discovery.async_get_background_discovery(hass),
                background_discovery.async_get_background_discovery(hass),
            )

        assert first is second
        assert starts == [first]
        assert len(stop_callbacks) == 1

    asyncio.run(exercise())


def test_shared_udp_listener_stop_hook_closes_config_flow_listener() -> None:
    """A listener started before the first entry still shuts down cleanly."""
    background_discovery = _load_background_discovery()

    async def exercise() -> None:
        stop_callbacks: list[object] = []
        scheduled: list[object] = []
        hass = types.SimpleNamespace(
            data={},
            bus=types.SimpleNamespace(
                async_listen_once=lambda _event, callback: stop_callbacks.append(callback)
            ),
            async_create_task=scheduled.append,
        )

        await background_discovery.async_get_background_discovery(hass, start=False)
        assert len(stop_callbacks) == 1

        stop_callbacks[0](None)
        assert len(scheduled) == 1
        await scheduled[0]

    asyncio.run(exercise())


def test_shared_udp_listener_retries_after_socket_start_failure() -> None:
    """A transient UDP bind failure must not poison later entry setup."""
    background_discovery = _load_background_discovery()

    async def exercise() -> None:
        hass = types.SimpleNamespace(
            data={},
            bus=types.SimpleNamespace(async_listen_once=lambda *_args: None),
        )
        attempts = 0

        async def start_with_one_failure(service) -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise OSError("temporary socket failure")
            service._transport = object()

        with patch.object(
            background_discovery.CozyLifeBackgroundDiscovery,
            "async_start",
            start_with_one_failure,
        ):
            try:
                await background_discovery.async_get_background_discovery(hass)
            except OSError:
                pass
            else:
                raise AssertionError("first socket attempt should fail")

            service = await background_discovery.async_get_background_discovery(hass)

        assert attempts == 2
        assert service.is_running

    asyncio.run(exercise())


def test_shared_udp_listener_uses_home_assistant_discovery_flow_helper() -> None:
    """An unconfigured announcement becomes one native discovery flow."""
    background_discovery = _load_background_discovery()
    calls: list[tuple[object, str, dict, dict]] = []
    hass = types.SimpleNamespace(
        config_entries=types.SimpleNamespace(async_entries=lambda _domain: [])
    )
    service = object.__new__(background_discovery.CozyLifeBackgroundDiscovery)
    service.hass = hass
    service._devices = {}
    service._entry_callbacks = {}
    service._address_callbacks = {}
    service._pending_discovery_ids = set()

    def create_flow(flow_hass, domain, *, context, data) -> None:
        calls.append((flow_hass, domain, context, data))

    with patch.object(
        background_discovery.discovery_flow,
        "async_create_flow",
        create_flow,
    ):
        service._handle_device(
            background_discovery.UdpDiscoveredDevice(
                device_id="new-device",
                ip_address="10.0.0.44",
                pid="hjk1jk",
                device_type_code="05",
            )
        )

    assert calls == [
        (
            hass,
            "cozylife_local",
            {"source": "integration_discovery"},
            {
                "device_id": "new-device",
                "ip_address": "10.0.0.44",
                "pid": "hjk1jk",
                "device_type_code": "05",
            },
        )
    ]


def test_udp_discovery_shutdown_cancels_its_task_and_closes_the_socket() -> None:
    """Stopping Home Assistant leaves no discovery socket or task behind."""
    background_discovery = _load_background_discovery()

    async def exercise() -> None:
        class FakeTransport:
            closed = False

            def close(self) -> None:
                self.closed = True

        async def wait_forever() -> None:
            await asyncio.Event().wait()

        transport = FakeTransport()
        service = object.__new__(background_discovery.CozyLifeBackgroundDiscovery)
        service._transport = transport
        service._broadcast_task = asyncio.create_task(wait_forever())

        await service.async_stop()

        assert transport.closed
        assert service._transport is None
        assert service._broadcast_task is None
        assert not service.is_running

    asyncio.run(exercise())


def test_udp_announcement_routes_to_provisional_sensor_address() -> None:
    """A sleeping sensor's reply wakes its entry without making another card."""
    background_discovery = _load_background_discovery()
    async def exercise() -> None:
        received: list[str] = []
        flow_calls: list[object] = []

        async def handle_address(ip_address: str) -> None:
            received.append(ip_address)

        service = object.__new__(background_discovery.CozyLifeBackgroundDiscovery)
        service.hass = types.SimpleNamespace(
            async_create_task=asyncio.create_task,
            config_entries=types.SimpleNamespace(async_entries=lambda _domain: []),
        )
        service._devices = {}
        service._entry_callbacks = {}
        service._address_callbacks = {"10.0.0.115": handle_address}

        with patch.object(
            background_discovery.discovery_flow,
            "async_create_flow",
            lambda *_args, **_kwargs: flow_calls.append(True),
        ):
            service._handle_device(
                background_discovery.UdpDiscoveredDevice(
                    device_id="verified-sensor-did",
                    ip_address="10.0.0.115",
                    pid="Z4tRml",
                    device_type_code="03",
                )
            )

        await asyncio.sleep(0)
        assert received == ["10.0.0.115"]
        assert flow_calls == []
        assert service._devices["verified-sensor-did"][0].ip_address == "10.0.0.115"

    asyncio.run(exercise())


def test_persistent_transport_preserves_existing_info_state_and_set_commands() -> None:
    """The reader-based transport still supports ordinary light/switch calls."""
    CozyLifeDevice = _load_api()

    async def exercise() -> None:
        requests: list[dict] = []

        async def handler(reader, writer) -> None:
            while True:
                try:
                    request = await _read_apk_tcp_request(reader)
                except EOFError:
                    break
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
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(handler, "127.0.0.1", 5555)
        device = CozyLifeDevice("127.0.0.1", timeout=1.0)
        try:
            assert await device.async_update_device_info()
            assert device.device_id == "device-1"
            assert device.dpid == ["1", "11", "18", "19", "20"]
            assert await device.async_set_state({"1": False})
            await asyncio.sleep(0.05)
            assert len(requests) == 3
            assert requests[-1]["cmd"] == 3
            assert requests[-1]["msg"] == {"attr": [1], "data": {"1": False}}
        finally:
            await device.async_close()
            server.close()
            await server.wait_closed()

    asyncio.run(exercise())


def test_property_write_succeeds_without_a_correlated_reply() -> None:
    """The APK regards a completed local CMD_SET write as success."""
    CozyLifeDevice = _load_api()

    async def exercise() -> None:
        async def handler(reader, writer) -> None:
            request = await _read_apk_tcp_request(reader)
            assert request["cmd"] == 3
            assert request["msg"] == {"attr": [1], "data": {"1": False}}
            # Deliberately no reply: the APK writer does not wait for one.
            await asyncio.sleep(0.05)
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(handler, "127.0.0.1", 5555)
        device = CozyLifeDevice("127.0.0.1", timeout=0.1)
        try:
            assert await device.async_set_state({"1": False})
        finally:
            await device.async_close()
            server.close()
            await server.wait_closed()

    asyncio.run(exercise())


def test_property_write_times_out_when_a_socket_stalls_during_flush() -> None:
    """A stalled local writer cannot leave an interactive command hung."""
    CozyLifeDevice = _load_api()

    class StalledWriter:
        closed = False

        def is_closing(self) -> bool:
            return self.closed

        def write(self, _data: bytes) -> None:
            return None

        async def drain(self) -> None:
            await asyncio.Event().wait()

        def close(self) -> None:
            self.closed = True

    async def exercise() -> None:
        device = CozyLifeDevice("192.0.2.50", timeout=0.01)
        writer = StalledWriter()
        device._writer = writer
        assert await device.async_set_state({"1": False}) is False
        assert writer.closed

    asyncio.run(exercise())


def test_switch_write_does_not_wait_for_an_inflight_availability_read() -> None:
    """A background health read must not add latency to an ON/OFF command."""
    CozyLifeDevice = _load_api()

    async def exercise() -> None:
        device = CozyLifeDevice("192.0.2.50", timeout=1.0)
        read_started = asyncio.Event()
        release_read = asyncio.Event()
        writes: list[dict[str, bool]] = []

        async def request_state(_attributes, *, wait_for_state):
            assert wait_for_state is True
            read_started.set()
            await release_read.wait()
            return {"1": True}

        async def send_state(_command, attributes):
            writes.append(attributes)
            return True

        device._async_request_state = request_state
        device._async_send = send_state

        read_task = asyncio.create_task(device.async_get_state(["0"]))
        await read_started.wait()
        assert await device.async_set_state({"1": False}) is True
        assert writes == [{"1": False}]

        release_read.set()
        assert await read_task == {"1": True}

    asyncio.run(exercise())


def test_state_query_accepts_apk_local_reply_with_a_different_sn() -> None:
    """APK status reads use the next state frame, not request-SN matching."""
    CozyLifeDevice = _load_api()

    async def exercise() -> None:
        async def handler(reader, writer) -> None:
            request = await _read_apk_tcp_request(reader)
            assert request["cmd"] == 2
            writer.write(
                (json.dumps({
                    "cmd": 2,
                    "sn": "device-state-event",
                    "msg": {"data": {"1": True, "11": 42}},
                }) + "\r\n").encode()
            )
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(handler, "127.0.0.1", 5555)
        device = CozyLifeDevice("127.0.0.1", timeout=1.0)
        try:
            assert await device.async_get_status() == {"1": True, "11": 42}
        finally:
            await device.async_close()
            server.close()
            await server.wait_closed()

    asyncio.run(exercise())


def test_powered_property_query_uses_apk_listener_state_frame() -> None:
    """Powered products deliver attr:[0] state through the listener."""
    CozyLifeDevice = _load_api()

    async def exercise() -> None:
        async def handler(reader, writer) -> None:
            request = await _read_apk_tcp_request(reader)
            assert request["cmd"] == 2
            assert request["msg"] == {"attr": [0]}
            await asyncio.sleep(0.05)
            writer.write(
                (json.dumps({
                    "cmd": 2,
                    "sn": "unrelated-state-event",
                    "msg": {"data": {"28": 12}},
                }) + "\r\n").encode()
            )
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(handler, "127.0.0.1", 5555)
        device = CozyLifeDevice("127.0.0.1", timeout=0.01)
        received: list[dict] = []
        device.set_message_listener(received.append)
        try:
            assert await device.async_request_properties() is True
            await asyncio.sleep(0.1)
            assert received == [
                {"cmd": 2, "sn": "unrelated-state-event", "msg": {"data": {"28": 12}}}
            ]
        finally:
            await device.async_close()
            server.close()
            await server.wait_closed()

    asyncio.run(exercise())


def test_catalog_classification_requires_explicit_local_capabilities() -> None:
    """Do not expose catalog-only lights or non-Wi-Fi sensors as local devices."""
    _load_api()
    from cozylife_local.discovery import classify_device

    known_light = classify_device("asdlks", "01", ["1", "2", "3", "4"])
    catalog_only_light = classify_device("kpfzBm", "01", [])
    bluetooth_light = classify_device(
        "y7274m", "01", ["1", "2", "4", "5", "6", "7", "8", "9"]
    )
    wifi_environment_sensor = classify_device("Z4tRml", "03", ["4", "8", "9"])
    another_wifi_environment_sensor = classify_device("s1AxFq", "03", ["4", "8", "9"])
    zigbee_environment_sensor = classify_device("v2kv9f", "03", ["8", "9"])
    bluetooth_environment_sensor = classify_device("bQ2cGF", "03", ["4", "8", "9"])
    appliance = classify_device("CJjAvl", "05", ["1"])
    tower_fan = classify_device("blEGaW", "02", ["1", "4", "9", "10", "11"])
    power_station = classify_device("h70fk5", "06", ["1", "2", "3", "4", "21"])
    multi_sensor = classify_device("t8nvdb", "03", ["4", "6", "7", "8"])
    gateway = classify_device("v44j8s", "08", ["2"])
    video_intercom = classify_device("wmX4pg", "11", ["101"])
    door_lock = classify_device("XxxGn1", "11", ["101"])
    speaker = classify_device("GriFPh", "23", ["1", "2", "3", "4", "5", "6"])
    ai_lamp = classify_device("HFyhNc", "22", ["201", "0"])
    ai_companion = classify_device("Eklp7l", "22", ["201", "202", "203"])

    assert known_light.is_light
    assert not catalog_only_light.is_light
    assert not bluetooth_light.is_local_transport
    assert not bluetooth_light.is_light
    assert wifi_environment_sensor.is_environment_sensor
    assert wifi_environment_sensor.is_local_transport
    assert another_wifi_environment_sensor.is_environment_sensor
    assert not zigbee_environment_sensor.is_environment_sensor
    assert not zigbee_environment_sensor.is_local_transport
    assert not bluetooth_environment_sensor.is_environment_sensor
    assert not bluetooth_environment_sensor.is_local_transport
    assert appliance.is_generic_power_device
    assert appliance.supports_switch_entities
    assert tower_fan.is_fan
    assert power_station.is_energy_storage
    assert multi_sensor.is_multi_sensor
    assert gateway.is_gateway
    assert video_intercom.is_video_intercom
    assert not door_lock.is_video_intercom
    assert speaker.is_speaker
    assert ai_lamp.is_light
    assert ai_companion.is_ai_expression_device


def test_apk_catalog_profiles_cover_each_supported_local_family() -> None:
    """Keep entity capability assumptions anchored to bundled APK catalog data."""
    _load_api()
    from cozylife_local.discovery import get_model_info

    expected_dpids = {
        # APK light panels: relay, work mode, temperature/brightness controls,
        # countdown, and normal timer.
        "asdlks": {"1", "2", "3", "4", "13", "14"},
        # APK RGB light panel: relay, mode, brightness, hue, and saturation.
        "kilulk": {"1", "2", "4", "5", "6", "13", "14"},
        # APK multi-gang electrical panel: local relay and gang controls.
        "hjhjkk": {"1", "2", "3", "4", "5", "6", "7"},
        # APK plug panel: relay, countdown/timer, plug options, and metering.
        "hjk1jk": {"1", "2", "3", "18", "19"},
        # Explicitly supported Wi-Fi temperature/humidity sensor.
        "Z4tRml": {"4", "8", "9", "14", "24", "25"},
    }

    for pid, required in expected_dpids.items():
        model = get_model_info(pid)
        assert model is not None
        assert required <= model.dpids, pid


def test_discovery_eligibility_matches_the_supported_local_boundary() -> None:
    """Do not let catalog presence turn deferred product families into entries."""
    _load_api()
    from cozylife_local.discovery import (
        classify_device,
        get_model_info,
        supports_local_entities,
    )

    supported_pids = {
        "hjk1jk",  # electrical socket
        "asdlks",  # light
        "blEGaW",  # fan
        "CJjAvl",  # appliance
        "h70fk5",  # power station
        "Z4tRml",  # sleeping temperature/humidity sensor
        "JTFMlo",  # temperature/humidity/motion sensor
        "GriFPh",  # X7 speaker
        "HFyhNc",  # panel-verified AI lamp
        "Eklp7l",  # panel-verified AI companion expression
    }
    deferred_pids = {
        "m0ldju",  # opaque DIY DPID-51 payload
        "XxxGn1",  # lock/session protocol
        "knAmlh",  # generic AI conversation profile
    }

    for pid in supported_pids:
        model = get_model_info(pid)
        assert model is not None, pid
        classification = classify_device(pid, model.type_code, model.dpids)
        assert supports_local_entities(pid, classification), pid

    for pid in deferred_pids:
        model = get_model_info(pid)
        assert model is not None, pid
        classification = classify_device(pid, model.type_code, model.dpids)
        assert not supports_local_entities(pid, classification), pid


def test_all_catalogued_wifi_electrical_relays_and_meters_get_local_entities() -> None:
    """Keep every APK Wi-Fi electrical relay on the supported local path."""
    _load_api()
    from cozylife_local.discovery import _load_model_catalog, classify_device

    electrical_models = [
        model
        for model in _load_model_catalog().values()
        if model.type_code == "00" and model.network_type == "02"
    ]
    assert electrical_models

    meter_dpids = {"26", "27", "28", "29", "42"}
    for model in electrical_models:
        classification = classify_device(
            model.product_id,
            model.type_code,
            model.dpids,
        )
        if "1" in model.dpids:
            assert classification.supports_switch_entities, model.product_id
        if meter_dpids & model.dpids:
            assert classification.supports_plug_metering, model.product_id


def test_all_catalogued_wifi_appliances_with_power_dpid_get_a_switch() -> None:
    """Expose the documented DPID-1 switch_power control for each appliance."""
    _load_api()
    from cozylife_local.discovery import _load_model_catalog, classify_device

    appliances = [
        model
        for model in _load_model_catalog().values()
        if model.type_code == "05" and model.network_type == "02"
    ]
    assert appliances

    for model in appliances:
        classification = classify_device(
            model.product_id,
            model.type_code,
            model.dpids,
        )
        if "1" in model.dpids:
            assert classification.is_generic_power_device, model.product_id
            assert classification.supports_switch_entities, model.product_id


def test_catalogued_wifi_energy_profiles_with_telemetry_get_local_entities() -> None:
    """Recognize every APK Wi-Fi power-station profile with defined telemetry."""
    _load_api()
    from cozylife_local.discovery import _load_model_catalog, classify_device

    energy_models = [
        model
        for model in _load_model_catalog().values()
        if model.type_code == "06" and model.network_type == "02"
    ]
    assert energy_models

    for model in energy_models:
        classification = classify_device(
            model.product_id,
            model.type_code,
            model.dpids,
        )
        if {"3", "4"} & model.dpids:
            assert classification.is_energy_storage, model.product_id


def test_energy_output_channels_require_the_documented_telemetry_pairing() -> None:
    """Full power stations retain their documented branch telemetry profile."""
    _load_api()
    from cozylife_local.discovery import _load_model_catalog

    energy_models = [
        model
        for model in _load_model_catalog().values()
        if model.type_code == "06" and model.network_type == "02"
    ]
    branch_dpids = {str(dpid) for dpid in range(5, 13)}
    channelled_models = [
        model
        for model in energy_models
        if "1" in model.dpids and branch_dpids <= model.dpids
    ]
    assert len(channelled_models) == 12
    documented_input_dpids = {str(dpid) for dpid in range(22, 30)}
    for model in channelled_models:
        assert documented_input_dpids <= model.dpids, model.product_id
        assert {"2", "3", "4", "21", "30", "31", "32", "44"} <= model.dpids, (
            model.product_id
        )
    assert {model.product_id for model in energy_models if model.dpids == {"1"}} == {
        "JTEnK3",
        "XZUWYv",
    }


def test_manual_sensor_choices_include_only_apk_wifi_profiles() -> None:
    """Manual sleeping-sensor setup must not offer Bluetooth or Zigbee PIDs."""
    _load_api()
    from cozylife_local.discovery import get_wifi_environment_sensor_models

    pids = {model.product_id for model in get_wifi_environment_sensor_models()}
    assert {"Z4tRml", "s1AxFq", "nrQdBz"} <= pids
    assert "bQ2cGF" not in pids
    assert "v2kv9f" not in pids


def test_wifi_temperature_humidity_motion_profile_exposes_motion() -> None:
    """The APK's JTFMlo profile has a documented DPID-6 motion report."""
    _load_api()
    components = types.ModuleType("homeassistant.components")
    binary_sensor_component = types.ModuleType("homeassistant.components.binary_sensor")

    class BinarySensorDeviceClass:
        DOOR = "door"
        MOISTURE = "moisture"
        MOTION = "motion"
        BATTERY = "battery"
        OCCUPANCY = "occupancy"

    binary_sensor_component.BinarySensorDeviceClass = BinarySensorDeviceClass
    binary_sensor_component.BinarySensorEntity = object
    config_entries = types.ModuleType("homeassistant.config_entries")
    config_entries.ConfigEntry = object
    entity = types.ModuleType("homeassistant.helpers.entity")
    entity.DeviceInfo = dict
    update_coordinator = types.ModuleType("homeassistant.helpers.update_coordinator")

    class CoordinatorEntity:
        @classmethod
        def __class_getitem__(cls, _item):
            return cls

    update_coordinator.CoordinatorEntity = CoordinatorEntity
    sys.modules["homeassistant.components"] = components
    sys.modules["homeassistant.components.binary_sensor"] = binary_sensor_component
    sys.modules["homeassistant.config_entries"] = config_entries
    sys.modules["homeassistant.helpers.entity"] = entity
    sys.modules["homeassistant.helpers.update_coordinator"] = update_coordinator
    sys.modules.pop("cozylife_local.binary_sensor", None)
    from cozylife_local.binary_sensor import _binary_sensor_descriptions

    descriptions = _binary_sensor_descriptions("JTFMlo", ["4", "6", "8", "9"])
    assert {(description.key, description.dpid) for description in descriptions} == {
        ("motion", "6"),
    }


def test_wifi_catalogue_flag_wins_over_misleading_contact_sensor_name() -> None:
    """The Wi-Fi-labelled gate magnet is retained on the local contact path."""
    _load_api()
    from cozylife_local.discovery import LOCAL_BINARY_SENSOR_PIDS, get_model_info

    model = get_model_info("o64g7o")
    assert model is not None
    assert model.network_type == "02"
    assert {"7", "9"} <= model.dpids
    assert model.product_id in LOCAL_BINARY_SENSOR_PIDS


def test_platforms_do_not_register_duplicate_schedule_controls() -> None:
    """Do not create a parallel device scheduler."""
    _load_api()
    from cozylife_local.const import PLATFORMS

    assert "time" not in PLATFORMS


def test_all_catalogued_wifi_speakers_get_a_media_player() -> None:
    """The APK's X7 panel verifies the two Wi-Fi speaker profiles."""
    _load_api()
    from cozylife_local.discovery import _load_model_catalog, classify_device

    speakers = [
        model
        for model in _load_model_catalog().values()
        if model.type_code == "23" and model.network_type == "02"
    ]
    assert {model.product_id for model in speakers} == {"GriFPh", "ZhTBTj"}

    for model in speakers:
        classification = classify_device(
            model.product_id, model.type_code, model.dpids
        )
        assert classification.is_speaker, model.product_id


def test_recovered_ai_lamp_panel_adds_its_verified_local_dpids() -> None:
    """AI lamp profiles use the recovered panel's DPID 1/4 control map."""
    _load_api()
    from cozylife_local.discovery import classify_device, get_model_info

    for pid in ("HFyhNc", "fBhCXl"):
        model = get_model_info(pid)
        assert model is not None
        assert {"1", "4"} <= model.dpids
        assert classify_device(pid, model.type_code, model.dpids).is_light


def test_recovered_s3_panel_adds_its_verified_expression_dpid() -> None:
    """The exact S3 panel maps five expression values to local DPID 211."""
    _load_api()
    from cozylife_local.discovery import classify_device, get_model_info

    model = get_model_info("Eklp7l")
    assert model is not None
    assert "211" in model.dpids
    assert classify_device(
        model.product_id, model.type_code, model.dpids
    ).is_ai_expression_device


def test_powered_device_accepts_apk_style_async_state_report() -> None:
    """Every valid local frame updates its owning device, as in the APK."""
    CozyLifeCoordinator = _load_coordinator()

    async def exercise() -> None:
        coordinator = object.__new__(CozyLifeCoordinator)
        coordinator._uses_sensor_polling = False
        coordinator.data = {"1": True, "11": 42}
        coordinator.device = types.SimpleNamespace(ip_address="10.0.0.44")
        updates: list[dict] = []
        coordinator.async_set_updated_data = updates.append

        # This reply is valid local state traffic, but it is deliberately not
        # tied to a request SN. The APK still delivers it to the DID listener.
        await coordinator._async_handle_device_message(
            {"cmd": 2, "sn": "device-event", "msg": {"data": {"1": False}}}
        )

        assert updates == [{"1": False, "11": 42}]

    asyncio.run(exercise())


def test_environment_report_accepts_a_valid_partial_measurement() -> None:
    """A valid reading must not be discarded because its paired DPID is absent."""
    CozyLifeCoordinator = _load_coordinator()
    coordinator = object.__new__(CozyLifeCoordinator)
    coordinator.classification = types.SimpleNamespace(is_environment_sensor=True)

    assert coordinator._has_valid_environment_measurements({"8": 221})
    assert coordinator._has_valid_environment_measurements({"4": 48})
    assert coordinator._has_valid_environment_measurements({"8": 0})
    assert coordinator._has_valid_environment_measurements({"4": 0})
    assert coordinator._has_valid_environment_measurements({"8": 0, "4": 48})
    assert not coordinator._has_valid_environment_measurements({})
    assert not coordinator._has_valid_environment_measurements({"8": None, "4": None})
    assert not coordinator._has_valid_environment_measurements({"8": False})
    assert not coordinator._has_valid_environment_measurements({"4": -1})


def test_sensor_udp_announcement_sends_one_status_request() -> None:
    """A matching UDP presence signal authorizes one local status write."""
    CozyLifeCoordinator = _load_coordinator()

    async def exercise() -> None:
        coordinator = object.__new__(CozyLifeCoordinator)
        entry = types.SimpleNamespace(data={"ip_address": "10.0.0.115"})
        updates: list[dict] = []

        def update_entry(_entry, *, data) -> None:
            updates.append(data)

        coordinator.hass = types.SimpleNamespace(
            config_entries=types.SimpleNamespace(async_update_entry=update_entry),
        )
        calls: list[str] = []

        async def send_status() -> bool:
            calls.append("status")
            return True

        async def update_ip_address(_ip_address: str) -> bool:
            return False

        coordinator._entry = entry
        coordinator._uses_sensor_polling = True
        coordinator.device = types.SimpleNamespace(
            async_send_status=send_status,
            async_update_ip_address=update_ip_address,
        )
        await coordinator.async_handle_discovered_address("10.0.0.115")

        assert updates == []
        assert calls == ["status"]
        assert not hasattr(coordinator, "_sensor_status_pending")

    asyncio.run(exercise())


def test_sensor_status_attempt_loop_is_independent_of_udp_announcements() -> None:
    """Sleeping-sensor attempts are not limited to the ten-second UDP cache."""
    CozyLifeCoordinator = _load_coordinator()
    coordinator_module = sys.modules["cozylife_local.coordinator"]
    original_interval = coordinator_module.SENSOR_STATUS_ATTEMPT_INTERVAL_SECONDS
    coordinator_module.SENSOR_STATUS_ATTEMPT_INTERVAL_SECONDS = 0.001

    async def exercise() -> None:
        coordinator = object.__new__(CozyLifeCoordinator)
        attempts: list[None] = []

        async def send_status() -> bool:
            attempts.append(None)
            return True

        coordinator.hass = types.SimpleNamespace(
            loop=asyncio.get_running_loop(),
            async_create_background_task=lambda coroutine, _name: asyncio.create_task(
                coroutine
            ),
        )
        coordinator.device = types.SimpleNamespace(
            async_send_status=send_status,
            async_close=lambda: asyncio.sleep(0),
        )
        coordinator._uses_sensor_polling = True
        coordinator._sensor_status_task = None
        coordinator._identity_refresh_task = None

        await coordinator.async_start_discovery()
        first_task = coordinator._sensor_status_task
        await coordinator.async_start_discovery()
        assert coordinator._sensor_status_task is first_task
        await asyncio.sleep(0.01)
        assert len(attempts) >= 2

        await coordinator.async_close()
        assert coordinator._sensor_status_task is None

    try:
        asyncio.run(exercise())
    finally:
        coordinator_module.SENSOR_STATUS_ATTEMPT_INTERVAL_SECONDS = original_interval


def test_powered_device_keeps_periodic_availability_refresh() -> None:
    """A powered light must become unavailable after a local power cut."""
    CozyLifeCoordinator = _load_coordinator()
    coordinator_module = sys.modules["cozylife_local.coordinator"]
    original_classify = coordinator_module.classify_device
    coordinator_module.classify_device = lambda *_args: types.SimpleNamespace(
        is_environment_sensor=False
    )
    try:
        device = types.SimpleNamespace(
            pid="light",
            device_type_code="00",
            dpid=["1"],
            ip_address="192.0.2.50",
            set_message_listener=lambda _listener: None,
        )
        coordinator = CozyLifeCoordinator(
            types.SimpleNamespace(), device, types.SimpleNamespace()
        )
        assert coordinator.update_interval == timedelta(seconds=30)
    finally:
        coordinator_module.classify_device = original_classify


def test_powered_refresh_uses_the_apk_listener_path() -> None:
    """A powered refresh writes attr:[0] without using UDP as availability."""
    CozyLifeCoordinator = _load_coordinator()

    async def exercise() -> None:
        coordinator = object.__new__(CozyLifeCoordinator)
        coordinator._uses_sensor_polling = False
        coordinator.hass = object()
        requests = 0

        async def async_request_properties(*, timeout: float | None = None) -> bool:
            nonlocal requests
            assert timeout == 1.0
            requests += 1
            return True

        coordinator.device = types.SimpleNamespace(
            device_id="plug-1",
            ip_address="192.0.2.50",
            async_request_properties=async_request_properties,
        )
        coordinator.data = {"1": True, "28": 12}

        async def get_discovery(_hass):
            raise AssertionError("powered availability must not depend on UDP presence")

        coordinator_module = sys.modules["cozylife_local.coordinator"]
        with patch.object(
            coordinator_module, "async_get_background_discovery", get_discovery
        ):
            assert await coordinator._async_get_local_state() == {"1": True, "28": 12}
        assert requests == 1

    asyncio.run(exercise())


def test_powered_refresh_marks_unavailable_only_when_its_local_request_fails() -> None:
    """A stale UDP announcement cannot fail an otherwise reachable product."""
    CozyLifeCoordinator = _load_coordinator()

    async def exercise() -> None:
        coordinator = object.__new__(CozyLifeCoordinator)
        coordinator._uses_sensor_polling = False
        coordinator.hass = object()
        requested = 0

        async def async_request_properties(*, timeout: float | None = None) -> bool:
            nonlocal requested
            assert timeout == 1.0
            requested += 1
            return False

        coordinator.device = types.SimpleNamespace(
            device_id="plug-1",
            ip_address="192.0.2.50",
            async_request_properties=async_request_properties,
        )

        async def get_discovery(_hass):
            raise AssertionError("powered availability must not depend on UDP presence")

        coordinator_module = sys.modules["cozylife_local.coordinator"]
        with patch.object(
            coordinator_module, "async_get_background_discovery", get_discovery
        ):
            try:
                await coordinator._async_get_local_state()
            except RuntimeError as err:
                assert str(err) == "Failed to request state from CozyLife device"
            else:
                raise AssertionError("A failed local request must fail refresh")
        assert requested == 1

    asyncio.run(exercise())


def test_powered_udp_announcement_queues_one_gated_refresh() -> None:
    """A powered device refreshes only after a matching UDP announcement."""
    CozyLifeCoordinator = _load_coordinator()

    async def exercise() -> None:
        coordinator = object.__new__(CozyLifeCoordinator)
        updates: list[dict] = []
        refreshes: list[bool] = []
        entry = types.SimpleNamespace(data={"ip_address": "10.0.0.44"})

        def update_entry(_entry, *, data) -> None:
            updates.append(data)

        coordinator.hass = types.SimpleNamespace(
            config_entries=types.SimpleNamespace(async_update_entry=update_entry)
        )
        coordinator._entry = entry
        coordinator._uses_sensor_polling = False
        coordinator.device = types.SimpleNamespace()

        async def update_ip_address(ip_address: str) -> bool:
            return ip_address == "10.0.0.45"

        async def refresh() -> None:
            refreshes.append(True)

        coordinator.device.async_update_ip_address = update_ip_address
        coordinator.async_request_refresh = refresh

        await coordinator.async_handle_discovered_address("10.0.0.44")
        await coordinator.async_handle_discovered_address("10.0.0.45")

        assert refreshes == [True, True]
        assert updates == [{"ip_address": "10.0.0.45"}]

    asyncio.run(exercise())


def test_sensor_udp_announcement_sends_status_without_an_ip_change() -> None:
    """A presence announcement triggers one state request even at the same IP."""
    CozyLifeCoordinator = _load_coordinator()

    async def exercise() -> None:
        coordinator = object.__new__(CozyLifeCoordinator)
        entry = types.SimpleNamespace(data={"ip_address": "10.0.0.115"})
        updates: list[dict] = []

        def update_entry(_entry, *, data) -> None:
            updates.append(data)

        coordinator.hass = types.SimpleNamespace(
            config_entries=types.SimpleNamespace(async_update_entry=update_entry),
            loop=types.SimpleNamespace(time=lambda: 100.0),
        )
        coordinator._entry = entry
        coordinator._uses_sensor_polling = True
        coordinator.device = types.SimpleNamespace()

        async def update_ip_address(_ip_address: str) -> bool:
            return False

        status_requests = 0

        async def send_status() -> bool:
            nonlocal status_requests
            status_requests += 1
            return True

        coordinator.device.async_update_ip_address = update_ip_address
        coordinator.device.async_send_status = send_status

        await coordinator.async_handle_discovered_address("10.0.0.115")

        assert updates == []
        assert status_requests == 1

    asyncio.run(exercise())


def test_sleeping_sensor_promotes_verified_local_device_id() -> None:
    """A provisional setup ID is replaced only by a matching local handshake."""
    CozyLifeCoordinator = _load_coordinator()

    async def exercise() -> None:
        coordinator = object.__new__(CozyLifeCoordinator)
        entry = types.SimpleNamespace(
            entry_id="sensor-entry",
            data={
                "device_id": "sleeping_sensor_Z4tRml_10.0.0.115",
                "ip_address": "10.0.0.115",
                "pid": "Z4tRml",
                "device_type_code": "03",
                "dpids": ["4", "8", "9"],
            }
        )
        updates: list[dict] = []

        def update_entry(updated_entry, *, data) -> None:
            updated_entry.data = data
            assert migrated_entity_ids == [
                ("sleeping_sensor_Z4tRml_10.0.0.115", "76608860c4d7fdd94485")
            ]
            updates.append(data)

        coordinator.hass = types.SimpleNamespace(
            config_entries=types.SimpleNamespace(async_update_entry=update_entry)
        )
        coordinator._entry = entry
        coordinator.device = types.SimpleNamespace(
            ip_address="10.0.0.115",
            pid="Z4tRml",
            device_id="76608860c4d7fdd94485",
            device_type_code="02",
            dpid=["4", "8", "9", "14"],
        )

        async def update_device_info() -> bool:
            return True

        coordinator.device.async_update_device_info = update_device_info
        unregistered: list[str] = []
        unregistered_addresses: list[str] = []
        registered: list[tuple[str, object]] = []
        discovery = types.SimpleNamespace(
            unregister_device=lambda device_id, entry_id: unregistered.append(
                (device_id, entry_id)
            ),
            unregister_address=unregistered_addresses.append,
            register_device=lambda device_id, entry_id, callback: registered.append(
                (device_id, entry_id, callback)
            ),
        )

        async def get_discovery(_hass):
            return discovery

        coordinator._identity_refresh_task = object()
        migrated_entity_ids: list[tuple[str, str]] = []
        coordinator._async_migrate_placeholder_entity_unique_ids = (
            lambda old_id, new_id: migrated_entity_ids.append((old_id, new_id))
        )

        with patch("cozylife_local.coordinator.async_get_background_discovery", get_discovery):
            await coordinator._async_promote_sensor_identity()

        assert updates == [
            {
                "device_id": "76608860c4d7fdd94485",
                "ip_address": "10.0.0.115",
                "pid": "Z4tRml",
                "device_type_code": "02",
                "dpids": ["4", "8", "9", "14"],
            }
        ]
        assert migrated_entity_ids == [
            ("sleeping_sensor_Z4tRml_10.0.0.115", "76608860c4d7fdd94485")
        ]
        assert unregistered == [
            ("sleeping_sensor_Z4tRml_10.0.0.115", "sensor-entry")
        ]
        assert unregistered_addresses == ["10.0.0.115"]
        assert registered == [
            (
                "76608860c4d7fdd94485",
                "sensor-entry",
                coordinator.async_handle_discovered_address,
            )
        ]
        assert coordinator._identity_refresh_task is None

    asyncio.run(exercise())


def test_sleeping_sensor_rejects_a_mismatched_local_identity() -> None:
    """A response from a different product must not replace the setup identity."""
    CozyLifeCoordinator = _load_coordinator()

    async def exercise() -> None:
        coordinator = object.__new__(CozyLifeCoordinator)
        entry = types.SimpleNamespace(
            data={
                "device_id": "sleeping_sensor_Z4tRml_10.0.0.115",
                "pid": "Z4tRml",
                "device_type_code": "03",
                "dpids": ["4", "8", "9"],
            }
        )
        updates: list[dict] = []
        coordinator.hass = types.SimpleNamespace(
            config_entries=types.SimpleNamespace(
                async_update_entry=lambda *_args, **kwargs: updates.append(kwargs)
            )
        )
        coordinator._entry = entry
        coordinator.device = types.SimpleNamespace(
            ip_address="10.0.0.115",
            pid="other-product",
            device_id="other-device-id",
            device_type_code="00",
            dpid=["1"],
        )

        async def update_device_info() -> bool:
            return True

        coordinator.device.async_update_device_info = update_device_info
        closed: list[bool] = []

        async def close() -> None:
            closed.append(True)

        def restore_from_cache(device_id, pid, device_type_code, dpids) -> None:
            coordinator.device.device_id = device_id
            coordinator.device.pid = pid
            coordinator.device.device_type_code = device_type_code
            coordinator.device.dpid = dpids

        coordinator.device.async_close = close
        coordinator.device.restore_from_cache = restore_from_cache
        coordinator._udp_discovery = None
        coordinator._identity_refresh_task = object()

        await coordinator._async_promote_sensor_identity()

        assert updates == []
        assert closed == [True]
        assert entry.data["device_id"] == "sleeping_sensor_Z4tRml_10.0.0.115"
        assert coordinator.device.pid == "Z4tRml"
        assert coordinator.device.dpid == ["4", "8", "9"]
        assert coordinator._identity_refresh_task is None

    asyncio.run(exercise())


def test_udp_discovery_registration_is_owner_safe_across_remove_and_readd() -> None:
    """An unloading old entry cannot erase the new entry's DID callback."""
    background_discovery = _load_background_discovery()
    service = object.__new__(background_discovery.CozyLifeBackgroundDiscovery)
    service._entry_callbacks = {}
    service._pending_discovery_ids = set()

    async def old_callback(_ip_address: str) -> None:
        return None

    async def new_callback(_ip_address: str) -> None:
        return None

    service.register_device("plug-1", "old-entry", old_callback)
    service.register_device("plug-1", "new-entry", new_callback)
    service.unregister_device("plug-1", "old-entry")

    assert service._entry_callbacks["plug-1"] == ("new-entry", new_callback)


def test_udp_discovery_only_starts_one_pending_flow_per_did() -> None:
    """Repeated UDP replies do not repeatedly create discovery flows."""
    background_discovery = _load_background_discovery()
    calls: list[object] = []
    service = object.__new__(background_discovery.CozyLifeBackgroundDiscovery)
    service.hass = types.SimpleNamespace(
        config_entries=types.SimpleNamespace(async_entries=lambda _domain: [])
    )
    service._devices = {}
    service._entry_callbacks = {}
    service._address_callbacks = {}
    service._pending_discovery_ids = set()

    device = background_discovery.UdpDiscoveredDevice(
        device_id="plug-1",
        ip_address="10.0.0.44",
        pid="hjk1jk",
        device_type_code="05",
    )
    with patch.object(
        background_discovery.discovery_flow,
        "async_create_flow",
        lambda *_args, **_kwargs: calls.append(True),
    ):
        service._handle_device(device)
        service._handle_device(device)

    assert calls == [True]

def test_sensor_identity_migration_preserves_existing_entity_ids() -> None:
    """Verified sensor IDs retain user-owned entity IDs and avoid collisions."""
    CozyLifeCoordinator = _load_coordinator()
    old_device_id = "sleeping_sensor_Z4tRml_10.0.0.115"
    new_device_id = "76608860c4d7fdd94485"
    entries = [
        types.SimpleNamespace(
            entity_id="sensor.spikes_temperature",
            unique_id=f"{old_device_id}_temperature",
            domain="sensor",
            platform="cozylife_local",
        ),
        types.SimpleNamespace(
            entity_id="sensor.spikes_humidity",
            unique_id=f"{old_device_id}_humidity",
            domain="sensor",
            platform="cozylife_local",
        ),
        types.SimpleNamespace(
            entity_id="sensor.unrelated",
            unique_id="unrelated_temperature",
            domain="sensor",
            platform="cozylife_local",
        ),
    ]
    updates: list[tuple[str, str]] = []
    registry = types.SimpleNamespace(
        async_get_entity_id=lambda domain, platform, unique_id: (
            "sensor.existing_humidity"
            if (domain, platform, unique_id)
            == ("sensor", "cozylife_local", f"{new_device_id}_humidity")
            else None
        ),
        async_update_entity=lambda entity_id, *, new_unique_id: updates.append(
            (entity_id, new_unique_id)
        ),
    )
    coordinator_module = sys.modules["cozylife_local.coordinator"]
    coordinator_module.er = types.SimpleNamespace(
        async_get=lambda _hass: registry,
        async_entries_for_config_entry=lambda _registry, _entry_id: entries,
    )
    coordinator = object.__new__(CozyLifeCoordinator)
    coordinator.hass = object()
    coordinator._entry = types.SimpleNamespace(entry_id="sensor-entry")

    coordinator._async_migrate_placeholder_entity_unique_ids(
        old_device_id, new_device_id
    )


def test_schedule_entity_migration_removes_only_retired_entities() -> None:
    """Upgrade cleanup removes retired scheduler entities without broad deletion."""
    _load_api()
    config_entries = types.ModuleType("homeassistant.config_entries")
    config_entries.ConfigEntry = object
    config_entries.SOURCE_IMPORT = "import"
    homeassistant_const = types.ModuleType("homeassistant.const")
    homeassistant_const.EVENT_HOMEASSISTANT_STARTED = "started"
    core = sys.modules["homeassistant.core"]
    core.CoreState = types.SimpleNamespace(running="running")

    background_discovery = types.ModuleType("cozylife_local.background_discovery")
    background_discovery.DATA_DISCOVERY_SERVICE = "discovery"
    background_discovery.async_get_background_discovery = lambda _hass: None
    coordinator = types.ModuleType("cozylife_local.coordinator")
    coordinator.CozyLifeCoordinator = object
    discovery = types.ModuleType("cozylife_local.discovery")
    discovery.async_load_model_catalog = lambda _hass: None

    registry_entries = {
        "switch.plug_schedule_enabled": types.SimpleNamespace(
            config_entry_id="entry-1",
            platform="cozylife_local",
            unique_id="plug-1_schedule_enabled",
        ),
        "select.plug_schedule_action": types.SimpleNamespace(
            config_entry_id="entry-1",
            platform="cozylife_local",
            unique_id="plug-1_schedule_action",
        ),
        "switch.plug_power": types.SimpleNamespace(
            config_entry_id="entry-1",
            platform="cozylife_local",
            unique_id="plug-1_0",
        ),
        "switch.other_schedule": types.SimpleNamespace(
            config_entry_id="other-entry",
            platform="cozylife_local",
            unique_id="plug-2_schedule_enabled",
        ),
    }
    removed: list[str] = []
    entity_registry = types.ModuleType("homeassistant.helpers.entity_registry")
    entity_registry.async_get = lambda _hass: types.SimpleNamespace(
        entities=registry_entries,
        async_remove=removed.append,
    )

    sys.modules["homeassistant.config_entries"] = config_entries
    sys.modules["homeassistant.const"] = homeassistant_const
    sys.modules["homeassistant"].config_entries = config_entries
    sys.modules["homeassistant"].const = homeassistant_const
    helpers = types.ModuleType("homeassistant.helpers")
    helpers.entity_registry = entity_registry
    sys.modules["homeassistant.helpers"] = helpers
    sys.modules["homeassistant.helpers.entity_registry"] = entity_registry
    sys.modules["cozylife_local.background_discovery"] = background_discovery
    sys.modules["cozylife_local.coordinator"] = coordinator
    sys.modules["cozylife_local.discovery"] = discovery
    spec = importlib.util.spec_from_file_location(
        "cozylife_local",
        COMPONENT_DIR / "__init__.py",
        submodule_search_locations=[str(COMPONENT_DIR)],
    )
    assert spec is not None and spec.loader is not None
    integration = importlib.util.module_from_spec(spec)
    sys.modules["cozylife_local"] = integration
    spec.loader.exec_module(integration)

    integration._async_remove_obsolete_schedule_entities(
        object(), types.SimpleNamespace(entry_id="entry-1")
    )

    assert removed == [
        "switch.plug_schedule_enabled",
        "select.plug_schedule_action",
    ]
