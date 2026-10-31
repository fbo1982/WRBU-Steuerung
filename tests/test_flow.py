from copy import deepcopy
import unittest
from unittest.mock import AsyncMock, patch
from harness import Abort, CATEGORIES, CONNECTION, FakeHass, Flow, config_flow, profiles


class FlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        Flow.configured = set()
        self.flow = config_flow.ConfigFlow()
        self.flow.hass = FakeHass()

    async def test_failed_probe_never_allows_save(self):
        result = await self.flow.async_step_finish()
        self.assertEqual(result["type"], "form")
        self.assertEqual(result["step_id"], "user")

    async def test_result_display_precedes_creation(self):
        adapter = AsyncMock()
        adapter.identify.return_value = ("SH25T", "SN001")
        adapter.probe.return_value = profiles.TestResult(
            success=True, model="SH25T", serial="SN001", before=30, after=30,
            write_attempted=True, write_acknowledged=True,
        )
        with patch.object(config_flow, "create_adapter", return_value=adapter):
            result = await self.flow.async_step_user(dict(CONNECTION))
        self.assertEqual(result["step_id"], "test_result")
        self.assertIn("30 %", result["description_placeholders"]["report"])
        result = await self.flow.async_step_test_result({})
        self.assertEqual(result["type"], "menu")
        result = await self.flow.async_step_finish()
        self.assertEqual(result["type"], "create_entry")
        self.assertTrue(result["data"]["test_result"]["success"])

    async def test_duplicate_wr_aborts_before_test_write(self):
        Flow.configured = {"sungrow_sh:SN001"}
        adapter = AsyncMock()
        adapter.identify.return_value = ("SH25T", "SN001")
        with patch.object(config_flow, "create_adapter", return_value=adapter):
            with self.assertRaises(Abort):
                await self.flow.async_step_user(dict(CONNECTION))
        adapter.probe.assert_not_awaited()

    async def test_native_ihm_result_explains_failure(self):
        result = await self.flow.async_step_user({**CONNECTION, "profile": "sungrow_ihm_native"})
        self.assertEqual(result["step_id"], "test_result")
        self.assertIn("FEHLGESCHLAGEN", result["description_placeholders"]["report"])
        self.assertFalse(self.flow._test.write_attempted)
        result = await self.flow.async_step_finish()
        self.assertNotEqual(result["type"], "create_entry")

    async def test_invalid_ip_and_warning(self):
        self.flow.hass.registry.async_get = lambda entity: None
        result = await self.flow.async_step_user({**CONNECTION, "host": "not-an-ip"})
        self.assertEqual(result["errors"], {"host": "invalid_ip", "warning_entity": "invalid_warning"})

    async def test_occupied_months_absent_from_new_category_selector(self):
        self.flow._categories = deepcopy(CATEGORIES[:1])
        result = await self.flow.async_step_add()
        schema = result["data_schema"].schema
        month_selector = next(v for k, v in schema.items() if str(k) == "months")
        values = {option["value"] for option in month_selector.config["options"]}
        self.assertNotIn("1", values)
        self.assertNotIn("12", values)
        self.assertEqual(len(values), 10)

    async def test_tampered_month_overlap_is_rejected_server_side(self):
        self.flow._categories = deepcopy(CATEGORIES[:1])
        self.flow._editing = None
        result = await self.flow.async_step_category({
            "name": "Other", "months": ["12"], "normal_soc": 25, "warning_soc": 60,
        })
        self.assertEqual(result["errors"], {"base": "invalid_category"})
        self.assertEqual(len(self.flow._categories), 1)

    async def test_options_are_transactional(self):
        entry = type("Entry", (), {"options": {}, "data": {"categories": deepcopy(CATEGORIES)}})()
        flow = config_flow.OptionsFlow(entry)
        await flow.async_step_remove({"category": "0"})
        self.assertEqual(len(entry.data["categories"]), 2)
        result = await flow.async_step_finish()
        self.assertEqual(len(result["data"]["categories"]), 1)
