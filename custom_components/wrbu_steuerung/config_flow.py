"""WRBU setup: identity, read/write/read proof, result, categories, save."""
from copy import deepcopy
import ipaddress

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er, selector

from .const import DOMAIN, MONTHS, PROFILE_NAMES
from .modbus import ModbusError
from .policy import available_months, validate_categories
from .profiles import TestResult, create_adapter


def selection(options, *, multiple=False):
    return selector.SelectSelector(selector.SelectSelectorConfig(
        options=options, multiple=multiple, mode="dropdown",
    ))


class CategoryWizard:
    """Shared transactional editor for setup and options."""
    _categories: list[dict]
    _editing: int | None = None

    def _overview(self):
        lines = [f"{c['name']}: {', '.join(MONTHS[m-1] for m in c['months'])}; "
                 f"normal {c['normal_soc']} %, DWD {c['warning_soc']} %"
                 for c in self._categories]
        free = available_months(self._categories)
        lines.append("Ohne Regel: " + (", ".join(MONTHS[m-1] for m in free) or "keine"))
        return "\n\n".join(lines)

    async def async_step_categories(self, user_input=None):
        menu = []
        if available_months(self._categories):
            menu.append("add")
        if self._categories:
            menu.extend(["edit", "remove", "finish"])
        return self.async_show_menu(
            step_id="categories", menu_options=menu,
            description_placeholders={"overview": self._overview()},
        )

    async def async_step_add(self, user_input=None):
        self._editing = None
        return await self.async_step_category()

    async def _choose(self, step, user_input):
        if user_input is not None:
            index = int(user_input["category"])
            if not 0 <= index < len(self._categories):
                return await self.async_step_categories()
            if step == "remove":
                self._categories.pop(index)
                return await self.async_step_categories()
            self._editing = index
            return await self.async_step_category()
        return self.async_show_form(
            step_id=step, data_schema=vol.Schema({
                vol.Required("category"): selection([
                    {"value": str(i), "label": c["name"]}
                    for i, c in enumerate(self._categories)
                ]),
            }),
        )

    async def async_step_edit(self, user_input=None):
        return await self._choose("edit", user_input)

    async def async_step_remove(self, user_input=None):
        return await self._choose("remove", user_input)

    async def async_step_category(self, user_input=None):
        errors = {}
        details = ""
        defaults = (self._categories[self._editing] if self._editing is not None
                    else {"name": "", "months": [], "normal_soc": 25, "warning_soc": 60})
        if user_input is not None:
            try:
                candidate = {
                    "name": user_input["name"],
                    "months": [int(m) for m in user_input["months"]],
                    "normal_soc": user_input["normal_soc"],
                    "warning_soc": user_input["warning_soc"],
                }
                choices = deepcopy(self._categories)
                if self._editing is None:
                    choices.append(candidate)
                else:
                    choices[self._editing] = candidate
                self._categories = validate_categories(choices)
                return await self.async_step_categories()
            except (ValueError, TypeError, KeyError) as err:
                errors["base"] = "invalid_category"
                details = str(err)
        months = available_months(self._categories, self._editing)
        if not months:
            return await self.async_step_categories()
        schema = vol.Schema({
            vol.Required("name", default=defaults["name"]): str,
            vol.Required("months", default=[str(m) for m in defaults["months"]]): selection(
                [{"value": str(m), "label": MONTHS[m-1]} for m in months], multiple=True,
            ),
            vol.Required("normal_soc", default=defaults["normal_soc"]): vol.All(
                vol.Coerce(int), vol.Range(min=0, max=100),
            ),
            vol.Required("warning_soc", default=defaults["warning_soc"]): vol.All(
                vol.Coerce(int), vol.Range(min=0, max=100),
            ),
        })
        return self.async_show_form(
            step_id="category", data_schema=schema, errors=errors,
            description_placeholders={"details": details},
        )


