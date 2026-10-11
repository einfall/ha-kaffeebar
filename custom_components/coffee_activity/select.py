"""Profile selection, labelled calibration and explicit event correction."""
from homeassistant.components.select import SelectEntity
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from .const import DOMAIN, ACTIVITY_LABELS
from .entity import CoffeeEntity
from .calibration import LABELS
from .engine import EVENT_TYPES

async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(CoffeeSelect(coordinator, key) for key in ("profile", "event", "calibration_type", "correction_type"))

def event_option(event):
    stamp = dt_util.as_local(dt_util.parse_datetime(event["ended_at"]))
    return f"#{event['id']} · {stamp:%d.%m. %H:%M:%S} · {ACTIVITY_LABELS[event['classification']]}"

class CoffeeSelect(CoffeeEntity, SelectEntity):
    _attr_icon = "mdi:format-list-bulleted"

    @property
    def options(self):
        if self.key == "profile":
            return list(self.coordinator.configuration["profiles"])
        if self.key == "event":
            return [event_option(event) for event in reversed(self.coordinator.engine.events)]
        return list(LABELS if self.key == "calibration_type" else EVENT_TYPES)

    @property
    def current_option(self):
        if self.key == "profile":
            return self.coordinator.engine.profile_name
        if self.key == "event":
            event = self.coordinator.selected_event
            return event_option(event) if event else None
        return getattr(self.coordinator, self.key)

    async def async_select_option(self, option):
        if option not in self.options:
            raise HomeAssistantError("Ungültige Auswahl.")
        try:
            if self.key == "profile":
                await self.coordinator.async_choose_profile(option)
                return
            if self.key == "event":
                event = next(event for event in self.coordinator.engine.events if event_option(event) == option)
                self.coordinator.selected_event_id = event["id"]
            elif self.key == "calibration_type" and self.coordinator.engine.calibration.active:
                raise ValueError("Aufnahmetyp während der Aufnahme nicht ändern.")
            else:
                setattr(self.coordinator, self.key, option)
            self.coordinator.async_set_updated_data(self.coordinator.engine)
        except ValueError as error:
            raise HomeAssistantError(str(error)) from error
