"""Real weather parsing -> monthly rule selection -> persisted save -> WR adapter."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from aiohttp.test_utils import TestClient, TestServer
from harness import CONNECTION, CATEGORIES, modbus
from test_app import FakeAdapter, FakeBridge
from test_weather import ADDRESS, CELL, collection, warning
from wrbu.events import CATALOG
from wrbu.main import create_app
from wrbu.manager import Manager, Store
from wrbu.policy import decision_for, validate_categories
from wrbu.weather import WeatherService, parse_warnings


RULES = [
    {"event": "group:WIND", "min_level": 2, "soc": 50},
    {"event": "ii:55", "min_level": 3, "soc": 85},
    {"event": "group:BLACK_ICE", "min_level": 1, "soc": 75},
    {"event": "ii:61", "min_level": 2, "soc": 40},
]


class EventIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.rows = []
        self.adapter = FakeAdapter()
        self.manager = Manager(Store(Path(self.temp.name)/"devices.json"), adapter_factory=lambda *a: self.adapter)
        self.manager.bridge = FakeBridge()
        self.manager.now = lambda: datetime(2026, 10, 31, tzinfo=timezone.utc)

        async def fetch(params):
            if params["typeNames"].endswith("Warngebiete_Gemeinden"):
                return collection([{"WARNCELLID": int(CELL), "NAME": "Gemeinde Stockstadt am Rhein"}])
            return collection(self.rows)
        self.weather = WeatherService(self.temp.name, self.manager.trigger, fetch=fetch)
        self.manager.weather = self.weather
        candidates = await self.weather.search(ADDRESS)
        await self.weather.configure(candidates[0]["token"])
        await self.weather.refresh()
        self.categories = deepcopy(CATEGORIES)
        self.categories[1]["event_rules"] = deepcopy(RULES)
        probe = await self.manager.probe({"connection": CONNECTION})
        self.device = await self.manager.save({"connection": CONNECTION, "categories": self.categories, "proof": probe["proof"]})
        await self.drain()
        self.adapter.writes.clear()
        self.adapter.applies.clear()

    async def asyncTearDown(self):
        await self.manager.stop()
        self.temp.cleanup()

    async def drain(self):
        while self.manager._tasks:
            await asyncio.gather(*list(self.manager._tasks))

    async def weather_change(self, *rows):
        self.rows = list(rows)
        await self.weather.refresh()
        await self.drain()

    def payload(self):
        return {"id": self.device["id"], "connection": self.device["connection"], "categories": deepcopy(self.categories)}

    async def test_event_soc_can_replace_higher_default_for_that_warning(self):
        await self.weather_change(warning(EC_II="61", EC_GROUP="RAIN", SEVERITY="Moderate"))
        self.assertEqual(self.adapter.writes, [40])
        self.assertIn("Starkregen", self.manager.runtime[self.device["id"]]["reason"])

    async def test_different_events_at_same_level_are_not_lost(self):
        await self.weather_change(warning(SEVERITY="Moderate"))
        await self.weather_change(warning(EC_II="85", EC_GROUP="BLACK_ICE;SLIPPERINESS", SEVERITY="Moderate"))
        self.assertEqual(self.adapter.writes, [50, 75])

    async def test_simultaneous_events_and_partial_all_clear(self):
        wind = warning(SEVERITY="Moderate")
        ice = warning(EC_II="85", EC_GROUP="BLACK_ICE;SLIPPERINESS", SEVERITY="Moderate")
        await self.weather_change(wind)
        await self.weather_change(wind, ice)
        await self.weather_change(wind)
        await self.weather_change()
        self.assertEqual(self.adapter.writes, [50, 75, 50, 25])

    async def test_exact_event_and_group_use_highest_matching_soc(self):
        await self.weather_change(warning(EC_II="55", EC_GROUP="WIND"))
        self.assertEqual(self.adapter.writes, [85])

    async def test_unconfigured_warning_keeps_its_own_default(self):
        await self.weather_change(warning(SEVERITY="Moderate"),
            warning(EC_II="63", EC_GROUP="RAIN", EVENT="DAUERREGEN", SEVERITY="Moderate"))
        self.assertEqual(self.adapter.writes, [60])
        self.assertIn("DWD-Standardreserve", self.manager.runtime[self.device["id"]]["reason"])

    async def test_repeated_and_reordered_warnings_do_not_write_again(self):
        wind = warning(SEVERITY="Moderate")
        ice = warning(EC_II="85", EC_GROUP="BLACK_ICE;SLIPPERINESS", SEVERITY="Moderate")
        await self.weather_change(wind, ice)
        for _ in range(5):
            await self.weather_change(ice, wind)
        self.assertEqual(self.adapter.applies, [75])
        self.assertEqual(self.adapter.writes, [75])

    async def test_level_one_can_trigger_explicit_event_rule(self):
        await self.weather_change(warning(EC_II="85", EC_GROUP="BLACK_ICE", SEVERITY="Minor"))
        self.assertEqual(self.adapter.writes, [75])

    async def test_higher_level_rule_can_raise_same_group_soc(self):
        data = self.payload()
        data["categories"][1]["event_rules"].append({"event": "group:WIND", "min_level": 3, "soc": 90})
        await self.manager.save(data)
        await self.weather_change(warning(SEVERITY="Moderate"))
        await self.weather_change(warning(SEVERITY="Severe"))
        self.assertEqual(self.adapter.writes, [50, 90])

    async def test_combined_warning_uses_only_dwd_reported_optional_groups(self):
        data = self.payload()
        data["categories"][1]["event_rules"].append({"event": "group:TORNADO", "min_level": 1, "soc": 95})
        await self.manager.save(data)
        await self.weather_change(warning(EC_II="41", EC_GROUP="THUNDERSTORM;WIND"))
        await self.weather_change(warning(EC_II="41", EC_GROUP="THUNDERSTORM;WIND;TORNADO"))
        self.assertEqual(self.adapter.writes, [50, 95])

    async def test_unknown_new_code_uses_reported_group_or_default(self):
        await self.weather_change(warning(EC_II="999", EC_GROUP="NEW_PHENOMENON"))
        await self.weather_change(warning(EC_II="999", EC_GROUP="WIND"))
        self.assertEqual(self.adapter.writes, [60, 50])

    async def test_missing_event_metadata_freezes_previous_reserve(self):
        await self.weather_change(warning(EC_II="55"))
        self.assertEqual(self.adapter.current, 85)
        await self.weather_change(warning(EC_II=None))
        self.assertEqual(self.adapter.current, 85)
        self.assertEqual(self.manager.runtime[self.device["id"]]["status"], "pausiert")

    async def test_test_advance_and_expired_events_do_not_set_reserve(self):
        past = datetime.now(timezone.utc)-timedelta(hours=2)
        await self.weather_change(warning(EC_II="98", EC_GROUP="TEST"),
            warning(URGENCY="Future", EC_II="55"),
            warning(EC_II="55", ONSET=past.isoformat(), EXPIRES=(past+timedelta(hours=1)).isoformat()))
        self.assertEqual(self.adapter.writes, [])
        self.assertEqual(self.weather.snapshot()["warnings"], [])

    async def test_future_start_is_not_active_and_duplicate_regions_are_deduplicated(self):
        future = datetime.now(timezone.utc)+timedelta(hours=1)
        await self.weather_change(warning(EC_II="55", ONSET=future.isoformat(), EXPIRES=(future+timedelta(hours=1)).isoformat()))
        self.assertEqual(self.adapter.writes, [])
        wind = warning()
        await self.weather_change(wind, deepcopy(wind))
        self.assertEqual(len(self.weather.snapshot()["warnings"]), 1)

    async def test_rules_are_per_month_and_persist_across_restart(self):
        await self.weather_change(warning())
        self.manager.now = lambda: datetime(2026, 12, 1, tzinfo=timezone.utc)
        self.manager.trigger()
        await self.drain()
        self.assertEqual(self.adapter.writes, [50, 70])
        restored = self.manager.store.load()[self.device["id"]]
        self.assertEqual(restored["categories"][1]["event_rules"], RULES)

    async def test_saving_changed_event_rule_reconciles_current_soc_immediately(self):
        await self.weather_change(warning())
        data = self.payload()
        data["categories"][1]["event_rules"][0]["soc"] = 65
        result = await self.manager.save(data)
        self.assertEqual(self.adapter.writes, [50, 65])
        self.assertEqual(result["save_result"]["desired"], 65)
        self.assertIn("Wind", result["save_result"]["reason"])

    async def test_failed_write_retry_uses_latest_event_target(self):
        self.adapter.error = modbus.ModbusError("offline")
        await self.weather_change(warning())
        await self.weather_change(warning(EC_II="85", EC_GROUP="BLACK_ICE"))
        self.adapter.error = None
        timer = self.manager._retry[self.device["id"]]
        callback = timer._callback
        timer.cancel()
        callback()
        await self.drain()
        self.assertEqual(self.adapter.applies, [50, 75, 75])
        self.assertEqual(self.adapter.writes, [75])

    async def test_optional_interval_corrects_drift_using_event_target(self):
        await self.weather_change(warning())
        self.adapter.current = 25
        self.manager.trigger(self.device["id"], force=True)
        await self.drain()
        self.assertEqual(self.adapter.writes, [50, 50])

    async def test_status_api_exposes_catalog_detailed_warnings_and_reason(self):
        await self.weather_change(warning(EC_II="55"))
        client = TestClient(TestServer(create_app(self.manager, FakeBridge(), ingress_only=False)))
        await client.start_server()
        try:
            response = await client.get("/api/status")
            result = await response.json()
            self.assertIn("group:WIND", [item["id"] for item in result["event_catalog"]])
            self.assertEqual(result["weather"]["warnings"][0]["event_code"], 55)
            self.assertIn("Orkanböen", result["devices"][0]["runtime"]["reason"])
        finally:
            await client.close()


class EventValidationTests(unittest.TestCase):
    def test_invalid_rules_cannot_be_saved(self):
        invalid = [None, {}, [None], [{"event": []}],
            [{"event": "ii:999", "min_level": 2, "soc": 50}],
            [{"event": "group:WIND", "min_level": True, "soc": 50}],
            [{"event": "group:WIND", "min_level": 5, "soc": 50}],
            [{"event": "group:WIND", "min_level": 2, "soc": 10}],
            [{"event": "group:WIND", "min_level": 2, "soc": 101}],
            [{"event": "group:WIND", "min_level": 2, "soc": 50.1}],
            [RULES[0], RULES[0]], RULES*9]
        for rules in invalid:
            data = deepcopy(CATEGORIES)
            data[1]["event_rules"] = rules
            with self.subTest(rules=rules), self.assertRaises(ValueError):
                validate_categories(data)

    def test_catalog_is_unique_and_does_not_offer_test_codes(self):
        ids = [item["id"] for item in CATALOG]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertNotIn("ii:98", ids)
        self.assertNotIn("ii:99", ids)

    def test_inconsistent_warning_list_pauses_instead_of_lowering(self):
        categories = deepcopy(CATEGORIES)
        categories[1]["event_rules"] = RULES
        for warnings in (None, [], [{}]):
            self.assertIsNone(decision_for(categories, 10, "3", warnings)["desired"])

    def test_malformed_code_or_group_is_not_an_all_clear(self):
        for changes in ({"EC_II": True}, {"EC_II": "bad"}, {"EC_GROUP": None}, {"EC_GROUP": ""}, {"EC_GROUP": "WIND;"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                parse_warnings(collection([warning(**changes)]), [CELL])
