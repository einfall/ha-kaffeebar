"""Entities grouped under one selected coffee machine."""
from __future__ import annotations

from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, LEGACY_ENTITIES, NEW_MIGRATED_ENTITIES
from .coordinator import CoffeeCoordinator

class CoffeeEntity(CoordinatorEntity[CoffeeCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: CoffeeCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self.key = key
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, coordinator.entry.entry_id)}, name=coordinator.entry.title, manufacturer="Coffee Activity", model="Power-based activity detector")
        # Keep known IDs only for the explicit migration. New machines use normal HA naming.
        if coordinator.entry.data.get("migrate_legacy") and key in LEGACY_ENTITIES:
            self.entity_id = LEGACY_ENTITIES[key][0]
        elif coordinator.entry.data.get("migrate_legacy") and key in NEW_MIGRATED_ENTITIES:
            self.entity_id = NEW_MIGRATED_ENTITIES[key]
