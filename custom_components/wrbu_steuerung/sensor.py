"""Visible connection result and current reserve policy; no polling."""
from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.entity import EntityCategory

from .entity import WRBUEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([CommunicationSensor(entry)])


class CommunicationSensor(WRBUEntity, SensorEntity):
    _attr_has_entity_name = True
    _attr_name = "Kommunikationsstatus"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:lan-check"

    def __init__(self, entry):
        super().__init__(entry, "communication")
        self._test = entry.data["test_result"]

    @property
    def native_value(self):
        return self.controller.status

    @property
    def extra_state_attributes(self):
        return {
            "active_category": self.controller.category,
            "desired_soc": self.controller.desired,
            "last_verified": self.controller.last_verified,
            "error": self.controller.error,
            "setup_test": self._test,
            "categories": self.controller.categories,
        }
