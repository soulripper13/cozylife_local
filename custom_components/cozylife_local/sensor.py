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
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    PLUG_ADDED_ENERGY,
    PLUG_CURRENT,
    PLUG_ENERGY,
    PLUG_POWER,
    PLUG_VOLTAGE,
    SENSOR_BATTERY,
    SENSOR_HUMIDITY,
    SENSOR_TEMPERATURE,
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

    if (
        not coordinator.classification.is_local_transport
        or not coordinator.device.dpid
    ):
        return

    entities = [CozyLifeIPAddressSensor(coordinator)]
    dpids = set(coordinator.device.dpid)

    if coordinator.classification.is_environment_sensor or coordinator.classification.is_multi_sensor:
        entities.extend([
            CozyLifeTemperatureSensor(coordinator, config_entry),
            CozyLifeHumiditySensor(coordinator, config_entry),
        ])
        if SENSOR_BATTERY in dpids:
            entities.append(CozyLifeBatterySensor(coordinator, config_entry))

    if coordinator.classification.supports_plug_metering:
        plug_sensor_descriptions = [
            *(
                [
                    PlugSensorDescription(
                        dpid=PLUG_ADDED_ENERGY,
                        key="energy",
                        name="Energy",
                        device_class=SensorDeviceClass.ENERGY,
                        state_class=SensorStateClass.TOTAL_INCREASING,
                        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
                        scale=1000,
                    )
                ]
                if PLUG_ADDED_ENERGY in dpids
                else []
            ),
            *(
                [
                    PlugSensorDescription(
                        dpid=PLUG_ENERGY,
                        key="total_energy",
                        name="Total energy",
                        device_class=SensorDeviceClass.ENERGY,
                        state_class=SensorStateClass.TOTAL_INCREASING,
                        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
                        scale=1000,
                    )
                ]
                if PLUG_ENERGY in dpids
                else []
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

    if coordinator.classification.is_energy_storage:
        energy_sensor_descriptions = [
            PlugSensorDescription(
                dpid="2",
                key="battery_status",
                name="Battery status",
                device_class=None,
                state_class=None,
                native_unit_of_measurement=None,
                entity_category=EntityCategory.DIAGNOSTIC,
            ),
            PlugSensorDescription(
                dpid="3",
                key="battery_level",
                name="Battery",
                device_class=SensorDeviceClass.BATTERY,
                state_class=SensorStateClass.MEASUREMENT,
                native_unit_of_measurement=PERCENTAGE,
            ),
            PlugSensorDescription(
                dpid="4",
                key="total_output_power",
                name="Total output power",
                device_class=SensorDeviceClass.POWER,
                state_class=SensorStateClass.MEASUREMENT,
                native_unit_of_measurement=UnitOfPower.WATT,
            ),
            *[
                PlugSensorDescription(
                    dpid=str(dpid),
                    key=f"output_{dpid - 4}_power",
                    name=f"Output {dpid - 4} power",
                    device_class=SensorDeviceClass.POWER,
                    state_class=SensorStateClass.MEASUREMENT,
                    native_unit_of_measurement=UnitOfPower.WATT,
                )
                for dpid in range(5, 21)
            ],
            PlugSensorDescription(
                dpid="21",
                key="total_input_power",
                name="Total input power",
                device_class=SensorDeviceClass.POWER,
                state_class=SensorStateClass.MEASUREMENT,
                native_unit_of_measurement=UnitOfPower.WATT,
            ),
            *[
                PlugSensorDescription(
                    dpid=str(dpid),
                    key=f"input_{dpid - 21}_power",
                    name=f"Input {dpid - 21} power",
                    device_class=SensorDeviceClass.POWER,
                    state_class=SensorStateClass.MEASUREMENT,
                    native_unit_of_measurement=UnitOfPower.WATT,
                )
                for dpid in range(22, 30)
            ],
            PlugSensorDescription(
                dpid="30",
                key="remaining_usable_time",
                name="Remaining usable time",
                device_class=SensorDeviceClass.DURATION,
                state_class=SensorStateClass.MEASUREMENT,
                native_unit_of_measurement=UnitOfTime.MINUTES,
            ),
            PlugSensorDescription(
                dpid="31",
                key="remaining_charging_time",
                name="Remaining charging time",
                device_class=SensorDeviceClass.DURATION,
                state_class=SensorStateClass.MEASUREMENT,
                native_unit_of_measurement=UnitOfTime.MINUTES,
            ),
            PlugSensorDescription(
                dpid="32",
                key="battery_temperature",
                name="Battery temperature",
                device_class=SensorDeviceClass.TEMPERATURE,
                state_class=SensorStateClass.MEASUREMENT,
                native_unit_of_measurement=UnitOfTemperature.CELSIUS,
                offset=-273,
            ),
            PlugSensorDescription(
                dpid="44",
                key="fault_code",
                name="Fault code",
                device_class=None,
                state_class=None,
                native_unit_of_measurement=None,
                entity_category=EntityCategory.DIAGNOSTIC,
            ),
        ]
        entities.extend(
            CozyLifePlugSensor(coordinator, description)
            for description in energy_sensor_descriptions
            if description.dpid in dpids
        )

    if coordinator.classification.is_video_intercom and "102" in dpids:
        entities.append(
            CozyLifePlugSensor(
                coordinator,
                PlugSensorDescription(
                    dpid="102",
                    key="video_battery",
                    name="Battery",
                    device_class=SensorDeviceClass.BATTERY,
                    state_class=SensorStateClass.MEASUREMENT,
                    native_unit_of_measurement=PERCENTAGE,
                    scale=10,
                ),
            )
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
        offset: float = 0,
        entity_category: EntityCategory | None = None,
    ) -> None:
        self.dpid = dpid
        self.key = key
        self.name = name
        self.device_class = device_class
        self.state_class = state_class
        self.native_unit_of_measurement = native_unit_of_measurement
        self.scale = scale
        self.offset = offset
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

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:ip-network"

    def __init__(self, coordinator: CozyLifeCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_name = "IP address"
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

    _attr_has_entity_name = True
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
        if (
            (value := self._native_value_from_data()) is not None
            and self._is_valid_native_value(value)
        ):
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
            restored_value = float(last_state.state)
        except (TypeError, ValueError):
            return
        if self._is_valid_native_value(restored_value):
            self._last_valid_native_value = restored_value

    def _is_valid_native_value(self, value: float) -> bool:
        """Return whether a restored value is safe to present as a sensor state."""
        return True

    def _native_value_from_data(self) -> Optional[float]:
        """Return the current native value from coordinator data."""
        raise NotImplementedError

    @property
    def native_value(self) -> Optional[float]:
        value = self._native_value_from_data()
        if value is not None and self._is_valid_native_value(value):
            self._last_valid_native_value = value
            return value

        return self._last_valid_native_value


class CozyLifeTemperatureSensor(CozyLifeSensorBase):
    """Temperature sensor entity."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    # A sleeping sensor can deliver a real report whose rounded value is the
    # same as the preceding one. Keep it observable in HA history instead of
    # treating it as if no report arrived.
    _attr_force_update = True

    def __init__(self, coordinator: CozyLifeCoordinator, entry: ConfigEntry):
        super().__init__(coordinator, entry, "temperature")
        self._attr_name = "Temperature"

    def _native_value_from_data(self) -> Optional[float]:
        raw = (self.coordinator.data or {}).get(SENSOR_TEMPERATURE)
        if raw is None:
            return None
        return round(raw / 10, 1)

    def _is_valid_native_value(self, value: float) -> bool:
        """Reject non-measurement values from malformed local sensor frames."""
        return -40.0 <= value <= 80.0


class CozyLifeHumiditySensor(CozyLifeSensorBase):
    """Humidity sensor entity."""

    _attr_device_class = SensorDeviceClass.HUMIDITY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    # See CozyLifeTemperatureSensor: unchanged reported measurements are still
    # real sensor reports and should update their report timestamp.
    _attr_force_update = True

    def __init__(self, coordinator: CozyLifeCoordinator, entry: ConfigEntry):
        super().__init__(coordinator, entry, "humidity")
        self._attr_name = "Humidity"

    def _native_value_from_data(self) -> Optional[float]:
        raw = (self.coordinator.data or {}).get(SENSOR_HUMIDITY)
        if raw is None:
            return None
        return float(raw)

    def _is_valid_native_value(self, value: float) -> bool:
        """Reject non-measurement values from malformed local sensor frames."""
        return 0.0 <= value <= 100.0


class CozyLifeBatterySensor(CozyLifeSensorBase):
    """Battery sensor entity."""

    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: CozyLifeCoordinator, entry: ConfigEntry):
        super().__init__(coordinator, entry, "battery")
        self._attr_name = "Battery"

    def _native_value_from_data(self) -> Optional[float]:
        raw = (self.coordinator.data or {}).get(SENSOR_BATTERY)
        if raw is None:
            return None
        return round(raw / 10, 1)


class CozyLifePlugSensor(CoordinatorEntity[CozyLifeCoordinator], SensorEntity):
    """Metering sensor for CozyLife smart plugs."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: CozyLifeCoordinator,
        description: PlugSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self._description = description
        self._attr_name = description.name
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

        value = raw / self._description.scale + self._description.offset
        if self._description.scale == 1:
            return float(value)
        return round(value, 3)

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()
