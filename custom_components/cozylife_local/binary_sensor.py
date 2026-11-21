import logging
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import CozyLifeCoordinator
from .discovery import LOCAL_BINARY_SENSOR_PIDS

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class BinarySensorDescription:
    dpid: str
    key: str
    name: str
    device_class: BinarySensorDeviceClass


_WIFI_CONTACT_SENSOR_PIDS = LOCAL_BINARY_SENSOR_PIDS - {"y9DaDw"}
_WIFI_WATER_SENSOR_PIDS = frozenset({"y9DaDw"})
_WIFI_MOTION_SENSOR_PIDS = frozenset({"t8nvdb", "JTFMlo"})
_WIFI_CONTACT_AND_MOTION_SENSOR_PIDS = frozenset({"t8nvdb"})


def _binary_sensor_descriptions(
    pid: str | None,
    dpids: list[str],
) -> list[BinarySensorDescription]:
    """Return binary mappings for catalogued local Wi-Fi sensor profiles."""
    dpid_set = set(dpids)
    descriptions: list[BinarySensorDescription] = []

    if pid in _WIFI_CONTACT_SENSOR_PIDS and "7" in dpid_set:
        descriptions.append(
            BinarySensorDescription(
                dpid="7",
                key="contact",
                name="Contact",
                device_class=BinarySensorDeviceClass.DOOR,
            )
        )

    if pid in _WIFI_WATER_SENSOR_PIDS and "10" in dpid_set:
        descriptions.append(
            BinarySensorDescription(
                dpid="10",
                key="moisture",
                name="Moisture",
                device_class=BinarySensorDeviceClass.MOISTURE,
            )
        )

    if pid in _WIFI_MOTION_SENSOR_PIDS and "6" in dpid_set:
        descriptions.append(
            BinarySensorDescription(
                dpid="6",
                key="motion",
                name="Motion",
                device_class=BinarySensorDeviceClass.MOTION,
            )
        )

    if pid in _WIFI_CONTACT_AND_MOTION_SENSOR_PIDS:
        if "7" in dpid_set:
            descriptions.append(
                BinarySensorDescription(
                    dpid="7",
                    key="contact",
                    name="Contact",
                    device_class=BinarySensorDeviceClass.DOOR,
                )
            )

    # CozyLife's public video project documents these local intercom/doorbell
    # reports. DPID 101 uses 1 for an active call and 2 for inactive, unlike
    # the usual 0/1 binary convention.
    if "101" in dpid_set and pid in {"wmX4pg", "TtzhrF", "LS5ZYq", "njzaOQ", "PDXXfS"}:
        descriptions.append(
            BinarySensorDescription(
                dpid="101",
                key="call_active",
                name="Call active",
                device_class=BinarySensorDeviceClass.OCCUPANCY,
            )
        )
    if "103" in dpid_set and pid in {"oPGsQu", "YHoxOX", "bZOyOH", "I56w6F", "iaKiBX"}:
        descriptions.append(
            BinarySensorDescription(
                dpid="103",
                key="low_battery",
                name="Low battery",
                device_class=BinarySensorDeviceClass.BATTERY,
            )
        )
    if "106" in dpid_set and pid in {"oPGsQu", "YHoxOX", "bZOyOH", "I56w6F", "iaKiBX"}:
        descriptions.append(
            BinarySensorDescription(
                dpid="106",
                key="motion",
                name="Motion",
                device_class=BinarySensorDeviceClass.MOTION,
            )
        )
    return descriptions


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities,
):
    """Set up CozyLife binary sensor platform."""
    coordinator: CozyLifeCoordinator = hass.data[DOMAIN][config_entry.entry_id]

    if (
        not coordinator.classification.is_local_transport
        or not coordinator.device.dpid
    ):
        return

    descriptions = _binary_sensor_descriptions(
        coordinator.device.pid,
        coordinator.device.dpid,
    )
    entities = [
        CozyLifeBinarySensor(coordinator, description)
        for description in descriptions
        if description.dpid in coordinator.device.dpid
    ]

    if not entities:
        _LOGGER.debug(
            "Device %s has no supported binary sensor DPIDs, skipping binary sensor setup.",
            coordinator.device.ip_address,
        )
        return

    async_add_entities(entities)


class CozyLifeBinarySensor(CoordinatorEntity[CozyLifeCoordinator], BinarySensorEntity):
    """Binary sensor for CozyLife sensor devices."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: CozyLifeCoordinator,
        description: BinarySensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self._description = description
        self._attr_name = description.name
        self._attr_unique_id = f"{coordinator.device.device_id}_{description.key}"
        self._attr_device_class = description.device_class

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.device.device_id)},
            name=self.coordinator.device.device_model_name,
            manufacturer="CozyLife",
            model=self.coordinator.device.pid,
        )

    @property
    def is_on(self) -> bool | None:
        raw = self.coordinator.data.get(self._description.dpid)
        if raw is None:
            return None
        if self._description.dpid in {"101", "103", "106"}:
            # Video event reports use 1 for active and 2 for clear.
            if raw in (1, "1"):
                return True
            if raw in (2, "2", 0, "0"):
                return False
            return None
        if self._description.dpid in {"6", "7"}:
            if raw in (1, "1"):
                return True
            if raw in (0, "0"):
                return False
            return None
        return _coerce_binary_state(raw)

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()


def _coerce_binary_state(raw: Any) -> bool | None:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return raw != 0
    if isinstance(raw, str):
        value = raw.strip().lower()
        if value in {"1", "true", "on", "open", "detected", "alarm"}:
            return True
        if value in {"0", "false", "off", "closed", "clear", "normal"}:
            return False
    return None
