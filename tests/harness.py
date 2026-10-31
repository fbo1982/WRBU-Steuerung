"""Isolated HA doubles; production modules and real voluptuous run unchanged."""
import asyncio
import importlib
from pathlib import Path
import sys
import types
from unittest.mock import patch


def mod(name, **attrs):
    result = types.ModuleType(name)
    result.__dict__.update(attrs)
    return result


class Abort(Exception):
    pass


class Flow:
    configured = set()

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__()

    async def async_set_unique_id(self, unique_id):
        self.unique_id = unique_id

    def _abort_if_unique_id_configured(self):
        if self.unique_id in self.configured:
            raise Abort("already_configured")

    def async_show_form(self, **kwargs):
        return {"type": "form", **kwargs}

    def async_show_menu(self, **kwargs):
        return {"type": "menu", **kwargs}

    def async_create_entry(self, **kwargs):
        return {"type": "create_entry", **kwargs}


class Selector:
    def __init__(self, config):
        self.config = config

    def __call__(self, value):
        return value


def schedule(hass, seconds, action):
    timer = {"seconds": seconds, "action": action, "cancelled": False}
    hass.timers.append(timer)
    return lambda: timer.update(cancelled=True)


CLOCK = types.SimpleNamespace(month=10)
dt = mod("homeassistant.util.dt", now=lambda: CLOCK)
entries = mod("homeassistant.config_entries", ConfigFlow=Flow, OptionsFlow=Flow)
selectors = mod("homeassistant.helpers.selector",
    SelectSelector=Selector, EntitySelector=Selector,
    SelectSelectorConfig=lambda **kw: kw, EntitySelectorConfig=lambda **kw: kw)
registry = mod("homeassistant.helpers.entity_registry", async_get=lambda hass: hass.registry)
events = mod("homeassistant.helpers.event", async_call_later=schedule,
    async_track_state_change_event=lambda *a, **kw: lambda: None,
    async_track_time_change=lambda *a, **kw: lambda: None)
package = mod("_wrbu")
package.__path__ = [str(Path(__file__).resolve().parents[1] / "custom_components" / "wrbu_steuerung")]
stubs = {
    "_wrbu": package,
    "homeassistant": mod("homeassistant", config_entries=entries),
    "homeassistant.core": mod("homeassistant.core", callback=lambda f: f),
    "homeassistant.config_entries": entries,
    "homeassistant.helpers": mod("homeassistant.helpers", entity_registry=registry, selector=selectors),
    "homeassistant.helpers.event": events,
    "homeassistant.helpers.entity_registry": registry,
    "homeassistant.helpers.selector": selectors,
    "homeassistant.helpers.start": mod("homeassistant.helpers.start",
        async_at_started=lambda hass, action: (action(hass), lambda: None)[1]),
    "homeassistant.util": mod("homeassistant.util", dt=dt),
    "homeassistant.util.dt": dt,
}
with patch.dict(sys.modules, stubs):
    policy = importlib.import_module("_wrbu.policy")
    modbus = importlib.import_module("_wrbu.modbus")
    profiles = importlib.import_module("_wrbu.profiles")
    controller = importlib.import_module("_wrbu.controller")
    config_flow = importlib.import_module("_wrbu.config_flow")


class FakeHass:
    def __init__(self, level="0"):
        self.level = level
        self.timers = []
        self.states = types.SimpleNamespace(get=lambda entity: types.SimpleNamespace(state=self.level))
        self.registry = types.SimpleNamespace(async_get=lambda entity: types.SimpleNamespace(
            platform="dwd_weather_warnings", unique_id="123-current_warning_level"))

    def async_create_task(self, coro):
        return asyncio.create_task(coro)


CATEGORIES = [
    {"name": "Winter", "months": [1, 12], "normal_soc": 40, "warning_soc": 70},
    {"name": "Rest", "months": list(range(2, 12)), "normal_soc": 25, "warning_soc": 60},
]
CONNECTION = {"name": "WR", "profile": "sungrow_sh", "host": "127.0.0.1",
              "port": 502, "unit": 1, "warning_entity": "sensor.dwd_current_warning_level"}
