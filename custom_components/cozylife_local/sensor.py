import logging
from typing import Optional

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfTemperature, EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    SENSOR_TEMPERATURE,
    SENSOR_HUMIDITY,
    SENSOR_BATTERY,
    SWITCH,
    KNOWN_SENSOR_PIDS,
)
from .coordinator import CozyLifeCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities,
):
    """Set up CozyLife sensor platform for temperature/humidity devices."""
    coordinator: CozyLifeCoordinator = hass.data[DOMAIN][config_entry.entry_id]

    if not coordinator.device.dpid:
        return

    # Only set up sensor entities for known sensor devices (by PID or DPID pattern)
    if not (
        coordinator.device.pid in KNOWN_SENSOR_PIDS
        or (
            SENSOR_TEMPERATURE in coordinator.device.dpid
            and SENSOR_BATTERY in coordinator.device.dpid
            and SWITCH not in coordinator.device.dpid
        )
    ):
        _LOGGER.debug(
            f"Device {coordinator.device.ip_address} is not a temp/humidity sensor, skipping sensor platform setup."
        )
        return

    entities = [
        CozyLifeTemperatureSensor(coordinator, config_entry),
        CozyLifeHumiditySensor(coordinator, config_entry),
        CozyLifeBatterySensor(coordinator, config_entry),
    ]
    async_add_entities(entities)


class CozyLifeSensorBase(CoordinatorEntity[CozyLifeCoordinator], SensorEntity):
    """Base class for CozyLife sensors."""

    def __init__(self, coordinator: CozyLifeCoordinator, entry: ConfigEntry, sensor_type: str):
        super().__init__(coordinator)
        self._sensor_type = sensor_type
        self._attr_unique_id = f"{coordinator.device.device_id}_{sensor_type}"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.device.device_id)},
            name=self.coordinator.device.device_model_name,
            manufacturer="CozyLife",
            model=self.coordinator.device.pid,
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()


class CozyLifeTemperatureSensor(CozyLifeSensorBase):
    """Temperature sensor entity."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(self, coordinator: CozyLifeCoordinator, entry: ConfigEntry):
        super().__init__(coordinator, entry, "temperature")
        self._attr_name = f"{coordinator.device.device_model_name} Temperature"

    @property
    def native_value(self) -> Optional[float]:
        raw = self.coordinator.data.get(SENSOR_TEMPERATURE)
        if raw is None:
            return None
        return round(raw / 10, 1)


class CozyLifeHumiditySensor(CozyLifeSensorBase):
    """Humidity sensor entity."""

    _attr_device_class = SensorDeviceClass.HUMIDITY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(self, coordinator: CozyLifeCoordinator, entry: ConfigEntry):
        super().__init__(coordinator, entry, "humidity")
        self._attr_name = f"{coordinator.device.device_model_name} Humidity"

    @property
    def native_value(self) -> Optional[float]:
        raw = self.coordinator.data.get(SENSOR_HUMIDITY)
        if raw is None:
            return None
        return float(raw)


class CozyLifeBatterySensor(CozyLifeSensorBase):
    """Battery sensor entity."""

    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: CozyLifeCoordinator, entry: ConfigEntry):
        super().__init__(coordinator, entry, "battery")
        self._attr_name = f"{coordinator.device.device_model_name} Battery"

    @property
    def native_value(self) -> Optional[float]:
        raw = self.coordinator.data.get(SENSOR_BATTERY)
        if raw is None:
            return None
        return round(raw / 10, 1)
