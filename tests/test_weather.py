"""DWD integration, local address parsing and independent inverter setup."""
import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from aiohttp.test_utils import TestClient, TestServer

from harness import CONNECTION, CATEGORIES
from test_app import FakeAdapter, FakeBridge
from wrbu.main import create_app
from wrbu.manager import Manager, Store
from wrbu.weather import WeatherError, WeatherService, extract_town, parse_warnings

ADDRESS = "Musterstraße 99, 64589 Stockstadt am Rhein"
CELL = "806433013"


def collection(properties):
    return {"type": "FeatureCollection", "features": [{"properties": p} for p in properties],
            "numberMatched": len(properties)}


def warning(**overrides):
    now = datetime.now(timezone.utc)
    return {"WARNCELLID": int(CELL), "STATUS": "Actual", "MSGTYPE": "Alert",
            "URGENCY": "Immediate", "SEVERITY": "Severe", "HEADLINE": "Sturmwarnung",
            "ONSET": (now-timedelta(hours=1)).isoformat(),
            "EXPIRES": (now+timedelta(hours=1)).isoformat(), **overrides}


class WeatherTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.requests = []
        self.notifications = []
        self.response = collection([])
        self.fail = False

        async def fetch(params):
            self.requests.append(params.copy())
            if self.fail:
                raise WeatherError("offline")
            if params["typeNames"].endswith("Warngebiete_Gemeinden"):
                return collection([{"WARNCELLID": int(CELL), "NAME": "Gemeinde Stockstadt am Rhein",
                    "BUNDESLAND": "Hessen", "KREIS": "Groß-Gerau"}])
            return self.response

        self.weather = WeatherService(self.temp.name, lambda: self.notifications.append(True), fetch=fetch)

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def configure(self):
        candidates = await self.weather.search(ADDRESS)
        return await self.weather.configure(candidates[0]["token"])

    async def test_only_town_sent_to_dwd_not_full_address(self):
        await self.configure()
        await self.weather.refresh()
        sent = json.dumps(self.requests)
        self.assertIn("Stockstadt am Rhein", sent)
        self.assertNotIn("Musterstraße", sent)
        self.assertNotIn("64589", sent)
        self.assertNotIn("99", sent)
        self.assertEqual(self.weather.config["address"], ADDRESS)

    async def test_local_town_region_cache_avoids_repeated_lookup(self):
        first = await self.weather.search(ADDRESS)
        second = await self.weather.search("Andere Straße 12, 64589 Stockstadt am Rhein")
        self.assertEqual(len(self.requests), 1)
        self.assertNotEqual(first[0]["token"], second[0]["token"])
        self.assertTrue((Path(self.temp.name)/"weather_regions.json").exists())

    async def test_weather_configuration_saved_separately_from_inverters(self):
        await self.configure()
        self.assertTrue((Path(self.temp.name)/"weather.json").exists())
        self.assertFalse((Path(self.temp.name)/"devices.json").exists())
        reloaded = WeatherService(self.temp.name, lambda: None)
        self.assertEqual(reloaded.config["warncell_ids"], [CELL])
        self.assertIsNone(reloaded.level())  # cached config is not a fresh all-clear

    async def test_candidate_tokens_expire_and_cannot_be_guessed(self):
        candidates = await self.weather.search(ADDRESS)
        with self.assertRaises(WeatherError):
            await self.weather.configure("invented")
        self.weather._candidates[candidates[0]["token"]]["expires"] = 0
        with self.assertRaises(WeatherError):
            await self.weather.configure(candidates[0]["token"])

    async def test_empty_valid_response_is_all_clear(self):
        await self.configure()
        self.assertIsNone(self.weather.level())
        await self.weather.refresh()
        self.assertEqual(self.weather.level(), "0")

    async def test_active_levels_and_end_are_calculated(self):
        await self.configure()
        self.response = collection([warning(SEVERITY="Moderate"), warning(SEVERITY="Extreme")])
        await self.weather.refresh()
        self.assertEqual(self.weather.level(), "4")
        self.assertIn("Sturmwarnung", self.weather.snapshot()["headlines"])
        now = datetime.now(timezone.utc)
        for item in self.weather._warnings:
            item["end"] = now - timedelta(seconds=1)
        self.assertEqual(self.weather.level(), "0")

    async def test_advance_expired_and_future_start_do_not_override(self):
        await self.configure()
        now = datetime.now(timezone.utc)
        self.response = collection([
            warning(URGENCY="Future"),
            warning(ONSET=(now+timedelta(hours=1)).isoformat(), EXPIRES=(now+timedelta(hours=2)).isoformat()),
            warning(ONSET=(now-timedelta(hours=2)).isoformat(), EXPIRES=(now-timedelta(hours=1)).isoformat()),
        ])
        await self.weather.refresh()
        self.assertEqual(self.weather.level(), "0")

    async def test_network_error_is_not_all_clear(self):
        await self.configure()
        self.response = collection([warning()])
        await self.weather.refresh()
        self.assertEqual(self.weather.level(), "3")
        self.fail = True
        await self.weather.refresh()
        self.assertIsNone(self.weather.level())
        self.assertEqual(self.weather.snapshot()["error"], "offline")

    async def test_stale_cached_weather_is_unknown(self):
        await self.configure()
        await self.weather.refresh()
        self.weather._fetched = time.monotonic() - 901
        self.assertIsNone(self.weather.level())

    async def test_malformed_or_wrong_region_never_clears_warning(self):
        await self.configure()
        for response in ({}, collection([warning(WARNCELLID=809671155)]),
                         collection([warning(SEVERITY="Unknown")]),
                         collection([warning(ONSET=None)]),
                         collection([{"WARNCELLID": int(CELL)}]),
                         {"type": "FeatureCollection", "features": [{"properties": None}]}):
            self.response = response
            await self.weather.refresh()
            self.assertIsNone(self.weather.level())

    async def test_truncated_response_cannot_clear_warning(self):
        await self.configure()
        self.response = {**collection([]), "numberMatched": 5}
        await self.weather.refresh()
        self.assertIsNone(self.weather.level())

    async def test_disk_error_does_not_replace_weather_configuration(self):
        await self.configure()
        previous = self.weather.config.copy()
        candidates = await self.weather.search(ADDRESS)
        with patch("wrbu.weather.write_json", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                await self.weather.configure(candidates[0]["token"])
        self.assertEqual(self.weather.config, previous)

    async def test_weather_change_does_not_require_new_inverter_probe(self):
        adapter = FakeAdapter()
        manager = Manager(Store(Path(self.temp.name)/"devices.json"), adapter_factory=lambda *a: adapter)
        manager.weather = self.weather
        self.weather.changed = manager.trigger
        manager.now = lambda: datetime(2026, 10, 31, tzinfo=timezone.utc)
        connection = {k: v for k, v in CONNECTION.items() if k != "warning_entity"}
        try:
            result = await manager.probe({"connection": connection})
            device = await manager.save({"connection": connection, "categories": CATEGORIES, "proof": result["proof"]})
            await asyncio.gather(*list(manager._tasks))
            self.assertEqual(adapter.writes, [35])
            await self.configure()
            await self.weather.refresh()
            await asyncio.gather(*list(manager._tasks))
            self.assertEqual(adapter.writes, [35, 25])
            self.response = collection([warning()])
            await self.weather.refresh()
            await asyncio.gather(*list(manager._tasks))
            self.assertEqual(adapter.writes, [35, 25, 60])
            await self.weather.refresh()
            await asyncio.gather(*list(manager._tasks))
            self.assertEqual(adapter.writes, [35, 25, 60])
            self.fail = True
            await self.weather.refresh()
            await asyncio.gather(*list(manager._tasks))
            self.assertEqual(adapter.current, 60)
            self.assertEqual(manager.runtime[device["id"]]["status"], "pausiert")
        finally:
            await manager.stop()

    async def test_api_global_weather_is_independent_of_device_editor(self):
        manager = Manager(Store(Path(self.temp.name)/"devices.json"))
        manager.weather = self.weather
        client = TestClient(TestServer(create_app(manager, FakeBridge(), ingress_only=False)))
        await client.start_server()
        headers = {"X-WRBU-Request": "1"}
        try:
            response = await client.post("/api/weather/search", json={"address": ADDRESS}, headers=headers)
            self.assertEqual(response.status, 200)
            token = (await response.json())["candidates"][0]["token"]
            response = await client.post("/api/weather/location", json={"token": token}, headers=headers)
            self.assertEqual(response.status, 200)
            self.assertEqual(manager.devices, {})
            response = await client.get("/api/status")
            status = await response.json()
            self.assertIn("weather", status)
            self.assertNotIn("warnings", status)
        finally:
            await client.close()
            await manager.stop()

    async def test_run_checks_warning_expiration_without_extra_network_poll(self):
        await self.configure()
        now = datetime.now(timezone.utc)
        self.response = collection([warning(EXPIRES=(now+timedelta(seconds=0.2)).isoformat())])
        task = asyncio.create_task(self.weather.run())
        try:
            async with asyncio.timeout(2):
                while self.weather.level() != "3":
                    await asyncio.sleep(0.01)
                while self.weather.level() != "0":
                    await asyncio.sleep(0.01)
            self.assertEqual(len(self.requests), 2)  # town lookup + one warning fetch
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


class AddressParsingTests(unittest.TestCase):
    def test_complete_address_is_parsed_locally(self):
        address, town = extract_town(ADDRESS)
        self.assertEqual(address, ADDRESS)
        self.assertEqual(town, "Stockstadt am Rhein")

    def test_street_fragments_after_postcode_are_rejected(self):
        for address in ("Stockstadt", "Musterstraße, 64589 Stockstadt", "Musterstraße 1, 64589",
                        "Musterstraße 1, 64589 Stockstadt Andere Straße 8"):
            with self.subTest(address=address), self.assertRaises(WeatherError):
                extract_town(address)

    def test_dwd_cancel_and_test_messages_are_ignored(self):
        self.assertEqual(parse_warnings(collection([warning(MSGTYPE="Cancel"), warning(STATUS="Test")]), [CELL]), [])
