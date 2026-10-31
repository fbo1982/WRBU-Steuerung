import asyncio
from copy import deepcopy
import unittest
from harness import CATEGORIES, CLOCK, CONNECTION, FakeHass, controller, modbus


class FakeAdapter:
    def __init__(self):
        self.current = 40
        self.writes = []
        self.requests = []
        self.error = None
        self.wait = None
        self.entered = asyncio.Event()

    async def apply(self, value):
        self.requests.append(value)
        self.entered.set()
        if self.wait:
            await self.wait.wait()
        if self.error:
            raise self.error
        written = self.current != value
        if written:
            self.writes.append(value)
            self.current = value
        return self.current, written


class ControllerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        CLOCK.month = 10
        self.hass = FakeHass()
        self.adapter = FakeAdapter()
        self.controller = controller.Controller(
            self.hass, CONNECTION, deepcopy(CATEGORIES), self.adapter)
        self.controller._ready = True

    async def asyncTearDown(self):
        await self.controller.stop()

    async def evaluate(self, level=None):
        if level is not None:
            self.hass.level = level
        await self.controller.evaluate()

    async def retry(self):
        timer = self.hass.timers[-1]
        self.assertEqual(timer["seconds"], 30)
        timer["action"](None)
        await asyncio.gather(*list(self.controller._tasks))

    async def test_start_and_repeated_events_write_once(self):
        await self.evaluate()
        for _ in range(20):
            await self.evaluate()
        self.assertEqual(self.adapter.writes, [25])
        self.assertEqual(self.adapter.requests, [25])

    async def test_start_already_correct_skips_write(self):
        self.adapter.current = 25
        await self.evaluate()
        self.assertEqual(self.adapter.writes, [])

    async def test_storm_changes_no_duplicate_writes_and_clear(self):
        for level in ("2", "3", "4", "1"):
            await self.evaluate(level)
        self.assertEqual(self.adapter.writes, [60, 25])

    async def test_month_change_uses_category_warning_soc(self):
        await self.evaluate("3")
        CLOCK.month = 12
        await self.evaluate("3")
        await self.evaluate("0")
        self.assertEqual(self.adapter.writes, [60, 70, 40])

    async def test_equal_seasonal_months_no_reads_or_writes(self):
        await self.evaluate()
        CLOCK.month = 9
        await self.evaluate()
        self.assertEqual(self.adapter.requests, [25])

    async def test_unknown_dwd_never_clears_override(self):
        await self.evaluate("3")
        await self.evaluate("unavailable")
        self.assertEqual(self.adapter.writes, [60])
        self.assertEqual(self.controller.status, "pausiert")
        await self.evaluate("3")
        self.assertEqual(self.controller.status, "bereit")
        self.assertEqual(self.adapter.writes, [60])

    async def test_unassigned_month_does_not_write_even_for_storm(self):
        self.controller.categories = CATEGORIES[:1]
        await self.evaluate("4")
        self.assertEqual(self.adapter.requests, [])

    async def test_retry_only_after_failure(self):
        self.adapter.error = modbus.ModbusError("offline")
        await self.evaluate("3")
        await self.evaluate("3")
        self.assertEqual(self.adapter.requests, [60])
        self.adapter.error = None
        await self.retry()
        await self.evaluate("3")
        self.assertEqual(self.adapter.requests, [60, 60])
        self.assertEqual(self.adapter.writes, [60])

    async def test_new_desired_cancels_obsolete_retry(self):
        self.adapter.error = modbus.ModbusError("offline")
        await self.evaluate("3")
        self.adapter.error = None
        await self.evaluate("0")
        self.assertTrue(self.hass.timers[0]["cancelled"])
        self.assertEqual(self.adapter.writes, [25])

    async def test_unknown_dwd_cancels_pending_retry_then_resumes(self):
        self.adapter.error = modbus.ModbusError("offline")
        await self.evaluate("3")
        await self.evaluate("unknown")
        self.assertTrue(self.hass.timers[0]["cancelled"])
        self.adapter.error = None
        await self.evaluate("3")
        self.assertEqual(self.adapter.writes, [60])

    async def test_concurrent_events_serialized(self):
        await asyncio.gather(*(self.evaluate("3") for _ in range(20)))
        self.assertEqual(self.adapter.requests, [60])

    async def test_warning_ends_while_write_inflight(self):
        self.adapter.wait = asyncio.Event()
        first = asyncio.create_task(self.evaluate("3"))
        await self.adapter.entered.wait()
        second = asyncio.create_task(self.evaluate("0"))
        self.adapter.wait.set()
        await asyncio.gather(first, second)
        self.assertEqual(self.adapter.writes, [60, 25])

    async def test_calendar_guard_and_stop(self):
        self.controller._calendar(type("Now", (), {"day": 2})())
        self.assertEqual(len(self.controller._tasks), 0)
        self.controller._calendar(type("Now", (), {"day": 1})())
        await asyncio.gather(*list(self.controller._tasks))
        await self.controller.stop()
        await self.evaluate("3")
        self.assertEqual(self.adapter.requests, [25])

    async def test_stop_cancels_retry(self):
        self.adapter.error = modbus.ModbusError("offline")
        await self.evaluate("3")
        await self.controller.stop()
        self.assertTrue(self.hass.timers[0]["cancelled"])

    async def test_stop_cancels_inflight_task(self):
        self.adapter.wait = asyncio.Event()
        self.controller._queue()
        await self.adapter.entered.wait()
        tasks = list(self.controller._tasks)
        await self.controller.stop()
        self.assertTrue(all(task.cancelled() for task in tasks))
