"""Base entity for the Teltonika NTP Server integration."""

from __future__ import annotations

from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import TeltonikaNtpCoordinator


class TeltonikaNtpEntity(CoordinatorEntity[TeltonikaNtpCoordinator]):
    """Entity bound to one Teltonika device."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: TeltonikaNtpCoordinator, description: EntityDescription
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_device_info = coordinator.device_info
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.unique_id or entry.entry_id}_{description.key}"
