"""Real app HTTP tests, persistent storage, verified save and controller events."""
import asyncio
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from aiohttp.test_utils import TestClient, TestServer
from aiohttp import web
import yaml

from harness import CONNECTION, CATEGORIES, modbus, profiles
from wrbu import VERSION
from wrbu.ha import HABridge, next_month_check
from wrbu.main import create_app
from wrbu.manager import Manager, Store


class FakeBridge:
    ready = True
    error = ""
    zone = ZoneInfo("Europe/Berlin")
    value = "0"

    def is_warning(self, entity):
        return self.ready and entity == CONNECTION["warning_entity"]

    def level(self, entity=None):
        return self.value if self.ready else None

    def snapshot(self):
        return {"config": None, "level": self.level(), "ready": self.ready, "error": "", "headlines": [], "last_update": None}

    def warnings(self):
        return [{"entity_id": CONNECTION["warning_entity"], "name": "Stockstadt", "region": "Stockstadt", "level": self.value}]


class FakeAdapter:
    def __init__(self):
        self.current = 35
        self.serial = "SN001"
        self.writes = []
        self.applies = []
        self.probe_success = True
        self.error = None
        self.entered = asyncio.Event()
        self.release = None

    async def identify(self):
        return "SH25T", self.serial

    async def probe(self):
        self.writes.append(self.current)
        return profiles.TestResult(
            success=self.probe_success, model="SH25T", serial=self.serial,
            before=self.current, after=self.current,
            write_attempted=True, write_acknowledged=True,
            error="" if self.probe_success else "Rücklesefehler",
        )

    async def apply(self, value):
        self.applies.append(value)
        self.entered.set()
        if self.release:
            await self.release.wait()
        if self.error:
            raise self.error
        written = self.current != value
        if written:
            self.writes.append(value)
            self.current = value
        return self.current, written


class AppTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "devices.json")
        self.adapter = FakeAdapter()
        self.manager = Manager(self.store, adapter_factory=lambda *args: self.adapter)
        self.bridge = FakeBridge()
        self.manager.bridge = self.bridge
        self.manager.weather = self.bridge
        self.manager.now = lambda: datetime(2026, 10, 31, 22, tzinfo=self.bridge.zone)
        self.client = TestClient(TestServer(create_app(self.manager, self.bridge, ingress_only=False)))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        await self.manager.stop()
        self.temp.cleanup()

    async def drain(self):
        if self.manager._tasks:
            await asyncio.gather(*list(self.manager._tasks))

    async def payload(self, editing=None):
        result = await self.manager.probe({"id": editing, "connection": deepcopy(CONNECTION)})
        return {"id": editing, "connection": deepcopy(CONNECTION),
                "proof": result["proof"], "categories": deepcopy(CATEGORIES)}

    async def device(self):
        device = await self.manager.save(await self.payload())
        await self.drain()
        self.adapter.applies.clear()
        self.adapter.writes.clear()
        return device

    async def test_successful_probe_save_and_restart_persistence(self):
        data = await self.payload()
        self.assertEqual(self.adapter.writes, [35])
        self.assertFalse(self.store.path.exists())
        device = await self.manager.save(data)
        await self.drain()
        reloaded = Store(self.store.path).load()
        self.assertEqual(reloaded[device["id"]]["serial"], "SN001")
        self.assertEqual(reloaded[device["id"]]["categories"], CATEGORIES)

    async def test_save_without_probe_rejected(self):
        data = {"connection": CONNECTION, "categories": CATEGORIES}
        with self.assertRaises(ValueError):
            await self.manager.save(data)
        self.assertFalse(self.store.path.exists())

    async def test_failed_probe_has_no_save_proof(self):
        self.adapter.probe_success = False
        data = await self.payload()
        self.assertIsNone(data["proof"])
        with self.assertRaises(ValueError):
            await self.manager.save(data)

    async def test_proof_bound_to_connection_and_consumed(self):
        data = await self.payload()
        altered = deepcopy(data)
        altered["connection"]["port"] = 503
        with self.assertRaises(ValueError):
            await self.manager.save(altered)
        await self.manager.save(data)
        await self.drain()
        with self.assertRaises(ValueError):
            await self.manager.save(data)

    async def test_expired_proof_cannot_save(self):
        data = await self.payload()
        self.manager._proofs[data["proof"]]["expires"] = time.monotonic() - 1
        with self.assertRaises(ValueError):
            await self.manager.save(data)

    async def test_overlapping_months_rejected(self):
        data = await self.payload()
        data["categories"][1]["months"].append(12)
        with self.assertRaises(ValueError):
            await self.manager.save(data)
        self.assertFalse(self.store.path.exists())

    async def test_duplicate_serial_rejected_before_probe_write(self):
        await self.device()
        with self.assertRaises(ValueError):
            await self.payload()
        self.assertEqual(self.adapter.writes, [])

    async def test_edit_does_not_replace_another_device(self):
        device = await self.device()
        self.adapter.serial = "OTHER"
        with self.assertRaises(ValueError):
            await self.payload(device["id"])
        self.assertEqual(self.adapter.writes, [])

    async def test_disk_failure_keeps_previous_configuration(self):
        data = await self.payload()
        with patch.object(self.store, "write", side_effect=OSError("disk unavailable")):
            with self.assertRaises(OSError):
                await self.manager.save(data)
        self.assertEqual(self.manager.devices, {})

    async def test_repeated_weather_events_have_no_modbus_io(self):
        device = await self.device()
        for _ in range(10):
            self.manager.trigger(device["id"])
        await self.drain()
        self.assertEqual(self.adapter.applies, [])

    async def test_warning_start_level_changes_and_clear(self):
        device = await self.device()
        for level in ("2", "3", "4", "1"):
            self.bridge.value = level
            self.manager.trigger(device["id"])
            await self.drain()
        self.assertEqual(self.adapter.writes, [60, 25])

    async def test_month_change_selects_category_specific_warning(self):
        device = await self.device()
        self.bridge.value = "3"
        self.manager.trigger()
        await self.drain()
        self.manager.now = lambda: datetime(2026, 12, 1, 0, 5, tzinfo=self.bridge.zone)
        self.manager.trigger()
        await self.drain()
        self.assertEqual(self.adapter.writes, [60, 70])

    async def test_unavailable_weather_does_not_lower_reserve(self):
        device = await self.device()
        self.bridge.value = "3"
        self.manager.trigger()
        await self.drain()
        self.bridge.ready = False
        self.manager.trigger()
        await self.drain()
        self.assertEqual(self.adapter.writes, [60])
        self.assertEqual(self.manager.runtime[device["id"]]["status"], "pausiert")

    async def test_unassigned_month_has_no_write_even_for_storm(self):
        data = await self.payload()
        data["categories"] = CATEGORIES[:1]
        self.bridge.value = "4"
        await self.manager.save(data)
        await self.drain()
        self.assertEqual(self.adapter.writes, [35])
        self.assertEqual(self.adapter.applies, [])

    async def test_failure_schedules_one_30_second_retry(self):
        device = await self.device()
        self.adapter.error = modbus.ModbusError("offline")
        self.bridge.value = "3"
        self.manager.trigger()
        await self.drain()
        timer = self.manager._retry[device["id"]]
        self.assertTrue(29 <= timer.when() - asyncio.get_running_loop().time() <= 30)
        self.manager.trigger()
        await self.drain()
        self.assertEqual(self.adapter.applies, [60])
        self.adapter.error = None
        self.manager._cancel_retry(device["id"])
        self.manager.trigger()
        await self.drain()
        self.assertEqual(self.adapter.writes, [60])

    async def test_new_desired_cancels_obsolete_retry(self):
        device = await self.device()
        self.adapter.error = modbus.ModbusError("offline")
        self.bridge.value = "3"
        self.manager.trigger()
        await self.drain()
        timer = self.manager._retry[device["id"]]
        self.adapter.error = None
        self.bridge.value = "0"
        self.manager.trigger()
        await self.drain()
        self.assertTrue(timer.cancelled())
        self.assertEqual(self.adapter.current, 25)

    async def test_warning_end_during_inflight_write_is_not_lost(self):
        device = await self.device()
        self.adapter.entered.clear()
        self.adapter.release = asyncio.Event()
        self.bridge.value = "3"
        self.manager.trigger(device["id"])
        await self.adapter.entered.wait()
        self.bridge.value = "0"
        self.manager.trigger(device["id"])
        self.adapter.release.set()
        await self.drain()
        self.assertEqual(self.adapter.writes, [60, 25])

    async def test_delete_cancels_retry_and_removes_persisted_device(self):
        device = await self.device()
        self.adapter.error = modbus.ModbusError("offline")
        self.bridge.value = "3"
        self.manager.trigger()
        await self.drain()
        timer = self.manager._retry[device["id"]]
        await self.manager.delete(device["id"])
        self.assertTrue(timer.cancelled())
        self.assertEqual(self.store.load(), {})

    async def test_http_probe_save_flow(self):
        headers = {"X-WRBU-Request": "1"}
        response = await self.client.post("/api/probe", json={"connection": CONNECTION}, headers=headers)
        self.assertEqual(response.status, 200)
        data = await response.json()
        response = await self.client.post("/api/devices", json={
            "connection": CONNECTION, "proof": data["proof"], "categories": CATEGORIES,
        }, headers=headers)
        self.assertEqual(response.status, 200)
        await self.drain()
        response = await self.client.get("/api/status")
        status = await response.json()
        self.assertEqual(len(status["devices"]), 1)
        self.assertEqual(status["version"], "0.1.3")

    async def test_mutation_without_custom_header_is_forbidden(self):
        response = await self.client.post("/api/probe", json={"connection": CONNECTION})
        self.assertEqual(response.status, 403)
        self.assertEqual(self.adapter.writes, [])

    async def test_ingress_does_not_trust_forwarded_headers(self):
        client = TestClient(TestServer(create_app(self.manager, self.bridge)))
        await client.start_server()
        try:
            response = await client.get("/", headers={"X-Forwarded-For": "172.30.32.2"})
            self.assertEqual(response.status, 403)
            response = await client.get("/health")
            self.assertEqual(response.status, 200)
        finally:
            await client.close()

    async def test_ui_and_relative_static_assets_served(self):
        response = await self.client.get("/")
        self.assertEqual(response.status, 200)
        self.assertIn("static/app.js", await response.text())
        self.assertIn("Content-Security-Policy", response.headers)
        response = await self.client.get("/static/app.js")
        self.assertEqual(response.status, 200)

    async def test_corrupt_store_fails_without_overwriting(self):
        self.store.path.write_text("{broken", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.store.load()
        self.assertEqual(self.store.path.read_text(), "{broken")

    async def test_disk_error_returns_visible_json_without_saving(self):
        data = await self.payload()
        with patch.object(self.store, "write", side_effect=OSError("disk full")):
            response = await self.client.post("/api/devices", json=data,
                                              headers={"X-WRBU-Request": "1"})
        self.assertEqual(response.status, 503)
        self.assertIn("gespeichert", (await response.json())["error"])
        self.assertFalse(self.store.path.exists())

    async def test_real_ha_websocket_only_loads_timezone(self):
        await self.device()
        update = asyncio.Event()
        token_received = asyncio.Event()

        async def handler(request):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            await ws.send_json({"type": "auth_required"})
            auth = await ws.receive_json()
            self.assertEqual(auth, {"type": "auth", "access_token": "test-token"})
            token_received.set()
            await ws.send_json({"type": "auth_ok"})
            for _ in range(2):
                command = await ws.receive_json()
                self.assertIn(command["type"], ("subscribe_events", "get_config"))
                result = {"time_zone": "Europe/Berlin"} if command["type"] == "get_config" else None
                await ws.send_json({"type": "result", "id": command["id"], "success": True, "result": result})
            await update.wait()
            await ws.send_json({"type": "event", "id": 1, "event": {"event_type": "core_config_updated", "data": {}}})
            command = await ws.receive_json()
            self.assertEqual(command["type"], "get_config")
            await ws.send_json({"type": "result", "id": command["id"], "success": True,
                                "result": {"time_zone": "Europe/London"}})
            async for _message in ws:
                pass
            return ws

        app = web.Application()
        app.router.add_get("/ws", handler)
        server = TestServer(app)
        await server.start_server()
        url = str(server.make_url("/ws")).replace("http://", "ws://", 1)
        bridge = HABridge(self.manager, "test-token", url)
        self.manager.bridge = bridge
        task = asyncio.create_task(bridge.run())
        try:
            await asyncio.wait_for(token_received.wait(), 2)
            async with asyncio.timeout(2):
                while not bridge.ready:
                    await asyncio.sleep(0.01)
            await self.drain()
            update.set()
            async with asyncio.timeout(2):
                while str(bridge.zone) != "Europe/London":
                    await asyncio.sleep(0.01)
            self.assertEqual(self.adapter.writes, [])
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await server.close()

    async def test_probe_and_save_without_weather_or_home_assistant(self):
        self.manager.bridge = None
        self.manager.weather = None
        connection = {k: v for k, v in CONNECTION.items() if k != "warning_entity"}
        result = await self.manager.probe({"connection": connection})
        self.assertTrue(result["result"]["success"])
        device = await self.manager.save({"connection": connection, "proof": result["proof"], "categories": CATEGORIES})
        await self.drain()
        self.assertEqual(self.adapter.writes, [35])  # only the explicit communication test
        self.assertEqual(self.manager.runtime[device["id"]]["status"], "pausiert")
        self.assertNotIn("warning_entity", device["connection"])

    async def test_legacy_device_sensor_is_ignored_on_load(self):
        device = await self.device()
        persisted = self.store.load()
        persisted[device["id"]]["connection"]["warning_entity"] = "sensor.old_dwd"
        self.store.write(persisted)
        reloaded = self.store.load()
        self.assertNotIn("warning_entity", reloaded[device["id"]]["connection"])


class CalendarAndBridgeTests(unittest.TestCase):
    def test_local_month_boundary_and_year_rollover(self):
        zone = ZoneInfo("Europe/Berlin")
        for current, expected in [
            (datetime(2026, 10, 31, 23, 59, tzinfo=zone), datetime(2026, 11, 1, 0, 5, tzinfo=zone)),
            (datetime(2026, 11, 1, 0, 4, tzinfo=zone), datetime(2026, 11, 1, 0, 5, tzinfo=zone)),
            (datetime(2026, 11, 1, 0, 5, tzinfo=zone), datetime(2026, 12, 1, 0, 5, tzinfo=zone)),
            (datetime(2026, 12, 20, tzinfo=zone), datetime(2027, 1, 1, 0, 5, tzinfo=zone)),
        ]:
            self.assertEqual(next_month_check(current), expected)

    def test_ha_bridge_has_no_weather_sensor_dependency(self):
        bridge = HABridge(None, "not-a-real-token")
        bridge.connected = True
        self.assertFalse(bridge.ready)
        bridge.accept_config({"time_zone": "Europe/Berlin"})
        self.assertTrue(bridge.ready)
        self.assertEqual(str(bridge.zone), "Europe/Berlin")


class AppPackagingTests(unittest.TestCase):
    def test_supervisor_repository_and_app_version(self):
        root = Path(__file__).resolve().parents[1]
        repository = yaml.safe_load((root / "repository.yaml").read_text())
        config = yaml.safe_load((root / "wrbu_steuerung/config.yaml").read_text())
        self.assertEqual(repository["name"], "WRBU Steuerung")
        self.assertEqual(config["version"], VERSION)
        self.assertEqual(VERSION, "0.1.3")
        self.assertTrue(config["ingress"])
        self.assertTrue(config["homeassistant_api"])
        self.assertNotIn("ports", config)
        self.assertEqual(config["arch"], ["amd64", "aarch64"])
        self.assertTrue((root / "wrbu_steuerung/Dockerfile").exists())

