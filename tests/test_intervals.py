"""Optional reconciliation reads actual SOC; only drift causes a write."""
import asyncio
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import tempfile
import unittest

from harness import CONNECTION, CATEGORIES, modbus
from test_app import FakeAdapter, FakeBridge
from wrbu.manager import Manager, Store


class IntervalTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.adapter = FakeAdapter()
        self.manager = Manager(Store(Path(self.temp.name)/"devices.json"), adapter_factory=lambda *a: self.adapter)
        self.weather = FakeBridge()
        self.manager.weather = self.weather
        self.manager.bridge = self.weather
        self.manager.now = lambda: datetime(2026, 11, 1, tzinfo=self.weather.zone)
        proof = await self.manager.probe({"connection": CONNECTION})
        saved = await self.manager.save({"connection": CONNECTION, "categories": CATEGORIES, "proof": proof["proof"]})
        self.identity = saved["id"]
        self.connection = saved["connection"]
        self.adapter.applies.clear()
        self.adapter.writes.clear()

    async def asyncTearDown(self):
        await self.manager.stop()
        self.temp.cleanup()

    async def set_interval(self, minutes):
        result = await self.manager.save({"id": self.identity, "connection": self.connection,
            "categories": deepcopy(CATEGORIES), "check_interval_minutes": minutes})
        self.adapter.applies.clear()
        self.adapter.writes.clear()
        return result

    async def tick(self):
        timer = self.manager._checks[self.identity]
        callback = timer._callback
        timer.cancel()
        callback()
        await asyncio.gather(*list(self.manager._tasks))

    async def test_default_has_no_periodic_modbus_access(self):
        self.assertFalse(self.manager._checks)
        self.manager.trigger()
        await asyncio.gather(*list(self.manager._tasks))
        self.assertEqual(self.adapter.applies, [])

    async def test_timer_reads_matching_soc_without_writing_and_rearms_once(self):
        await self.set_interval(5)
        for _ in range(3):
            await self.tick()
        self.assertEqual(self.adapter.applies, [25, 25, 25])
        self.assertEqual(self.adapter.writes, [])
        self.assertEqual(len(self.manager._checks), 1)
        timer = self.manager._checks[self.identity]
        self.assertTrue(299 <= timer.when()-asyncio.get_running_loop().time() <= 300)
        self.assertIn("next_check", self.manager.snapshot()[0]["runtime"])

    async def test_manual_drift_is_corrected_once(self):
        await self.set_interval(10)
        self.adapter.current = 42
        await self.tick()
        await self.tick()
        self.assertEqual(self.adapter.writes, [25])

    async def test_check_uses_current_month_and_current_warning(self):
        await self.set_interval(1)
        self.manager.now = lambda: datetime(2026, 12, 1, tzinfo=self.weather.zone)
        self.weather.value = "3"
        await self.tick()
        self.assertEqual(self.adapter.writes, [70])

    async def test_unknown_weather_does_not_correct_or_lower(self):
        await self.set_interval(1)
        self.weather.ready = False
        self.adapter.current = 70
        await self.tick()
        self.assertEqual(self.adapter.applies, [])
        self.assertEqual(self.adapter.current, 70)
        self.assertIn(self.identity, self.manager._checks)

    async def test_periodic_failure_has_one_retry_and_tick_does_not_bypass_it(self):
        await self.set_interval(1)
        self.adapter.error = modbus.ModbusError("offline")
        await self.tick()
        retry = self.manager._retry[self.identity]
        await self.tick()
        self.assertIs(self.manager._retry[self.identity], retry)
        self.assertEqual(self.adapter.applies, [25])
        self.adapter.error = None
        callback = retry._callback
        retry.cancel()
        callback()
        await asyncio.gather(*list(self.manager._tasks))
        self.assertEqual(self.adapter.applies, [25, 25])
        self.assertFalse(self.manager._retry)

    async def test_interval_update_and_disable_cancel_previous_timer(self):
        await self.set_interval(5)
        old = self.manager._checks[self.identity]
        await self.set_interval(15)
        self.assertTrue(old.cancelled())
        timer = self.manager._checks[self.identity]
        self.assertTrue(899 <= timer.when()-asyncio.get_running_loop().time() <= 900)
        await self.set_interval(0)
        self.assertTrue(timer.cancelled())
        self.assertFalse(self.manager._checks)
        self.assertNotIn("next_check", self.manager.runtime[self.identity])

    async def test_restart_restores_interval_and_legacy_load_defaults_to_off(self):
        await self.set_interval(15)
        restarted = Manager(self.manager.store)
        try:
            restarted.start_intervals()
            self.assertEqual(restarted.devices[self.identity]["check_interval_minutes"], 15)
            self.assertEqual(len(restarted._checks), 1)
            restarted.start_intervals()
            self.assertEqual(len(restarted._checks), 1)
        finally:
            await restarted.stop()
        devices = deepcopy(self.manager.devices)
        devices[self.identity].pop("check_interval_minutes")
        self.manager.store.write(devices)
        self.assertEqual(self.manager.store.load()[self.identity]["check_interval_minutes"], 0)

    async def test_invalid_intervals_are_rejected_without_io(self):
        for value in (-1, 1441, 1.5, True, "5", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                await self.set_interval(value)
        self.assertFalse(self.manager._checks)
        self.assertEqual(self.adapter.applies, [])

    async def test_old_api_omitting_interval_preserves_existing_setting(self):
        await self.set_interval(5)
        job = await self.manager.start_save({"id": self.identity, "connection": self.connection,
                                           "categories": CATEGORIES})
        await asyncio.gather(*list(self.manager._tasks))
        self.assertEqual(self.manager.save_status(job["id"])["result"]["check_interval_minutes"], 5)

    async def test_delete_and_stop_cancel_timers(self):
        await self.set_interval(5)
        timer = self.manager._checks[self.identity]
        await self.manager.delete(self.identity)
        self.assertTrue(timer.cancelled())
        self.assertFalse(self.manager._checks)

    async def test_stop_cancels_timer_and_does_not_rearm(self):
        await self.set_interval(5)
        timer = self.manager._checks[self.identity]
        await self.manager.stop()
        self.assertTrue(timer.cancelled())
        self.manager.start_intervals()
        self.assertFalse(self.manager._checks)
