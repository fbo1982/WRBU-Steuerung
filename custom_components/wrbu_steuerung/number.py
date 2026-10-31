"""Backup SOC number; values are updated after verified event-driven access."""
from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.exceptions import HomeAssistantError

from .entity import WRBUEntity
from .modbus import ModbusError


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([BackupNumber(entry)])


class BackupNumber(WRBUEntity, NumberEntity):
    _attr_name = "Battery reserved SOC for backup"
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "%"
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:battery-lock"

    def __init__(self, entry):
        super().__init__(entry, "backup_soc")

    @property
    def native_value(self):
        return self.controller.current

    @property
    def available(self):
        return self.controller.current is not None and self.controller.status != "fehler"

    @property
    def extra_state_attributes(self):
        return {"last_verified": self.controller.last_verified,
                "update_policy": "event_driven_no_polling"}

    async def async_set_native_value(self, value):
        if not float(value).is_integer():
            raise HomeAssistantError("Backup-SOC muss ganzzahlig sein")
        try:
            await self.controller.manual_set(int(value))
        except (ModbusError, ValueError) as err:
            raise HomeAssistantError(str(err)) from err
