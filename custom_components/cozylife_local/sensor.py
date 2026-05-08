import logging
from typing import Optional

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    SENSOR_TEMPERATURE,
    SENSOR_HUMIDITY,
    SENSOR_BATTERY,
    PLUG_ENERGY,
    PLUG_CURRENT,
    PLUG_POWER,
    PLUG_VOLTAGE,
)
from .coordinator import CozyLifeCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities,
):
    """Set up CozyLife sensor platform."""
    coordinator: CozyLifeCoordinator = hass.data[DOMAIN][config_entry.entry_id]

    if not coordinator.device.dpid:
        return

    entities = []
    dpids = coordinator.device.dpid

    if coordinator.classification.is_environment_sensor:
        entities.extend(
            [
                CozyLifeTemperatureSensor(coordinator, config_entry),
                CozyLifeHumiditySensor(coordinator, config_entry),
                CozyLifeBatterySensor(coordinator, config_entry),
            ]
        )

    if coordinator.classification.supports_plug_metering:
        plug_sensor_descriptions = [
            PlugSensorDescription(
                dpid=PLUG_ENERGY,
                key="energy",
                name="Energy",
                device_class=SensorDeviceClass.ENERGY,
                state_class=SensorStateClass.TOTAL_INCREASING,
                native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
                scale=1000,
            ),
            PlugSensorDescription(
                dpid=PLUG_CURRENT,
                key="current",
                name="Current",
                device_class=SensorDeviceClass.CURRENT,
                state_class=SensorStateClass.MEASUREMENT,
                native_unit_of_measurement=UnitOfElectricCurrent.MILLIAMPERE,
            ),
            PlugSensorDescription(
                dpid=PLUG_POWER,
                key="power",
                name="Power",
                device_class=SensorDeviceClass.POWER,
                state_class=SensorStateClass.MEASUREMENT,
                native_unit_of_measurement=UnitOfPower.WATT,
                scale=10,
            ),
            PlugSensorDescription(
                dpid=PLUG_VOLTAGE,
                key="voltage",
                name="Voltage",
                device_class=SensorDeviceClass.VOLTAGE,
                state_class=SensorStateClass.MEASUREMENT,
                native_unit_of_measurement=UnitOfElectricPotential.VOLT,
                scale=10,
            ),
        ]

        entities.extend(
            CozyLifePlugSensor(coordinator, description)
            for description in plug_sensor_descriptions
            if description.dpid in dpids
        )

    if not entities:
        _LOGGER.debug(
            f"Device {coordinator.device.ip_address} has no supported sensor DPIDs, skipping sensor platform setup."
        )
        return

    async_add_entities(entities)


class PlugSensorDescription:
    """Description for a CozyLife smart plug metering sensor."""

    def __init__(
        self,
        dpid: str,
        key: str,
        name: str,
        device_class: SensorDeviceClass,
        state_class: SensorStateClass,
        native_unit_of_measurement: str,
        scale: int = 1,
    ) -> None:
        self.dpid = dpid
        self.key = key
        self.name = name
        self.device_class = device_class
        self.state_class = state_class
        self.native_unit_of_measurement = native_unit_of_measurement
        self.scale = scale


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


class CozyLifePlugSensor(CoordinatorEntity[CozyLifeCoordinator], SensorEntity):
    """Metering sensor for CozyLife smart plugs."""

    def __init__(
        self,
        coordinator: CozyLifeCoordinator,
        description: PlugSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self._description = description
        self._attr_name = f"{coordinator.device.device_model_name} {description.name}"
        self._attr_unique_id = f"{coordinator.device.device_id}_{description.key}"
        self._attr_device_class = description.device_class
        self._attr_state_class = description.state_class
        self._attr_native_unit_of_measurement = description.native_unit_of_measurement

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.device.device_id)},
            name=self.coordinator.device.device_model_name,
            manufacturer="CozyLife",
            model=self.coordinator.device.pid,
        )

    @property
    def native_value(self) -> Optional[float]:
        raw = self.coordinator.data.get(self._description.dpid)
        if raw is None:
            return None

        value = raw / self._description.scale
        if self._description.scale == 1:
            return float(value)
        return round(value, 3)

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()
