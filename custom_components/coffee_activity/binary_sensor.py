"""Provisional live preparation indicators."""
from homeassistant.components.binary_sensor import BinarySensorEntity
from .const import DOMAIN
from .entity import CoffeeEntity

async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(CoffeeBinarySensor(coordinator, key) for key in ("is_espresso", "is_milchschaum"))

class CoffeeBinarySensor(CoffeeEntity, BinarySensorEntity):
    def __init__(self, coordinator, key):
        super().__init__(coordinator, key)
        self._attr_icon = "mdi:coffee" if key == "is_espresso" else "mdi:cup"

    @property
    def available(self):
        return super().available and self.coordinator.engine.available

    @property
    def is_on(self):
        return getattr(self.coordinator.engine, self.key)

    @property
    def extra_state_attributes(self):
        return {"estimated": True, "power_entity": self.coordinator.power_entity}
