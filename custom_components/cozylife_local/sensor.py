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
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.restore_state import RestoreEntity
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
from .discovery import get_model_info

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

    entities = [CozyLifeIPAddressSensor(coordinator)]
    dpids = set(coordinator.device.dpid)
    model_info = get_model_info(coordinator.device.pid)
    if model_info:
        dpids.update(model_info.dpids)

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
            ),
            PlugSensorDescription(
                dpid=PLUG_VOLTAGE,
                key="voltage",
                name="Voltage",
                device_class=SensorDeviceClass.VOLTAGE,
                state_class=SensorStateClass.MEASUREMENT,
                native_unit_of_measurement=UnitOfElectricPotential.VOLT,
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
        device_class: SensorDeviceClass | None,
        state_class: SensorStateClass | None,
        native_unit_of_measurement: str | None,
        scale: int = 1,
        entity_category: EntityCategory | None = None,
    ) -> None:
        self.dpid = dpid
        self.key = key
        self.name = name
        self.device_class = device_class
        self.state_class = state_class
        self.native_unit_of_measurement = native_unit_of_measurement
        self.scale = scale
        self.entity_category = entity_category


def _device_info(coordinator: CozyLifeCoordinator) -> DeviceInfo:
    """Return shared Home Assistant device info for CozyLife entities."""
    return DeviceInfo(
        identifiers={(DOMAIN, coordinator.device.device_id)},
        name=coordinator.device.device_model_name,
        manufacturer="CozyLife",
        model=coordinator.device.pid,
    )


class CozyLifeIPAddressSensor(CoordinatorEntity[CozyLifeCoordinator], SensorEntity):
    """Diagnostic sensor exposing the device IP address."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:ip-network"

    def __init__(self, coordinator: CozyLifeCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_name = f"{coordinator.device.device_model_name} IP Address"
        self._attr_unique_id = f"{coordinator.device.device_id}_ip_address"

    @property
    def device_info(self) -> DeviceInfo:
        return _device_info(self.coordinator)

    @property
    def native_value(self) -> str:
        return self.coordinator.device.ip_address

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()


class CozyLifeSensorBase(
    CoordinatorEntity[CozyLifeCoordinator],
    SensorEntity,
    RestoreEntity,
):
    """Base class for CozyLife sensors."""

    def __init__(self, coordinator: CozyLifeCoordinator, entry: ConfigEntry, sensor_type: str):
        super().__init__(coordinator)
        self._sensor_type = sensor_type
        self._attr_unique_id = f"{coordinator.device.device_id}_{sensor_type}"
        self._last_valid_native_value: float | None = None

    @property
    def device_info(self) -> DeviceInfo:
        return _device_info(self.coordinator)

    @callback
    def _handle_coordinator_update(self) -> None:
        if (value := self._native_value_from_data()) is not None:
            self._last_valid_native_value = value
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Restore the last recorded value while sleeping sensors are offline."""
        await super().async_added_to_hass()

        last_state = await self.async_get_last_state()
        if last_state is None or last_state.state in {
            STATE_UNAVAILABLE,
            STATE_UNKNOWN,
        }:
            return

        try:
            self._last_valid_native_value = float(last_state.state)
        except (TypeError, ValueError):
            return

    def _native_value_from_data(self) -> Optional[float]:
        """Return the current native value from coordinator data."""
        raise NotImplementedError

    @property
    def native_value(self) -> Optional[float]:
        value = self._native_value_from_data()
        if value is not None:
            self._last_valid_native_value = value
            return value

        return self._last_valid_native_value


class CozyLifeTemperatureSensor(CozyLifeSensorBase):
    """Temperature sensor entity."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(self, coordinator: CozyLifeCoordinator, entry: ConfigEntry):
        super().__init__(coordinator, entry, "temperature")
        self._attr_name = f"{coordinator.device.device_model_name} Temperature"

    def _native_value_from_data(self) -> Optional[float]:
        raw = (self.coordinator.data or {}).get(SENSOR_TEMPERATURE)
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

    def _native_value_from_data(self) -> Optional[float]:
        raw = (self.coordinator.data or {}).get(SENSOR_HUMIDITY)
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

    def _native_value_from_data(self) -> Optional[float]:
        raw = (self.coordinator.data or {}).get(SENSOR_BATTERY)
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
        self._attr_entity_category = description.entity_category

    @property
    def device_info(self) -> DeviceInfo:
        return _device_info(self.coordinator)

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
