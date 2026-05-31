import asyncio
import logging
import math
import time
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .cozylife_api import CozyLifeDevice
from .const import (
    DEFAULT_SENSOR_REPORT_INTERVAL,
    MIN_SENSOR_REPORT_INTERVAL,
    SENSOR_HUMIDITY,
    SENSOR_HUMIDITY_SENSITIVITY_DPID,
    SENSOR_TEMPERATURE,
    SENSOR_REPORT_INTERVAL_DPID,
    SENSOR_TEMP_SENSITIVITY_DPID,
    STANDARD_SENSOR_REPORT_INTERVAL,
)
from .discovery import classify_device

_LOGGER = logging.getLogger(__name__)
_UPDATE_LOGGER = logging.getLogger(f"{__name__}.updates")
_UPDATE_LOGGER.setLevel(logging.INFO)

UPDATE_INTERVAL = timedelta(seconds=30)
SENSOR_WAKE_WINDOW_LEAD = 10
SENSOR_CATCH_POLL_INTERVAL = 1
SENSOR_CATCH_TIMEOUT = 120
SENSOR_WAKE_SAMPLE_SECONDS = 4
SENSOR_WAKE_SAMPLE_DELAY = 0.15
SENSOR_REPORT_INTERVAL_CONFIRM_SECONDS = 5
SENSOR_REPORT_INTERVAL_CONFIRM_DELAY = 0.35
REPORT_INTERVAL_IGNORED_LIMIT = 3
ENVIRONMENT_SENSOR_MEASUREMENTS = {
    SENSOR_TEMPERATURE: "temperature",
    SENSOR_HUMIDITY: "humidity",
}
EXPERIMENTAL_SHORT_INTERVAL_KEY = "experimental_short_interval"


def _next_sensor_wake_delay(report_interval: int) -> int:
    """Return how long to wait before polling near the next expected wake."""
    return max(SENSOR_CATCH_POLL_INTERVAL, report_interval - SENSOR_WAKE_WINDOW_LEAD)


def _next_sensor_cycle_delay(
    last_response_at: datetime,
    report_interval: int,
    now: datetime,
) -> int:
    """Return delay to the next expected wake window from a previous response."""
    elapsed = max(0, (now - last_response_at).total_seconds())
    cycle = math.floor((elapsed + SENSOR_WAKE_WINDOW_LEAD) / report_interval) + 1
    next_window_elapsed = (cycle * report_interval) - SENSOR_WAKE_WINDOW_LEAD
    return max(
        SENSOR_CATCH_POLL_INTERVAL,
        int(next_window_elapsed - elapsed),
    )


def _is_valid_environment_measurement(
    dpid: str,
    value: Any,
    *,
    zero_is_placeholder: bool,
) -> bool:
    """Return whether a temperature/humidity reading looks like a real report."""
    if not isinstance(value, (int, float)):
        return False

    # Z4tRml can intermittently report 0 while asleep/waking. Treat that as a
    # placeholder so Home Assistant does not record false zero spikes.
    if zero_is_placeholder and value == 0:
        return False

    if dpid == SENSOR_TEMPERATURE:
        return -400 <= value <= 800

    if dpid == SENSOR_HUMIDITY:
        return 0 < value <= 100

    return True


