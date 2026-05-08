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

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class BinarySensorDescription:
    dpid: str
    key: str
    name: str
    device_class: BinarySensorDeviceClass


def _binary_sensor_descriptions(
    model_name: str | None,
    dpids: list[str],
) -> list[BinarySensorDescription]:
    """Return conservative binary sensor mappings for known CozyLife sensors."""
    lower_name = (model_name or "").lower()
    dpid_set = set(dpids)
    descriptions: list[BinarySensorDescription] = []

    if ("door" in lower_name or "magnet" in lower_name) and "7" in dpid_set:
        descriptions.append(
            BinarySensorDescription(
                dpid="7",
                key="contact",
                name="Contact",
                device_class=BinarySensorDeviceClass.DOOR,
            )
        )

    if "motion" in lower_name and "6" in dpid_set:
        descriptions.append(
            BinarySensorDescription(
                dpid="6",
                key="motion",
                name="Motion",
                device_class=BinarySensorDeviceClass.MOTION,
            )
        )

    if "water" in lower_name and "10" in dpid_set:
        descriptions.append(
            BinarySensorDescription(
                dpid="10",
                key="moisture",
                name="Moisture",
                device_class=BinarySensorDeviceClass.MOISTURE,
            )
        )

    if "smoke" in lower_name and "11" in dpid_set:
        descriptions.append(
            BinarySensorDescription(
                dpid="11",
                key="smoke",
                name="Smoke",
                device_class=BinarySensorDeviceClass.SMOKE,
            )
        )

    if ("proximity" in lower_name or "radar" in lower_name) and "101" in dpid_set:
        descriptions.append(
            BinarySensorDescription(
                dpid="101",
                key="occupancy",
                name="Occupancy",
                device_class=BinarySensorDeviceClass.OCCUPANCY,
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

    if not coordinator.device.dpid:
        return

    descriptions = _binary_sensor_descriptions(
        coordinator.classification.model_name or coordinator.device.device_model_name,
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

    def __init__(
        self,
        coordinator: CozyLifeCoordinator,
        description: BinarySensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self._description = description
        self._attr_name = f"{coordinator.device.device_model_name} {description.name}"
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
