"""Shared push-only entity subscriptions."""
from homeassistant.helpers.entity import Entity

from .const import DOMAIN


class WRBUEntity(Entity):
    _attr_should_poll = False

    def __init__(self, entry, suffix):
        self.controller = entry.runtime_data
        connection = entry.data["connection"]
        self._attr_unique_id = f"{connection['serial']}_{suffix}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, connection["serial"])},
            "name": connection["name"],
            "manufacturer": "Sungrow",
            "model": connection["model"],
            "serial_number": connection["serial"],
        }

    async def async_added_to_hass(self):
        self.controller.subscribers.append(self.async_write_ha_state)
        self.async_on_remove(
            lambda: self.controller.subscribers.remove(self.async_write_ha_state)
        )
