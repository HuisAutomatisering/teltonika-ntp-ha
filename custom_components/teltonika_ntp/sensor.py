"""Sensors for the Teltonika NTP Server integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from . import TeltonikaNtpConfigEntry
from .const import SYNC_STATES
from .coordinator import TeltonikaNtpData
from .entity import TeltonikaNtpEntity

PARALLEL_UPDATES = 0


def _ms(seconds: float | None) -> float | None:
    """Convert seconds to milliseconds."""
    return None if seconds is None else seconds * 1000


def _last_sync(data: TeltonikaNtpData) -> datetime | None:
    """Return when the server clock was last set by its reference (GPS)."""
    if data.ntp is None or data.ntp.is_kiss_of_death or data.ntp.reference_time is None:
        return None
    return datetime.fromtimestamp(data.ntp.reference_time, tz=UTC)


@dataclass(frozen=True, kw_only=True)
class TeltonikaNtpSensorDescription(SensorEntityDescription):
    """Describes a Teltonika NTP sensor."""

    value_fn: Callable[[TeltonikaNtpData], StateType | datetime]


SENSORS: tuple[TeltonikaNtpSensorDescription, ...] = (
    TeltonikaNtpSensorDescription(
        key="synchronization",
        translation_key="synchronization",
        device_class=SensorDeviceClass.ENUM,
        options=SYNC_STATES,
        value_fn=lambda data: data.sync_state,
    ),
    TeltonikaNtpSensorDescription(
        key="stratum",
        translation_key="stratum",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: (
            data.ntp.stratum if data.ntp and not data.ntp.is_kiss_of_death else None
        ),
    ),
    TeltonikaNtpSensorDescription(
        key="reference",
        translation_key="reference",
        value_fn=lambda data: (
            data.ntp.reference_id or None if data.ntp and not data.ntp.is_kiss_of_death else None
        ),
    ),
    TeltonikaNtpSensorDescription(
        key="last_gps_sync",
        translation_key="last_gps_sync",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_last_sync,
    ),
    TeltonikaNtpSensorDescription(
        key="max_error",
        translation_key="max_error",
        native_unit_of_measurement=UnitOfTime.MILLISECONDS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda data: _ms(data.ntp.max_error) if data.ntp else None,
    ),
    TeltonikaNtpSensorDescription(
        key="offset",
        translation_key="offset",
        native_unit_of_measurement=UnitOfTime.MILLISECONDS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda data: _ms(data.ntp.offset) if data.ntp else None,
    ),
    TeltonikaNtpSensorDescription(
        key="round_trip_delay",
        translation_key="round_trip_delay",
        native_unit_of_measurement=UnitOfTime.MILLISECONDS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: _ms(data.ntp.delay) if data.ntp else None,
    ),
    TeltonikaNtpSensorDescription(
        key="root_dispersion",
        translation_key="root_dispersion",
        native_unit_of_measurement=UnitOfTime.MILLISECONDS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: (
            _ms(data.ntp.root_dispersion) if data.ntp and not data.ntp.is_kiss_of_death else None
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TeltonikaNtpConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensors."""
    coordinator = entry.runtime_data
    async_add_entities(TeltonikaNtpSensor(coordinator, description) for description in SENSORS)


class TeltonikaNtpSensor(TeltonikaNtpEntity, SensorEntity):
    """A Teltonika NTP sensor."""

    entity_description: TeltonikaNtpSensorDescription

    @property
    def native_value(self) -> StateType | datetime:
        """Return the sensor value."""
        return self.entity_description.value_fn(self.coordinator.data)
