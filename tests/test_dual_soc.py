"""Separate optional emergency target, verified endpoint activation and partial retries."""
import asyncio
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import tempfile
import unittest

from harness import CONNECTION, CATEGORIES, modbus, profiles, policy
from test_app import FakeBridge
from wrbu.manager import Manager, Store


class RegisterSession:
    def __init__(self):
        self.values = {13099: 40, 32617: 20}
        self.writes = []
        self.unsupported = False
        self.fail_address = None
        self.corrupt_address = None

    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass

    async def read(self, address, count=1, *, input_register=False):
        if input_register and address == 4999:
            return [0xE28]
        if input_register and address == 4989:
            raw = b"TESTSH25T".ljust(20, b"\0")
            return [int.from_bytes(raw[i:i+2], "big") for i in range(0, 20, 2)]
        if address == 32617 and self.unsupported:
            raise modbus.ModbusError("Illegal data address")
        return [self.values[address]]

    async def write(self, address, value):
        self.writes.append((address, value))
        if address == self.fail_address:
            raise modbus.ModbusError("Timeout")
        self.values[address] = value+1 if address == self.corrupt_address else value


class DualAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.session = RegisterSession()
        self.adapter = profiles.SungrowSH(CONNECTION, session_factory=lambda *a: self.session)

    async def test_dual_probe_writes_each_original_value_once(self):
        result = await self.adapter.probe(include_emergency=True)
        self.assertTrue(result.success)
        self.assertTrue(profiles.emergency_verified(result.as_dict()))
        self.assertEqual((result.before, result.after), (40, 40))
        self.assertEqual((result.emergency_before, result.emergency_after), (20, 20))
        self.assertEqual(self.session.writes, [(13099, 40), (32617, 20)])

    async def test_backup_only_probe_never_accesses_emergency_register(self):
        self.session.unsupported = True
        self.assertTrue((await self.adapter.probe()).success)
        self.assertEqual(self.session.writes, [(13099, 40)])

    async def test_unavailable_or_zero_emergency_prevents_all_test_writes(self):
        for value in (0, 65535):
            self.session.values[32617] = value
            self.assertFalse((await self.adapter.probe(include_emergency=True)).success)
        self.session.unsupported = True
        self.assertFalse((await self.adapter.probe(include_emergency=True)).success)
        self.assertEqual(self.session.writes, [])

    async def test_dual_probe_readback_mismatch_does_not_succeed(self):
        self.session.corrupt_address = 32617
        result = await self.adapter.probe(include_emergency=True)
        self.assertFalse(result.success)
        self.assertEqual(result.emergency_after, 21)
        self.assertEqual(result.stage, "readback_emergency")

    async def test_matching_targets_generate_no_writes(self):
        result = await self.adapter.apply_targets(40, 20)
        self.assertFalse(result["backup_written"] or result["emergency_written"])
        self.assertEqual(self.session.writes, [])

    async def test_emergency_only_change_does_not_rewrite_backup(self):
        await self.adapter.apply_targets(40, 30)
        self.assertEqual(self.session.writes, [(32617, 30)])

    async def test_no_emergency_target_reads_but_leaves_value_unchanged(self):
        result = await self.adapter.apply_targets(50, None)
        self.assertEqual(result["emergency_current"], 20)
        self.assertEqual(self.session.writes, [(13099, 50)])

    async def test_partial_failure_is_reported_and_retry_skips_correct_backup(self):
        self.session.fail_address = 32617
        with self.assertRaises(profiles.SettingsApplyError) as caught:
            await self.adapter.apply_targets(60, 30)
        self.assertEqual(caught.exception.report["current"], 60)
        self.assertIsNone(caught.exception.report["emergency_current"])
        self.session.fail_address = None
        await self.adapter.apply_targets(60, 30)
        self.assertEqual(self.session.writes, [(13099, 60), (32617, 30), (32617, 30)])

    async def test_invalid_emergency_target_and_identity_prevent_writes(self):
        for value in (0, 101, True, 1.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                await self.adapter.apply_targets(50, value)
        self.adapter.expected_serial = "OTHER"
        with self.assertRaises(modbus.ModbusError):
            await self.adapter.apply_targets(50, 20)
        self.assertEqual(self.session.writes, [])


class DualManagerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.session = RegisterSession()
        self.manager = Manager(Store(Path(self.temp.name)/"devices.json"), adapter_factory=lambda c, s=None:
            profiles.SungrowSH(c, s, session_factory=lambda *a: self.session))
        self.bridge = FakeBridge()
        self.manager.bridge = self.manager.weather = self.bridge
        self.manager.now = lambda: datetime(2026, 10, 31, tzinfo=self.bridge.zone)
        probe = await self.manager.probe({"connection": CONNECTION})
        self.device = await self.manager.save({"connection": CONNECTION, "categories": CATEGORIES, "proof": probe["proof"]})
        self.session.writes.clear()

    async def asyncTearDown(self):
        await self.manager.stop()
        self.temp.cleanup()

    def payload(self):
        categories = deepcopy(CATEGORIES)
        categories[1].update(normal_emergency_soc=20, warning_emergency_soc=40)
        return {"id": self.device["id"], "connection": self.device["connection"], "categories": categories,
                "manage_emergency_soc": True}

    async def activate(self):
        data = self.payload()
        probe = await self.manager.probe({"id": self.device["id"], "connection": self.device["connection"], "verify_emergency": True})
        data["proof"] = probe["proof"]
        result = await self.manager.save(data)
        self.session.writes.clear()
        return result

    async def drain(self):
        while self.manager._tasks:
            await asyncio.gather(*list(self.manager._tasks))

    async def test_first_activation_requires_dual_proof_not_backup_only(self):
        data = self.payload()
        with self.assertRaises(ValueError): await self.manager.save(data)
        probe = await self.manager.probe({"id": self.device["id"], "connection": self.device["connection"]})
        data["proof"] = probe["proof"]
        with self.assertRaises(ValueError): await self.manager.save(data)
        self.assertFalse(self.manager.devices[self.device["id"]]["manage_emergency_soc"])

    async def test_activation_persists_verified_capability_and_both_values(self):
        result = await self.activate()
        self.assertTrue(self.manager.store.load()[self.device["id"]]["manage_emergency_soc"])
        self.assertEqual(result["save_result"]["emergency_current"], 20)
        self.assertTrue(result["save_result"]["applied"])

    async def test_category_update_changes_only_emergency_without_new_probe(self):
        await self.activate()
        data = self.payload()
        data["categories"][1]["normal_emergency_soc"] = 30
        result = await self.manager.save(data)
        self.assertEqual(self.session.writes, [(32617, 30)])
        self.assertEqual(result["save_result"]["emergency_desired"], 30)

    async def test_warning_and_all_clear_reconcile_two_independent_targets(self):
        await self.activate()
        self.bridge.value = "3"
        self.manager.trigger()
        await self.drain()
        self.bridge.value = "0"
        self.manager.trigger()
        await self.drain()
        self.assertEqual(self.session.writes, [(13099, 60), (32617, 40), (13099, 25), (32617, 20)])

    async def test_partial_save_failure_keeps_rules_and_retries_only_missing_change(self):
        await self.activate()
        data = self.payload()
        data["categories"][1].update(normal_soc=35, normal_emergency_soc=30)
        self.session.fail_address = 32617
        result = await self.manager.save(data)
        self.assertEqual(result["save_result"]["outcome"], "retry_pending")
        self.assertEqual(result["save_result"]["current"], 35)
        self.assertIsNone(result["save_result"]["emergency_current"])
        self.session.fail_address = None
        timer = self.manager._retry[self.device["id"]]
        callback = timer._callback
        timer.cancel()
        callback()
        await self.drain()
        self.assertEqual(self.session.writes, [(13099, 35), (32617, 30), (32617, 30)])

    async def test_disabling_additional_control_stops_emergency_register_access(self):
        await self.activate()
        self.session.unsupported = True
        data = self.payload()
        data["manage_emergency_soc"] = False
        result = await self.manager.save(data)
        self.assertTrue(result["save_result"]["applied"])
        self.assertEqual(self.session.writes, [])

    async def test_move_current_month_to_new_category_persists_and_reconciles(self):
        data = self.payload()
        data["manage_emergency_soc"] = False
        data["categories"][1]["months"].remove(10)
        data["categories"].append({"name": "Neue Regel", "months": [10], "normal_soc": 45, "warning_soc": 80})
        result = await self.manager.save(data)
        self.assertEqual(result["save_result"]["current"], 45)
        self.assertNotIn(10, self.manager.store.load()[self.device["id"]]["categories"][1]["months"])


class AdditionalPolicyTests(unittest.TestCase):
    def test_many_templates_are_valid_and_do_not_claim_months(self):
        categories = deepcopy(CATEGORIES)+[{"name": f"Vorlage {i}", "months": [], "normal_soc": 25, "warning_soc": 60} for i in range(30)]
        result = policy.validate_categories(categories)
        self.assertEqual(len(result), 32)
        self.assertEqual(policy.target_for(result, 10, "0"), (25, "Rest"))

    def test_no_assigned_month_pauses_both_targets(self):
        categories = [{"name": "Vorlage", "months": [], "normal_soc": 40, "warning_soc": 60, "normal_emergency_soc": 20}]
        self.assertIsNone(policy.decision_for(categories, 10, "0")["desired"])
        self.assertIsNone(policy.decision_for(categories, 10, "0")["emergency_desired"])

    def test_emergency_target_has_independent_fallback_and_event_values(self):
        categories = deepcopy(CATEGORIES)
        category = categories[1]
        category.update(normal_emergency_soc=20, warning_emergency_soc=15,
            event_rules=[{"event": "group:WIND", "min_level": 2, "soc": 80, "emergency_soc": 30},
                         {"event": "ii:55", "min_level": 3, "soc": 90, "emergency_soc": 40}])
        category = policy.validate_categories(categories)[1]
        self.assertEqual(policy.decision_for(categories, 10, "0", [])["emergency_desired"], 20)
        storm = {"level": 3, "event_code": 55, "groups": ["WIND"]}
        self.assertEqual(policy.decision_for(categories, 10, "3", [storm])["emergency_desired"], 40)
        rain = {"level": 3, "event_code": 61, "groups": ["RAIN"]}
        self.assertEqual(policy.decision_for(categories, 10, "3", [rain])["emergency_desired"], 15)

    def test_invalid_emergency_values_are_rejected(self):
        for value in (0, 101, True, 2.5, "20"):
            categories = deepcopy(CATEGORIES)
            categories[1]["normal_emergency_soc"] = value
            with self.subTest(value=value), self.assertRaises(ValueError): policy.validate_categories(categories)
