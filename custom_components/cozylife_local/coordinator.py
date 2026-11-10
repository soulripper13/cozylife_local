import asyncio
import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .background_discovery import async_get_background_discovery
from .const import (
    PLUG_CURRENT,
    PLUG_LED_STATUS,
    PLUG_OVERCURRENT_PROTECTION,
    PLUG_POWER_ON_STATE,
    PLUG_TIMER_STATUS,
    PLUG_VOLTAGE,
    LIGHT_COUNTDOWN,
    SENSOR_HUMIDITY,
    SENSOR_TEMPERATURE,
)
from .cozylife_api import CozyLifeDevice
from .discovery import classify_device, get_model_info
from .debug import summarize_dpids

_LOGGER = logging.getLogger(__name__)
_UPDATE_LOGGER = logging.getLogger(f"{__name__}.updates")
_UPDATE_LOGGER.setLevel(logging.INFO)

# This only bounds one TCP connection attempt. It is not a sensor wake or
# report schedule.
SENSOR_CONNECT_TIMEOUT_SECONDS = 0.2
# A Home Assistant-side retry cadence for the same local ``cmd:2 / attr:[1]``
# request used by the APK's device screen. It is deliberately independent of
# UDP discovery: the APK's ten-second broadcast/cache lifecycle is an app
# address-routing mechanism, not evidence that a battery sensor's TCP window
# only exists immediately after an announcement. Status writes are serialized
# by CozyLifeDevice, so this never overlaps another status write.
SENSOR_STATUS_ATTEMPT_INTERVAL_SECONDS = 0.25
POWERED_DEVICE_AVAILABILITY_INTERVAL = timedelta(seconds=30)
# The APK uses a one-second TCP connection allowance. Apply that bound only
# to background health work; interactive commands keep the device's normal
# timeout and are never intentionally slowed by availability polling.
POWERED_DEVICE_AVAILABILITY_TIMEOUT_SECONDS = 1.0
ENVIRONMENT_SENSOR_MEASUREMENTS = {
    SENSOR_TEMPERATURE: "temperature",
    SENSOR_HUMIDITY: "humidity",
}
SWITCH_OPTION_FOLLOWUP_DPIDS = (
    PLUG_POWER_ON_STATE,
    PLUG_LED_STATUS,
    PLUG_OVERCURRENT_PROTECTION,
)
PLUG_METERING_FOLLOWUP_DPIDS = (
    PLUG_CURRENT,
    PLUG_TIMER_STATUS,
    PLUG_VOLTAGE,
)


