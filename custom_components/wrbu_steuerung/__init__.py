"""WRBU Steuerung – extensible backup reserve management."""
from homeassistant.const import Platform

from .controller import Controller
from .policy import validate_categories
from .profiles import create_adapter

PLATFORMS = [Platform.NUMBER, Platform.SENSOR]


async def async_setup_entry(hass, entry):
    connection = entry.data["connection"]
    categories = validate_categories(entry.options.get("categories", entry.data["categories"]))
    adapter = create_adapter(connection, connection["serial"])
    entry.runtime_data = Controller(hass, connection, categories, adapter)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_options_changed))
    entry.runtime_data.start()
    return True


async def _options_changed(hass, entry):
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass, entry):
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.stop()
    return unloaded