class CozyLifeCoordinator(DataUpdateCoordinator[Dict[str, Any]]):
    """Manages fetching data from a single CozyLife device."""

    def __init__(self, hass: HomeAssistant, device: CozyLifeDevice, entry: ConfigEntry):
        """Initialize coordinator."""
        self.classification = classify_device(
            device.pid,
            device.device_type_code,
            device.dpid,
        )
        self._uses_sensor_polling = (
            self.classification.is_sensor_category
            or self.classification.is_environment_sensor
        )
        self._experimental_short_interval = bool(
            entry.options.get(
                EXPERIMENTAL_SHORT_INTERVAL_KEY,
                entry.data.get(EXPERIMENTAL_SHORT_INTERVAL_KEY, False),
            )
        )
        min_report_interval = (
            MIN_SENSOR_REPORT_INTERVAL
            if self._experimental_short_interval
            else STANDARD_SENSOR_REPORT_INTERVAL
        )
        self._report_interval = max(
            min_report_interval,
            int(
                entry.options.get(
                    "report_interval",
                    entry.data.get("report_interval", DEFAULT_SENSOR_REPORT_INTERVAL),
                )
            ),
        )
        interval = (
            timedelta(seconds=SENSOR_CATCH_POLL_INTERVAL)
            if self._uses_sensor_polling
            else UPDATE_INTERVAL
        )

        super().__init__(
            hass,
            _UPDATE_LOGGER,
            name=f"CozyLife device {device.ip_address}",
            update_interval=interval,
            config_entry=entry,
        )
        self.device = device
        self._temp_sensitivity = entry.options.get("temp_sensitivity", None)
        self._humidity_sensitivity = entry.options.get("humidity_sensitivity", None)
        self._effective_report_interval = self._report_interval
        self._missed_expected_wake_logged = False
        self._catch_started_at: datetime | None = None
        self._fallback_logged = False
        self._last_sensor_response_at: datetime | None = None
        self._current_sensor_response_at: datetime | None = None
        self._first_success_wait_logged = False
        self._report_interval_ignored_count = 0
        self._report_interval_unsupported = False
        self._experimental_interval_retry_logged = False
        if self.classification.is_environment_sensor:
            _LOGGER.debug(
                "[COZYLIFE] Sensor %s configured with report_interval=%ss, "
                "starting catch polling every %ss",
                device.ip_address,
                self._report_interval,
                SENSOR_CATCH_POLL_INTERVAL,
            )

    @property
    def is_sensor(self) -> bool:
        return self._uses_sensor_polling

    def _schedule_next_sensor_wake(self) -> None:
        """Poll shortly before the sensor is expected to wake again."""
        if not self._uses_sensor_polling:
            return

        now = dt_util.utcnow()
        response_at = self._current_sensor_response_at or now
        self._last_sensor_response_at = response_at
        delay = _next_sensor_cycle_delay(
            response_at,
            int(self._effective_report_interval),
            now,
        )
        self.update_interval = timedelta(seconds=delay)
        self._missed_expected_wake_logged = False
        self._catch_started_at = None
        self._fallback_logged = False
        self._first_success_wait_logged = False
        self._current_sensor_response_at = None
        _LOGGER.debug(
            "[COZYLIFE] Sensor %s responded; next poll window starts in %ss "
            "(effective report interval: %ss)",
            self.device.ip_address,
            delay,
            self._effective_report_interval,
        )

    def _schedule_sensor_catch_polling(self) -> None:
        """Poll frequently while waiting for the sleeping sensor to respond."""
        if not self._uses_sensor_polling:
            return

        now = dt_util.utcnow()
        if self._catch_started_at is None:
            self._catch_started_at = now

        catch_seconds = (now - self._catch_started_at).total_seconds()
        if catch_seconds >= SENSOR_CATCH_TIMEOUT:
            if self._last_sensor_response_at is None:
                self.update_interval = timedelta(seconds=SENSOR_CATCH_POLL_INTERVAL)
                if not self._first_success_wait_logged:
                    self._first_success_wait_logged = True
                    _LOGGER.debug(
                        "[COZYLIFE] Sensor %s has not responded yet; continuing "
                        "first-acquisition polling every %ss so the short wake "
                        "window is not missed.",
                        self.device.ip_address,
                        SENSOR_CATCH_POLL_INTERVAL,
                    )
                return

            delay = _next_sensor_cycle_delay(
                self._last_sensor_response_at,
                int(self._effective_report_interval),
                now,
            )
            self.update_interval = timedelta(seconds=delay)
            self._catch_started_at = None
            if not self._fallback_logged:
                self._fallback_logged = True
                _LOGGER.debug(
                    "[COZYLIFE] Sensor %s did not respond within %ss of the "
                    "expected wake window; next poll window starts in %ss "
                    "based on report_interval=%ss.",
                    self.device.ip_address,
                    SENSOR_CATCH_TIMEOUT,
                    delay,
                    self._effective_report_interval,
                )
            return

        self.update_interval = timedelta(seconds=SENSOR_CATCH_POLL_INTERVAL)
        if not self._missed_expected_wake_logged:
            self._missed_expected_wake_logged = True
            _LOGGER.debug(
                "[COZYLIFE] Sensor %s missed the expected wake window; "
                "catch polling every %ss",
                self.device.ip_address,
                SENSOR_CATCH_POLL_INTERVAL,
            )

    def _preserve_environment_measurements(
        self,
        state_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Keep the last good temp/humidity values across placeholder reports."""
        if not self.classification.is_environment_sensor:
            return state_data

        previous_data = self.data or {}
        sanitized = dict(state_data)
        zero_is_placeholder = self.device.pid == "Z4tRml"

        for dpid, name in ENVIRONMENT_SENSOR_MEASUREMENTS.items():
            current = sanitized.get(dpid)
            previous = previous_data.get(dpid)
            if _is_valid_environment_measurement(
                dpid,
                current,
                zero_is_placeholder=zero_is_placeholder,
            ):
                continue

            if _is_valid_environment_measurement(
                dpid,
                previous,
                zero_is_placeholder=zero_is_placeholder,
            ):
                sanitized[dpid] = previous
                _LOGGER.debug(
                    "[COZYLIFE] Ignoring invalid %s reading from %s: %r; "
                    "preserving previous value %r",
                    name,
                    self.device.ip_address,
                    current,
                    previous,
                )
            elif dpid in sanitized:
                sanitized.pop(dpid)
                _LOGGER.debug(
                    "[COZYLIFE] Ignoring invalid %s reading from %s: %r",
                    name,
                    self.device.ip_address,
                    current,
                )

        return sanitized

    def _has_valid_environment_measurements(self, state_data: Dict[str, Any]) -> bool:
        """Return whether state data contains usable temperature and humidity."""
        if not self.classification.is_environment_sensor:
            return True

        zero_is_placeholder = self.device.pid == "Z4tRml"
        return all(
            _is_valid_environment_measurement(
                dpid,
                state_data.get(dpid),
                zero_is_placeholder=zero_is_placeholder,
            )
            for dpid in ENVIRONMENT_SENSOR_MEASUREMENTS
        )

    async def _async_get_environment_state(self) -> Dict[str, Any]:
        """Sample a waking environment sensor until real measurements appear."""
        state_data = await self.device.async_get_state()
        if state_data is None:
            raise UpdateFailed(f"Failed to query state from device {self.device.ip_address}")

        if self._uses_sensor_polling and self._current_sensor_response_at is None:
            self._current_sensor_response_at = dt_util.utcnow()

        if not self.classification.is_environment_sensor:
            return state_data

        if self._has_valid_environment_measurements(state_data):
            return state_data

        if self._experimental_short_interval:
            _LOGGER.debug(
                "[COZYLIFE] Sensor %s returned placeholder environment "
                "measurements; prioritizing experimental report interval "
                "reinforcement for this short wake window",
                self.device.ip_address,
            )
            return state_data

        end_time = time.monotonic() + SENSOR_WAKE_SAMPLE_SECONDS
        best_state = state_data
        while time.monotonic() < end_time:
            await asyncio.sleep(SENSOR_WAKE_SAMPLE_DELAY)
            next_state = await self.device.async_get_state()
            if next_state is None:
                continue

            best_state = {**best_state, **next_state}
            if self._has_valid_environment_measurements(best_state):
                _LOGGER.debug(
                    "[COZYLIFE] Sensor %s produced valid environment "
                    "measurements during wake sampling: %s",
                    self.device.ip_address,
                    best_state,
                )
                return best_state

        _LOGGER.debug(
            "[COZYLIFE] Sensor %s wake sampling ended without valid "
            "temperature/humidity; using best state: %s",
            self.device.ip_address,
            best_state,
        )
        return best_state

    async def _async_confirm_report_interval(self, state_data: Dict[str, Any]) -> Dict[str, Any]:
        """Write the report interval and keep it set while the sensor is awake."""
        if self._report_interval_unsupported:
            state_data[SENSOR_REPORT_INTERVAL_DPID] = self._effective_report_interval
            return state_data

        target_interval = self._report_interval
        best_state = dict(state_data)
        end_time = time.monotonic() + SENSOR_REPORT_INTERVAL_CONFIRM_SECONDS
        write_attempts = 0
        accepted_write = False
        confirmed_write = best_state.get(SENSOR_REPORT_INTERVAL_DPID) == target_interval

        while True:
            write_attempts += 1
            accepted = await self.device.async_set_state(
                {SENSOR_REPORT_INTERVAL_DPID: target_interval}
            )
            if not accepted:
                _LOGGER.warning(
                    "[COZYLIFE] Report interval update attempt %s to %ss was "
                    "not accepted by %s",
                    write_attempts,
                    target_interval,
                    self.device.ip_address,
                )
                break

            accepted_write = True
            self._effective_report_interval = target_interval
            await asyncio.sleep(SENSOR_REPORT_INTERVAL_CONFIRM_DELAY)
            readback = await self.device.async_get_state()
            if readback is not None:
                best_state.update(readback)
                if readback.get(SENSOR_REPORT_INTERVAL_DPID) == target_interval:
                    confirmed_write = True
                    self._effective_report_interval = target_interval
                    _LOGGER.debug(
                        "[COZYLIFE] Report interval update to %ss confirmed by "
                        "%s after %s attempt(s)",
                        target_interval,
                        self.device.ip_address,
                        write_attempts,
                    )
                    if (
                        not self._experimental_short_interval
                        or time.monotonic() >= end_time
                    ):
                        return best_state
                    continue

                _LOGGER.debug(
                    "[COZYLIFE] Report interval write attempt %s for %s read "
                    "back %s instead of %ss",
                    write_attempts,
                    self.device.ip_address,
                    readback.get(SENSOR_REPORT_INTERVAL_DPID),
                    target_interval,
                )
            elif accepted_write:
                _LOGGER.debug(
                    "[COZYLIFE] Report interval write attempt %s to %ss was "
                    "accepted by %s but readback was unavailable; assuming "
                    "the sensor returned to sleep.",
                    write_attempts,
                    target_interval,
                    self.device.ip_address,
                )
                break

            if (
                not self._experimental_short_interval
                or time.monotonic() >= end_time
            ):
                break

        if self._experimental_short_interval and (confirmed_write or accepted_write):
            if await self.device.async_set_state({SENSOR_REPORT_INTERVAL_DPID: target_interval}):
                best_state[SENSOR_REPORT_INTERVAL_DPID] = target_interval
                self._effective_report_interval = target_interval
                _LOGGER.debug(
                    "[COZYLIFE] Sent final report interval reinforcement to %ss "
                    "for %s near the end of the wake window",
                    target_interval,
                    self.device.ip_address,
                )
            else:
                _LOGGER.debug(
                    "[COZYLIFE] Final report interval reinforcement to %ss for "
                    "%s was not accepted; the sensor may have returned to sleep",
                    target_interval,
                    self.device.ip_address,
                )

        if confirmed_write or accepted_write:
            self._effective_report_interval = target_interval
        else:
            try:
                self._effective_report_interval = int(
                    best_state.get(SENSOR_REPORT_INTERVAL_DPID)
                )
            except (TypeError, ValueError):
                self._effective_report_interval = target_interval

        return best_state

    async def _async_update_data(self) -> Dict[str, Any]:
        """Fetch data from device."""
        try:
            if not self.device.device_id or not self.device.dpid:
                if not await self.device.async_update_device_info():
                    raise UpdateFailed(f"Failed to get full device info for {self.device.ip_address}")

            state_data = await self._async_get_environment_state()

            # Push sensitivity settings before report interval reinforcement so
            # DPID 14 can be the final write near the end of the wake window.
            if self.classification.is_environment_sensor:
                updates = {}
                if self._temp_sensitivity is not None and state_data.get(SENSOR_TEMP_SENSITIVITY_DPID) != self._temp_sensitivity:
                    updates[SENSOR_TEMP_SENSITIVITY_DPID] = self._temp_sensitivity
                if self._humidity_sensitivity is not None and state_data.get(SENSOR_HUMIDITY_SENSITIVITY_DPID) != self._humidity_sensitivity:
                    updates[SENSOR_HUMIDITY_SENSITIVITY_DPID] = self._humidity_sensitivity
                if updates:
                    _LOGGER.debug(f"[COZYLIFE] Pushing sensitivity settings {updates} to {self.device.ip_address}")
                    if await self.device.async_set_state(updates):
                        state_data.update(updates)

            # Check if report interval needs to be pushed (first connection or reverted by device)
            if (
                self.classification.is_environment_sensor
                and (
                    self._experimental_short_interval
                    or state_data.get(SENSOR_REPORT_INTERVAL_DPID) != self._report_interval
                )
            ):
                reported_interval = state_data.get(SENSOR_REPORT_INTERVAL_DPID)
                if reported_interval != self._report_interval:
                    _LOGGER.debug(
                        "[COZYLIFE] Report interval is %s, configured value is %ss "
                        "for %s",
                        reported_interval,
                        self._report_interval,
                        self.device.ip_address,
                    )
                if self._missed_expected_wake_logged or self._fallback_logged:
                    try:
                        self._effective_report_interval = int(reported_interval)
                    except (TypeError, ValueError):
                        self._effective_report_interval = self._report_interval
                    else:
                        if not self._report_interval_unsupported:
                            _LOGGER.debug(
                                "[COZYLIFE] Sensor %s did not wake on requested "
                                "report_interval=%ss; scheduling from device-reported "
                                "interval=%ss",
                                self.device.ip_address,
                                self._report_interval,
                                self._effective_report_interval,
                            )
                    if (
                        not self._report_interval_unsupported
                        and self._effective_report_interval != self._report_interval
                    ):
                        if self._experimental_short_interval:
                            if not self._experimental_interval_retry_logged:
                                self._experimental_interval_retry_logged = True
                                _LOGGER.debug(
                                    "[COZYLIFE] Sensor %s reverted from "
                                    "experimental report_interval=%ss to %ss; "
                                    "continuing to reapply the experimental "
                                    "interval on future wakes.",
                                    self.device.ip_address,
                                    self._report_interval,
                                    self._effective_report_interval,
                                )
                        else:
                            self._report_interval_ignored_count += 1
                            if (
                                self._report_interval_ignored_count
                                >= REPORT_INTERVAL_IGNORED_LIMIT
                            ):
                                self._report_interval_unsupported = True
                                _LOGGER.debug(
                                    "[COZYLIFE] Sensor %s keeps reporting "
                                    "report_interval=%ss after %s write attempts; "
                                    "stopping report_interval writes for this "
                                    "runtime.",
                                    self.device.ip_address,
                                    self._effective_report_interval,
                                    self._report_interval_ignored_count,
                                )

                state_data = await self._async_confirm_report_interval(state_data)
            elif self.classification.is_environment_sensor:
                self._effective_report_interval = self._report_interval
                self._report_interval_ignored_count = 0
                self._report_interval_unsupported = False

            state_data = self._preserve_environment_measurements(state_data)
            self._schedule_next_sensor_wake()
            _LOGGER.debug(f"[COZYLIFE] Successfully fetched state for {self.device.ip_address}: {state_data}")
            return state_data

        except UpdateFailed:
            # For sleeping sensors: if we have previous data, return it to keep entities available.
            # The device only wakes periodically so connection failures are expected.
            if self._uses_sensor_polling and self.data is not None:
                self._schedule_sensor_catch_polling()
                return self.data if self.data else {}
            raise

        except Exception as err:
            _LOGGER.error(f"Error communicating with device {self.device.ip_address}: {err}")
            raise UpdateFailed(f"Error communicating with device {self.device.ip_address}: {err}") from err