class ConfigFlow(CategoryWizard, config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self):
        self._connection = {}
        self._test = TestResult()
        self._categories = [
            {"name": "Winter", "months": [1, 12], "normal_soc": 40, "warning_soc": 60},
            {"name": "Übergangszeit", "months": [3, 11], "normal_soc": 30, "warning_soc": 60},
            {"name": "Übrige Monate", "months": [2, 4, 5, 6, 7, 8, 9, 10], "normal_soc": 25, "warning_soc": 60},
        ]

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                user_input["host"] = str(ipaddress.ip_address(user_input["host"].strip()))
            except ValueError:
                errors["host"] = "invalid_ip"
            warning = er.async_get(self.hass).async_get(user_input["warning_entity"])
            if (warning is None or warning.platform != "dwd_weather_warnings"
                    or not warning.unique_id.endswith("-current_warning_level")):
                errors["warning_entity"] = "invalid_warning"
            if not errors:
                self._connection = dict(user_input)
                return await self._run_test()
        default = user_input or self._connection
        schema = vol.Schema({
            vol.Required("name", default=default.get("name", "WRBU Steuerung")): str,
            vol.Required("profile", default=default.get("profile", "sungrow_sh")): selection(
                [{"value": k, "label": v} for k, v in PROFILE_NAMES.items()],
            ),
            vol.Required("host", default=default.get("host", "")): str,
            vol.Required("port", default=default.get("port", 502)): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=65535),
            ),
            vol.Required("unit", default=default.get("unit", 1)): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=247),
            ),
            vol.Required("warning_entity", **(
                {"default": default["warning_entity"]} if "warning_entity" in default else {}
            )): selector.EntitySelector(selector.EntitySelectorConfig(
                domain="sensor", integration="dwd_weather_warnings",
            )),
        })
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def _run_test(self):
        self._test = TestResult()
        try:
            adapter = create_adapter(self._connection)
            model, serial = await adapter.identify()
            await self.async_set_unique_id("sungrow_sh:" + serial)
            # Abort before the test write if any profile already owns this WR.
            self._abort_if_unique_id_configured()
            self._test = await create_adapter(self._connection, serial).probe()
            if self._test.success:
                self._connection.update(model=model, serial=serial)
        except ModbusError as err:
            self._test.error = str(err)
        return await self.async_step_test_result()

    def _report(self):
        result = self._test
        show = lambda value: "–" if value is None else str(value)
        return (
            f"Modell: {result.model or '–'}; Seriennummer: {result.serial or '–'}\n\n"
            f"Backup-SOC gelesen: {show(result.before)} %\n\n"
            f"Schreiben versucht: {'ja' if result.write_attempted else 'nein'}; "
            f"bestätigt: {'ja' if result.write_acknowledged else 'nein'}\n\n"
            f"Backup-SOC zurückgelesen: {show(result.after)} %\n\n"
            f"Ergebnis: {'ERFOLGREICH' if result.success else 'FEHLGESCHLAGEN'}\n\n"
            f"{result.error}"
        )

    async def async_step_test_result(self, user_input=None):
        if user_input is not None:
            if self._test.success:
                return await self.async_step_categories()
            return await self.async_step_user()
        return self.async_show_form(
            step_id="test_result", data_schema=vol.Schema({}),
            description_placeholders={"report": self._report()},
        )

    async def async_step_finish(self, user_input=None):
        if not self._test.success:
            return await self.async_step_user()
        categories = validate_categories(self._categories)
        if not categories:
            return await self.async_step_categories()
        return self.async_create_entry(
            title=self._connection["name"], data={
                "connection": self._connection,
                "categories": categories,
                "test_result": self._test.as_dict(),
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return OptionsFlow(config_entry)


class OptionsFlow(CategoryWizard, config_entries.OptionsFlow):
    def __init__(self, entry):
        self._categories = deepcopy(entry.options.get("categories", entry.data["categories"]))

    async def async_step_init(self, user_input=None):
        return await self.async_step_categories()

    async def async_step_finish(self, user_input=None):
        categories = validate_categories(self._categories)
        if not categories:
            return await self.async_step_categories()
        return self.async_create_entry(title="", data={"categories": categories})
