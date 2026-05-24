import logging
import math
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
)
from .discovery import classify_device

_LOGGER = logging.getLogger(__name__)
_UPDATE_LOGGER = logging.getLogger(f"{__name__}.updates")
_UPDATE_LOGGER.setLevel(logging.INFO)

UPDATE_INTERVAL = timedelta(seconds=30)
SENSOR_WAKE_WINDOW_LEAD = 10
SENSOR_CATCH_POLL_INTERVAL = 1
SENSOR_CATCH_TIMEOUT = 120
REPORT_INTERVAL_IGNORED_LIMIT = 3
ENVIRONMENT_SENSOR_MEASUREMENTS = {
    SENSOR_TEMPERATURE: "temperature",
    SENSOR_HUMIDITY: "humidity",
}


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
        self._report_interval = max(
            MIN_SENSOR_REPORT_INTERVAL,
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
        self._first_success_wait_logged = False
        self._report_interval_ignored_count = 0
        self._report_interval_unsupported = False
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

        self._last_sensor_response_at = dt_util.utcnow()
        delay = _next_sensor_wake_delay(int(self._effective_report_interval))
        self.update_interval = timedelta(seconds=delay)
        self._missed_expected_wake_logged = False
        self._catch_started_at = None
        self._fallback_logged = False
        self._first_success_wait_logged = False
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

    async def _async_update_data(self) -> Dict[str, Any]:
        """Fetch data from device."""
        try:
            if not self.device.device_id or not self.device.dpid:
                if not await self.device.async_update_device_info():
                    raise UpdateFailed(f"Failed to get full device info for {self.device.ip_address}")

            state_data = await self.device.async_get_state()
            if state_data is None:
                raise UpdateFailed(f"Failed to query state from device {self.device.ip_address}")

            # Check if report interval needs to be pushed (first connection or reverted by device)
            if (
                self.classification.is_environment_sensor
                and state_data.get(SENSOR_REPORT_INTERVAL_DPID) != self._report_interval
            ):
                reported_interval = state_data.get(SENSOR_REPORT_INTERVAL_DPID)
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

                if self._report_interval_unsupported:
                    state_data[SENSOR_REPORT_INTERVAL_DPID] = self._effective_report_interval
                elif await self.device.async_set_state({SENSOR_REPORT_INTERVAL_DPID: self._report_interval}):
                    state_data[SENSOR_REPORT_INTERVAL_DPID] = self._report_interval
                    _LOGGER.debug(
                        "[COZYLIFE] Report interval update accepted by %s",
                        self.device.ip_address,
                    )
                else:
                    _LOGGER.warning(
                        "[COZYLIFE] Report interval update to %ss was not accepted by %s",
                        self._report_interval,
                        self.device.ip_address,
                    )
            elif self.classification.is_environment_sensor:
                self._effective_report_interval = self._report_interval
                self._report_interval_ignored_count = 0
                self._report_interval_unsupported = False

            # Push sensitivity settings if configured and different from device values
            if self.classification.is_environment_sensor:
                updates = {}
                if self._temp_sensitivity is not None and state_data.get(SENSOR_TEMP_SENSITIVITY_DPID) != self._temp_sensitivity:
                    updates[SENSOR_TEMP_SENSITIVITY_DPID] = self._temp_sensitivity
                if self._humidity_sensitivity is not None and state_data.get(SENSOR_HUMIDITY_SENSITIVITY_DPID) != self._humidity_sensitivity:
                    updates[SENSOR_HUMIDITY_SENSITIVITY_DPID] = self._humidity_sensitivity
                if updates:
                    _LOGGER.debug(f"[COZYLIFE] Pushing sensitivity settings {updates} to {self.device.ip_address}")
                    await self.device.async_set_state(updates)

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
