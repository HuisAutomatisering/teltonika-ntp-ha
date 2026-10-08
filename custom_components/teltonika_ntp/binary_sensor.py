"""Binary sensors for the Teltonika NTP Server integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import TeltonikaNtpConfigEntry
from .const import SYNC_SYNCHRONIZED
from .coordinator import TeltonikaNtpData
from .entity import TeltonikaNtpEntity

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class TeltonikaNtpBinarySensorDescription(BinarySensorEntityDescription):
    """Describes a Teltonika NTP binary sensor."""

    value_fn: Callable[[TeltonikaNtpData], bool | None]


BINARY_SENSORS: tuple[TeltonikaNtpBinarySensorDescription, ...] = (
    TeltonikaNtpBinarySensorDescription(
        key="ntp_service",
        translation_key="ntp_service",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda data: data.ntp is not None,
    ),
    TeltonikaNtpBinarySensorDescription(
        key="time_problem",
        translation_key="time_problem",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda data: data.sync_state != SYNC_SYNCHRONIZED,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TeltonikaNtpConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the binary sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        TeltonikaNtpBinarySensor(coordinator, description) for description in BINARY_SENSORS
    )


class TeltonikaNtpBinarySensor(TeltonikaNtpEntity, BinarySensorEntity):
    """A Teltonika NTP binary sensor."""

    entity_description: TeltonikaNtpBinarySensorDescription

    @property
    def is_on(self) -> bool | None:
        """Return the state."""
        return self.entity_description.value_fn(self.coordinator.data)
