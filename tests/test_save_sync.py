"""Category saves reuse the saved endpoint and reconcile the current real SOC."""
import asyncio
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from aiohttp.test_utils import TestClient, TestServer

from harness import CONNECTION, CATEGORIES, modbus
from test_app import FakeAdapter, FakeBridge
from wrbu.main import create_app
from wrbu.manager import Manager, Store


class SaveSyncTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.adapter = FakeAdapter()
        self.manager = Manager(Store(Path(self.temp.name)/"devices.json"), adapter_factory=lambda *a: self.adapter)
        self.weather = FakeBridge()
        self.manager.weather = self.weather
        self.manager.bridge = self.weather
        self.manager.now = lambda: datetime(2026, 10, 31, 22, tzinfo=self.weather.zone)
        probe = await self.manager.probe({"connection": CONNECTION})
        saved = await self.manager.save({"connection": CONNECTION, "categories": CATEGORIES, "proof": probe["proof"]})
        self.identity = saved["id"]
        self.connection = saved["connection"]
        self.adapter.writes.clear()
        self.adapter.applies.clear()
        self.client = TestClient(TestServer(create_app(self.manager, self.weather, ingress_only=False)))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        await self.manager.stop()
        self.temp.cleanup()

    def payload(self):
        return {"id": self.identity, "connection": deepcopy(self.connection), "categories": deepcopy(CATEGORIES)}

    async def test_category_only_save_without_fresh_probe_writes_current_target(self):
        data = self.payload()
        data["categories"][1]["normal_soc"] = 35
        result = await self.manager.save(data)
        self.assertEqual(self.adapter.writes, [35])
        self.assertEqual(result["save_result"]["desired"], 35)
        self.assertEqual(result["save_result"]["outcome"], "written")
        self.assertEqual(self.manager.store.load()[self.identity]["categories"][1]["normal_soc"], 35)

    async def test_changed_future_month_checks_current_month_but_does_not_write(self):
        data = self.payload()
        data["categories"][0]["normal_soc"] = 45  # December/January, not October.
        result = await self.manager.save(data)
        self.assertEqual(self.adapter.applies, [25])
        self.assertEqual(self.adapter.writes, [])
        self.assertEqual(result["save_result"]["outcome"], "already_correct")

    async def test_manual_drift_is_corrected_even_with_unchanged_rules(self):
        self.adapter.current = 41
        result = await self.manager.save(self.payload())
        self.assertEqual(self.adapter.writes, [25])
        self.assertEqual(result["save_result"]["current"], 25)

    async def test_current_warning_selects_category_warning_soc(self):
        self.weather.value = "3"
        data = self.payload()
        data["categories"][1]["warning_soc"] = 65
        result = await self.manager.save(data)
        self.assertEqual(self.adapter.writes, [65])
        self.assertEqual(result["save_result"]["warning_level"], "3")

    async def test_current_local_month_is_used(self):
        self.manager.now = lambda: datetime(2026, 12, 1, 0, 1, tzinfo=self.weather.zone)
        result = await self.manager.save(self.payload())
        self.assertEqual(self.adapter.writes, [40])
        self.assertEqual(result["save_result"]["category"], "Winter")

    async def test_offline_weather_saves_rules_without_lowering_reserve(self):
        self.weather.ready = False
        self.adapter.current = 60
        data = self.payload()
        data["categories"][1]["normal_soc"] = 30
        result = await self.manager.save(data)
        self.assertEqual(self.adapter.applies, [])
        self.assertEqual(self.adapter.current, 60)
        self.assertEqual(result["save_result"]["outcome"], "paused")
        self.assertTrue(result["save_result"]["saved"])

    async def test_modbus_error_saves_rules_and_schedules_retry(self):
        self.adapter.error = modbus.ModbusError("WR offline")
        data = self.payload()
        data["categories"][1]["normal_soc"] = 30
        result = await self.manager.save(data)
        self.assertEqual(result["save_result"]["outcome"], "retry_pending")
        self.assertTrue(result["save_result"]["saved"])
        self.assertFalse(result["save_result"]["applied"])
        self.assertIn(self.identity, self.manager._retry)
        self.assertEqual(self.manager.store.load()[self.identity]["categories"][1]["normal_soc"], 30)

    async def test_persist_failure_does_not_touch_inverter(self):
        data = self.payload()
        data["categories"][1]["normal_soc"] = 33
        with patch.object(self.manager.store, "write", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                await self.manager.save(data)
        self.assertEqual(self.adapter.applies, [])
        self.assertEqual(self.manager.devices[self.identity]["categories"][1]["normal_soc"], 25)

    async def test_changed_endpoint_still_requires_valid_probe(self):
        for key, value in (("host", "192.0.2.20"), ("port", 503), ("unit", 2), ("profile", "sungrow_sh_with_ihm")):
            data = self.payload()
            data["connection"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                await self.manager.save(data)
        self.assertEqual(self.adapter.applies, [])

    async def test_name_change_is_not_a_new_modbus_endpoint(self):
        data = self.payload()
        data["connection"]["name"] = "Neue Bezeichnung"
        result = await self.manager.save(data)
        self.assertEqual(result["connection"]["name"], "Neue Bezeichnung")
        self.assertEqual(self.adapter.writes, [])

    async def test_save_job_api_returns_final_soc_result(self):
        data = self.payload()
        data["categories"][1]["normal_soc"] = 32
        response = await self.client.post("/api/saves", json=data, headers={"X-WRBU-Request": "1"})
        self.assertEqual(response.status, 202)
        identity = (await response.json())["id"]
        async with asyncio.timeout(2):
            while self.manager.save_status(identity)["state"] in ("queued", "running"):
                await asyncio.sleep(0.001)
        response = await self.client.get("/api/saves/"+identity)
        report = await response.json()
        self.assertEqual(report["state"], "complete")
        self.assertEqual(report["result"]["save_result"]["current"], 32)
        for _ in range(10):
            self.manager.save_status(identity)
        self.assertEqual(self.adapter.writes, [32])

    async def test_save_without_probe_cannot_create_new_device(self):
        data = self.payload()
        data["id"] = None
        with self.assertRaises(ValueError):
            await self.manager.save(data)
