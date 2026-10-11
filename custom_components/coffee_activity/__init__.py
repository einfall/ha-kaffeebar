"""Configure one persistent coffee detector per existing power sensor."""
from __future__ import annotations

import logging
import voluptuous as vol

from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv, entity_registry as er, restore_state
from homeassistant.util import dt as dt_util

from .const import DEFAULT_NAME, DOMAIN, LEGACY_ENTITIES, PLATFORMS, NEW_MIGRATED_ENTITIES
from .coordinator import CoffeeCoordinator
from .profiles import normalise_options, refine_rocket_options
from .maintenance import async_read_request, requested_options
from .frontend import async_setup_frontend

_LOGGER = logging.getLogger(__name__)
CONFIG_SCHEMA = vol.Schema({vol.Optional(DOMAIN): vol.Schema({vol.Required("power_entity"): cv.entity_id,
    vol.Optional("name", default=DEFAULT_NAME): cv.string, vol.Optional("migrate_legacy", default=False): cv.boolean,
    vol.Optional("migration_snapshot", default={}): dict, vol.Optional("options", default={}): dict})}, extra=vol.ALLOW_EXTRA)

async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    hass.data.setdefault(DOMAIN, {})
    await async_setup_frontend(hass)
    hass.data[DOMAIN]["legacy_template_enabled"] = any(
        s.get("unique_id") == "kaffee_zubereitungen_leistung"
        for block in (config.get("template") or []) if isinstance(block, dict)
        for s in block.get("sensor", []) if isinstance(s, dict)
    )
    if DOMAIN in config:
        hass.async_create_task(hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_IMPORT}, data=config[DOMAIN]))
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    request = await async_read_request(hass, entry.entry_id)
    options = requested_options(refine_rocket_options(entry.options, entry.data), request, entry.data["power_entity"])
    if dict(entry.options) != options:
        # Framework-managed migration, before registering our update listener.
        hass.config_entries.async_update_entry(entry, options=options)
    coordinator = CoffeeCoordinator(hass, entry)
    coordinator.maintenance_request = request
    registry = er.async_get(hass)
    snapshot = entry.data.get("migration_snapshot")
    legacy_properties = {}
    if entry.data.get("migrate_legacy"):
        for key, entity_id in NEW_MIGRATED_ENTITIES.items():
            current = registry.async_get(entity_id)
            if current and (current.platform != DOMAIN or current.config_entry_id != entry.entry_id or current.unique_id != f"{entry.entry_id}_{key}"):
                raise ConfigEntryNotReady(f"New coffee entity {entity_id} belongs to another detector")
        if hass.data[DOMAIN].get("legacy_template_enabled"):
            raise ConfigEntryNotReady("Remove the previous coffee template include before migration")
        # Read latest shutdown state when available, so counters changed after installation are preserved.
        restored = restore_state.async_get(hass).last_states.get("sensor.kaffee_zubereitungen")
        if restored and restored.state.state not in ("unknown", "unavailable"):
            try:
                restored_total = int(float(restored.state.state))
                fallback_total = int(float((snapshot or {}).get("state", 0)))
            except (TypeError, ValueError, OverflowError):
                restored_total, fallback_total = -1, 0
            captured_updated = dt_util.parse_datetime((snapshot or {}).get("last_updated", ""))
            if restored_total >= fallback_total and (captured_updated is None or restored.state.last_updated >= captured_updated):
                snapshot = {"state": restored.state.state, "attributes": dict(restored.state.attributes)}
        for key, (entity_id, unique_id) in LEGACY_ENTITIES.items():
            current = registry.async_get(entity_id)
            if current and current.platform not in ("template", DOMAIN):
                raise ConfigEntryNotReady(f"Existing entity {entity_id} belongs to another integration")
            if current and current.platform == "template" and current.unique_id != unique_id:
                raise ConfigEntryNotReady(f"Unexpected legacy entity ownership for {entity_id}")
            if current and current.platform == DOMAIN and (current.config_entry_id != entry.entry_id or current.unique_id != f"{entry.entry_id}_{key}"):
                raise ConfigEntryNotReady(f"Existing coffee entity {entity_id} belongs to another detector")
            if current and current.platform == "template":
                legacy_properties[entity_id] = {name: getattr(current, name) for name in ("name", "icon", "area_id", "labels", "disabled_by", "hidden_by")}
    await coordinator.async_initialize(snapshot)
    if entry.data.get("migrate_legacy"):
        for entity_id, unique_id in LEGACY_ENTITIES.values():
            current = registry.async_get(entity_id)
            if current and current.platform == "template" and current.unique_id == unique_id:
                registry.async_remove(entity_id)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        await coordinator.async_stop()
        hass.data[DOMAIN].pop(entry.entry_id, None)
        raise
    for entity_id, properties in legacy_properties.items():
        current = registry.async_get(entity_id)
        if current and current.platform == DOMAIN and current.config_entry_id == entry.entry_id:
            registry.async_update_entity(entity_id, **properties)
    coordinator.async_start()
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True

async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await coordinator.async_stop()
        hass.data[DOMAIN].pop(entry.entry_id, None)
        return True
    return False