def _is_valid_environment_measurement(
    dpid: str,
    value: Any,
) -> bool:
    """Return whether a temperature/humidity reading looks like a real report."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False

    if dpid == SENSOR_TEMPERATURE:
        return -400 <= value <= 800

    if dpid == SENSOR_HUMIDITY:
        return 0 <= value <= 100

    return True


class CozyLifeCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Manages fetching data from a single CozyLife device."""

    def __init__(self, hass: HomeAssistant, device: CozyLifeDevice, entry: ConfigEntry):
        """Initialize coordinator."""
        self.classification = classify_device(
            device.pid,
            device.device_type_code,
            device.dpid,
        )
        # Catalog category ``Sensor`` also contains Zigbee/BLE products. Only
        # explicitly identified Wi-Fi environment sensors use local sleeping
        # sensor transport.
        self._uses_sensor_polling = self.classification.is_environment_sensor
        # The APK keeps a socket reader alive; local reports arrive on that
        # stream rather than from an assumed report schedule.  Keep wake-window
        # probing outside DataUpdateCoordinator: it uses the configured local
        # address while UDP discovery independently refreshes that address.
        #
        # The APK's UDP presence cache decides when its app queues a command;
        # it has no equivalent of Home Assistant's entity availability.
        # Keep a bounded HA-side local health refresh for powered products so
        # a device that is physically powered off becomes unavailable again.
        # Sleeping sensors retain their separate wake-window task and are not
        # subjected to this periodic availability probe.
        interval = (
            None
            if self._uses_sensor_polling
            else POWERED_DEVICE_AVAILABILITY_INTERVAL
        )

        super().__init__(
            hass,
            _UPDATE_LOGGER,
            name=f"CozyLife device {device.ip_address}",
            update_interval=interval,
            config_entry=entry,
        )
        self.device = device
        self._sensor_status_task: asyncio.Task[None] | None = None
        self._identity_refresh_task: asyncio.Task[None] | None = None
        self._powered_followup_task: asyncio.Task[None] | None = None
        self._closed = False
        if self._uses_sensor_polling:
            # A sleeping device must not let one unsuccessful local connection
            # monopolize the transport when its next UDP presence announcement
            # arrives.
            self.device.set_timeout(SENSOR_CONNECT_TIMEOUT_SECONDS)
            # Linux must expire unacknowledged writes to a sleeping peer;
            # drain() alone can buffer requests for minutes on a stale socket.
            # This does not expire an ACKed connection awaiting measurements.
            self.device.set_tcp_user_timeout(1000)
        # The APK routes every valid local TCP frame to the device listener by
        # the socket's local address.  Do the same for powered devices as
        # well as sleeping sensors: some valid state reports are asynchronous
        # and do not carry the request sequence number we sent.
        self.device.set_message_listener(self._async_handle_device_message)
        self._entry = entry

    async def async_start_discovery(self) -> None:
        """Start independent UDP discovery and serialized sensor requests.

        The APK daemon broadcasts command 0 every ten seconds to keep its own
        address cache current. The configured HA entry also keeps the APK's
        status request available at a bounded local cadence: a sleeping sensor
        controls when it accepts the request, so an app-side cache update must
        not be treated as the sole opportunity to read it.
        """
        if getattr(self, "_closed", False):
            return
        if self._uses_sensor_polling:
            await async_get_background_discovery(self.hass)
            if getattr(self, "_closed", False):
                return
            if self._sensor_status_task is None:
                self._sensor_status_task = self.hass.async_create_background_task(
                    self._async_request_sensor_status(),
                    "cozylife sensor status requests",
                )

    async def _async_request_sensor_status(self) -> None:
        """Keep one non-overlapping local status request available per interval.

        A completed write is transport activity only. Actual measurements are
        published exclusively by ``_async_handle_device_message`` after a
        frame contains a valid temperature or humidity DPID.
        """
        while True:
            started = self.hass.loop.time()
            await self.device.async_send_status()
            remaining = SENSOR_STATUS_ATTEMPT_INTERVAL_SECONDS - (
                self.hass.loop.time() - started
            )
            if remaining > 0:
                await asyncio.sleep(remaining)

    async def async_handle_discovered_address(self, ip_address: str) -> None:
        """Use an APK discovery reply to refresh this entry's local address."""
        if getattr(self, "_closed", False):
            return
        moved = await self.device.async_update_ip_address(ip_address)
        if moved:
            self.hass.config_entries.async_update_entry(
                self._entry,
                data={**self._entry.data, "ip_address": ip_address},
            )
        if self._uses_sensor_polling:
            # Preserve the app's opportunistic cache-to-writer path as an
            # additional immediate attempt. The entry's independent status
            # task remains responsible for retrying between announcements.
            await self.device.async_send_status()
            return
        # The APK's Wi-Fi writer queues a local cmd-2 request only after the
        # device is present in its UDP cache. A matching announcement is that
        # presence signal here; refresh while it is current rather than on an
        # unrelated host-side timer.
        await self.async_request_refresh()

    @callback
    def _async_migrate_placeholder_entity_unique_ids(
        self, old_device_id: str, new_device_id: str
    ) -> None:
        """Keep entity IDs when a sleeping sensor gains its verified device ID."""
        prefix = f"{old_device_id}_"
        registry = er.async_get(self.hass)
        for registry_entry in er.async_entries_for_config_entry(
            registry, self._entry.entry_id
        ):
            old_unique_id = registry_entry.unique_id
            if not old_unique_id.startswith(prefix):
                continue

            new_unique_id = f"{new_device_id}_{old_unique_id.removeprefix(prefix)}"
            existing_entity_id = registry.async_get_entity_id(
                registry_entry.domain,
                registry_entry.platform,
                new_unique_id,
            )
            if existing_entity_id not in (None, registry_entry.entity_id):
                _LOGGER.warning(
                    "Keeping a CozyLife entity on placeholder identity because "
                    "the verified identity is already in use",
                )
                continue

            registry.async_update_entity(
                registry_entry.entity_id,
                new_unique_id=new_unique_id,
            )

    async def _async_promote_sensor_identity(self) -> None:
        """Replace a sleeping-sensor setup ID after a verified local handshake.

        Manual sleeping-sensor setup intentionally works while the device is
        unavailable, so it starts with an entry-local placeholder ID. A UDP
        reply contains the device's real ID and cannot match that placeholder.
        Once a report proves the device is awake, read and verify the actual
        ID over its local TCP connection before using it for UDP discovery.
        """
        try:
            expected_pid = self._entry.data.get("pid")
            if not await self.device.async_update_device_info():
                return
            if self.device.pid != expected_pid:
                _LOGGER.warning(
                    "Ignoring local identity: expected PID %s, got %s",
                    expected_pid,
                    self.device.pid,
                )
                await self.device.async_close()
                cached_device_id = self._entry.data.get("device_id")
                cached_type_code = self._entry.data.get("device_type_code")
                cached_dpids = self._entry.data.get("dpids")
                if cached_device_id and cached_type_code and cached_dpids:
                    self.device.restore_from_cache(
                        cached_device_id,
                        expected_pid,
                        cached_type_code,
                        [str(dpid) for dpid in cached_dpids],
                    )
                return

            device_id = self.device.device_id
            if not device_id or device_id == self._entry.data.get("device_id"):
                return

            old_device_id = self._entry.data.get("device_id")
            old_ip_address = self._entry.data.get("ip_address")
            data = {
                **self._entry.data,
                "device_id": device_id,
                "device_type_code": self.device.device_type_code,
            }
            if self.device.dpid:
                data["dpids"] = self.device.dpid
            if old_device_id:
                self._async_migrate_placeholder_entity_unique_ids(
                    old_device_id, device_id
                )
            self.hass.config_entries.async_update_entry(self._entry, data=data)

            discovery = await async_get_background_discovery(self.hass)
            if old_device_id:
                discovery.unregister_device(old_device_id, self._entry.entry_id)
            if old_ip_address:
                discovery.unregister_address(old_ip_address)
            discovery.register_device(
                device_id,
                self._entry.entry_id,
                self.async_handle_discovered_address,
            )
            _LOGGER.info(
                "Verified local device ID for sleeping CozyLife sensor",
            )
        finally:
            self._identity_refresh_task = None

    @property
    def is_sensor(self) -> bool:
        return self._uses_sensor_polling

    async def async_close(self) -> None:
        """Release the device reader owned by this config entry."""
        self._closed = True
        followup = getattr(self, "_powered_followup_task", None)
        if followup is not None:
            followup.cancel()
            try:
                await followup
            except asyncio.CancelledError:
                pass
            self._powered_followup_task = None
        if self._sensor_status_task:
            self._sensor_status_task.cancel()
            try:
                await self._sensor_status_task
            except asyncio.CancelledError:
                pass
            self._sensor_status_task = None
        if self._identity_refresh_task:
            self._identity_refresh_task.cancel()
            try:
                await self._identity_refresh_task
            except asyncio.CancelledError:
                pass
            self._identity_refresh_task = None
        await self.device.async_close()

    async def _async_handle_device_message(self, frame: dict[str, Any]) -> None:
        """Publish a sensor report received by the persistent device reader."""
        if getattr(self, "_closed", False):
            return
        message = frame.get("msg", frame)
        if frame.get("res", 0) != 0:
            return
        state_data = message.get("data") if isinstance(message, dict) else None
        if not isinstance(state_data, dict):
            return

        received_state = {str(dpid): value for dpid, value in state_data.items()}
        if self._uses_sensor_polling:
            from .measurement_filter import MeasurementFilter

            options = getattr(getattr(self, "_entry", None), "options", {})
            # Valid zero measurements pass through unless the optional filter
            # identifies them as outliers, just like any other numeric sample.
            enabled = options.get("filter_outliers", False)
            received_state = {
                key: value for key, value in received_state.items()
                if key not in ENVIRONMENT_SENSOR_MEASUREMENTS
                or _is_valid_environment_measurement(key, value)
            }
            if enabled:
                if not hasattr(self, "_measurement_filter"):
                    self._measurement_filter = MeasurementFilter()
                received_state = self._measurement_filter.filter(
                    frame.get("sn"), received_state,
                    {SENSOR_TEMPERATURE: options.get("temperature_radius", 2.0) * 10,
                     SENSOR_HUMIDITY: options.get("humidity_radius", 10.0)},
                )
        if self._uses_sensor_polling and not self._has_valid_environment_measurements(
            received_state
        ):
            measurement_diagnostics = {
                dpid: {
                    "present": dpid in received_state,
                    "type": type(received_state.get(dpid)).__name__,
                    "valid": _is_valid_environment_measurement(
                        dpid,
                        received_state.get(dpid),
                    ),
                }
                for dpid in ENVIRONMENT_SENSOR_MEASUREMENTS
            }
            _LOGGER.debug(
                "Ignoring local environment report with invalid measurements: %s",
                measurement_diagnostics,
            )
            return

        # Device state reports can be partial.  The APK delivers them to the
        # device listener as updates, rather than discarding the prior state.
        state_data = {**(self.data or {}), **received_state}
        if self._uses_sensor_polling:
            state_data = self._preserve_environment_measurements(state_data)

        self.async_set_updated_data(state_data)
        self._async_request_missing_powered_state(received_state)
        if (
            self._uses_sensor_polling
            and self._entry.data.get("device_id", "").startswith("sleeping_sensor_")
            and self._identity_refresh_task is None
        ):
            self._identity_refresh_task = self.hass.async_create_background_task(
                self._async_promote_sensor_identity(),
                "cozylife sensor identity verification",
            )
        _LOGGER.debug(
            "[COZYLIFE] Received local state report with DPIDs: %s",
            summarize_dpids(received_state),
        )

    def _async_request_missing_powered_state(
        self, received_state: dict[str, Any]
    ) -> None:
        """Request only advertised plug fields omitted by a state report.

        The original local integration used this documented DPID query after a
        partial plug state response. Keep it scoped to advertised plug fields;
        it does not probe unknown product capabilities.
        """
        classification = getattr(self, "classification", None)
        if (
            self._uses_sensor_polling
            or classification is None
            or getattr(self, "_powered_followup_task", None) is not None
        ):
            return

        # A cached inventory can itself be partial after an upgrade or restart.
        # Preserve the original integration's catalogue merge: the APK profile
        # is the verified local capability inventory for this known PID, while
        # the live frame determines which of those capabilities are missing.
        advertised_dpids = set(self.device.dpid or [])
        model_info = get_model_info(self.device.pid)
        if model_info is not None:
            advertised_dpids.update(model_info.dpids)
        candidate_dpids: set[str] = set()
        if classification.supports_switch_entities:
            candidate_dpids.update(SWITCH_OPTION_FOLLOWUP_DPIDS)
        if classification.supports_plug_metering:
            candidate_dpids.update(PLUG_METERING_FOLLOWUP_DPIDS)
        if classification.is_light:
            candidate_dpids.add(LIGHT_COUNTDOWN)

        missing_dpids = [
            dpid
            for dpid in sorted(candidate_dpids, key=int)
            if dpid in advertised_dpids and dpid not in received_state
        ]
        if not missing_dpids:
            return

        self._powered_followup_task = self.hass.async_create_background_task(
            self._async_read_missing_powered_state(missing_dpids),
            "cozylife powered DPID follow-up",
        )

    async def _async_read_missing_powered_state(self, dpids: list[str]) -> None:
        """Issue one serialized read for advertised fields omitted by a frame."""
        try:
            await self.device.async_get_state(dpids)
        finally:
            self._powered_followup_task = None

    def _preserve_environment_measurements(
        self,
        state_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Keep the last good temp/humidity values across placeholder reports."""
        if not self.classification.is_environment_sensor:
            return state_data

        previous_data = self.data or {}
        sanitized = dict(state_data)
        for dpid, name in ENVIRONMENT_SENSOR_MEASUREMENTS.items():
            current = sanitized.get(dpid)
            previous = previous_data.get(dpid)
            if _is_valid_environment_measurement(
                dpid,
                current,
            ):
                continue

            if _is_valid_environment_measurement(
                dpid,
                previous,
            ):
                sanitized[dpid] = previous
                _LOGGER.debug(
                    "[COZYLIFE] Ignoring invalid %s reading; preserving the previous value",
                    name,
                )
            elif dpid in sanitized:
                sanitized.pop(dpid)
                _LOGGER.debug(
                    "[COZYLIFE] Ignoring invalid %s reading",
                    name,
                )

        return sanitized

    def _has_valid_environment_measurements(self, state_data: dict[str, Any]) -> bool:
        """Return whether a report contains at least one usable measurement."""
        if not self.classification.is_environment_sensor:
            return True

        return any(
            _is_valid_environment_measurement(
                dpid,
                state_data.get(dpid),
            )
            for dpid in ENVIRONMENT_SENSOR_MEASUREMENTS
        )

    async def _async_get_local_state(self) -> dict[str, Any]:
        """Request the APK state payload appropriate for this device family."""
        if self._uses_sensor_polling:
            state_data = await self.device.async_get_status()
            if state_data is None:
                raise UpdateFailed("Failed to query state from CozyLife device")
            return state_data

        # The APK queues ``attr:[0]`` as a local write and routes a later
        # state frame to the device listener; it does not make this command
        # contingent on a correlated or immediate state reply. Its short-lived
        # UDP cache is only an app-side address-routing mechanism, not device
        # availability evidence: powered products can accept the local TCP
        # request while answering discovery only intermittently. A successful
        # TCP connect and completed local write provisionally keeps availability;
        # the API retires unanswered powered sessions on a later operation.
        # A failed local request makes the entity unavailable. Waiting for a
        # state frame was introduced in 1.1.3 and can hold the shared writer
        # lock long enough to delay a user control command.
        if not await self.device.async_request_properties(
            timeout=POWERED_DEVICE_AVAILABILITY_TIMEOUT_SECONDS
        ):
            raise UpdateFailed("Failed to request state from CozyLife device")
        return dict(self.data or {})

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from device."""
        try:
            if (
                (not self.device.device_id or not self.device.dpid)
                and not await self.device.async_update_device_info()
            ):
                raise UpdateFailed(
                    "Failed to get full device info for CozyLife device"
                )

            state_data = await self._async_get_local_state()
            if (
                getattr(self, "_entry", None)
                and self._entry.data.get("ip_address") != self.device.ip_address
            ):
                self.hass.config_entries.async_update_entry(
                    self._entry,
                    data={**self._entry.data, "ip_address": self.device.ip_address},
                )

            # The APK uses attr:[1] for sensor status and attr:[0] for powered
            # product state. Both are delivered as listener frames and may be
            # partial.
            state_data = self._preserve_environment_measurements(state_data)
            _LOGGER.debug("[COZYLIFE] Successfully fetched state with DPIDs: %s", summarize_dpids(state_data))
            return state_data

        except UpdateFailed:
            # For sleeping sensors: if we have previous data, return it to keep entities available.
            # The device only wakes periodically so connection failures are expected.
            if self._uses_sensor_polling and self.data is not None:
                return self.data
            raise

        except Exception as err:
            _LOGGER.error("Error communicating with CozyLife device: %s", type(err).__name__)
            raise UpdateFailed("Error communicating with CozyLife device") from err
