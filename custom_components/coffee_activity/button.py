"""Buttons only affect this detector's recordings, profiles and counts."""
from homeassistant.components.button import ButtonEntity
from homeassistant.exceptions import HomeAssistantError
from .const import DOMAIN
from .entity import CoffeeEntity

async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(CoffeeButton(coordinator, key) for key in ("calibration_start", "calibration_stop", "calibration_cancel", "calibration_profile", "correct_event", "calibration_guided"))

class CoffeeButton(CoffeeEntity, ButtonEntity):
    _attr_icon = "mdi:gesture-tap-button"

    @property
    def available(self):
        if not super().available:
            return False
        engine = self.coordinator.engine
        if self.key in ("calibration_start", "calibration_guided"):
            idle = engine.available and engine.power is not None and engine.power < engine.options.off_w and not self.coordinator.busy
            fresh = self.coordinator.measurement_quality["state"] not in ("missing", "stale")
            return idle and fresh and (self.key != "calibration_guided" or not self.coordinator.calibration_guide["complete"])
        if self.key in ("calibration_stop", "calibration_cancel"):
            return engine.calibration.active is not None
        if self.key == "calibration_profile":
            return not self.coordinator.busy and self.coordinator.calibration_proposal["ready"]
        return not self.coordinator.busy and self.coordinator.selected_event is not None

    async def async_press(self):
        try:
            await self.coordinator.async_control(self.key)
        except ValueError as error:
            raise HomeAssistantError(str(error)) from error
