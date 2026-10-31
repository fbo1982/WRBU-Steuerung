"""Probe failures remain visible, polling never repeats the test write."""
import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from aiohttp.test_utils import TestClient, TestServer

from harness import CONNECTION, CATEGORIES, modbus
from test_app import FakeAdapter, FakeBridge
from wrbu.main import create_app
from wrbu.manager import Manager, Store


class ProbeJobTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.adapter = FakeAdapter()
        self.manager = Manager(Store(Path(self.temp.name)/"devices.json"), adapter_factory=lambda *a: self.adapter)
        self.client = TestClient(TestServer(create_app(self.manager, FakeBridge(), ingress_only=False)))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        await self.manager.stop()
        self.temp.cleanup()

    async def finish(self, identity):
        async with asyncio.timeout(2):
            while self.manager.probe_status(identity)["state"] in ("queued", "running"):
                await asyncio.sleep(0.001)
        return self.manager.probe_status(identity)

    async def test_success_returns_proof_and_just_one_test_write(self):
        job = await self.manager.start_probe({"connection": CONNECTION})
        result = await self.finish(job["id"])
        self.assertEqual(result["state"], "complete")
        self.assertTrue(result["proof"])
        for _ in range(20):
            self.manager.probe_status(job["id"])
        self.assertEqual(self.adapter.writes, [35])

    async def test_initial_identity_failure_has_structured_result(self):
        async def fail():
            raise modbus.ModbusError("Keine Antwort von Geräte-ID 1")
        self.adapter.identify = fail
        job = await self.manager.start_probe({"connection": CONNECTION})
        result = await self.finish(job["id"])
        self.assertEqual(result["state"], "failed")
        self.assertIn("Geräte-ID", result["result"]["error"])
        self.assertIsNone(result["proof"])
        self.assertEqual(self.adapter.writes, [])
        with self.assertRaises(ValueError):
            await self.manager.save({"connection": CONNECTION, "categories": CATEGORIES, "proof": result["proof"]})

    async def test_readback_failure_cannot_unlock_save(self):
        self.adapter.probe_success = False
        job = await self.manager.start_probe({"connection": CONNECTION})
        result = await self.finish(job["id"])
        self.assertEqual(result["state"], "failed")
        self.assertIn("Rücklesefehler", result["message"])
        self.assertIsNone(result["proof"])

    async def test_duplicate_running_request_reuses_job(self):
        release = asyncio.Event()
        async def delayed():
            await release.wait()
            return "SH25T", "SN001"
        self.adapter.identify = delayed
        first = await self.manager.start_probe({"connection": CONNECTION})
        await asyncio.sleep(0)
        second = await self.manager.start_probe({"connection": CONNECTION})
        self.assertEqual(first["id"], second["id"])
        release.set()
        await self.finish(first["id"])
        self.assertEqual(self.adapter.writes, [35])

    async def test_api_returns_immediately_and_exposes_failure_without_http_400(self):
        async def fail():
            raise modbus.ModbusError("TCP-Verbindung nicht erreichbar")
        self.adapter.identify = fail
        response = await self.client.post("/api/probes", json={"connection": CONNECTION}, headers={"X-WRBU-Request": "1"})
        self.assertEqual(response.status, 202)
        identity = (await response.json())["id"]
        await self.finish(identity)
        response = await self.client.get("/api/probes/"+identity)
        self.assertEqual(response.status, 200)
        result = await response.json()
        self.assertEqual(result["state"], "failed")
        self.assertIn("nicht erreichbar", result["result"]["error"])
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    async def test_invalid_job_returns_clear_json_error(self):
        response = await self.client.get("/api/probes/missing")
        self.assertEqual(response.status, 400)
        self.assertIn("nicht mehr vorhanden", (await response.json())["error"])

    async def test_unexpected_adapter_error_is_reported_and_never_succeeds(self):
        async def fail():
            raise RuntimeError("unexpected test adapter issue")
        self.adapter.identify = fail
        with self.assertLogs("wrbu.manager", level="ERROR"):
            job = await self.manager.start_probe({"connection": CONNECTION})
            result = await self.finish(job["id"])
        self.assertEqual(result["state"], "failed")
        self.assertIsNone(result["proof"])
        self.assertIn("App-Protokoll", result["message"])

    async def test_stop_cancels_queued_job_without_write(self):
        await self.manager._lock.acquire()
        job = await self.manager.start_probe({"connection": CONNECTION})
        await asyncio.sleep(0)
        await self.manager.stop()
        self.manager._lock.release()
        self.assertEqual(self.manager.probe_status(job["id"])["state"], "failed")
        self.assertEqual(self.adapter.writes, [])
