import unittest
from harness import CONNECTION, modbus, profiles


class FakeSession:
    def __init__(self):
        self.value = 35
        self.readback = None
        self.code = 0xE28
        self.serial = "SH250001"
        self.writes = []
        self.fail_write = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def read(self, address, count=1, *, input_register=False):
        if input_register:
            if address == 4999:
                return [self.code]
            self.assert_serial_address = address
            raw = self.serial.encode().ljust(20, b"\0")
            return [int.from_bytes(raw[i:i+2], "big") for i in range(0, 20, 2)]
        return [self.readback if self.writes and self.readback is not None else self.value]

    async def write(self, address, value):
        self.writes.append((address, value))
        if self.fail_write:
            raise modbus.ModbusError("write failed")
        self.value = value


class ProfileTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.session = FakeSession()
        self.adapter = profiles.SungrowSH(CONNECTION, session_factory=lambda *a: self.session)

    async def test_probe_exactly_one_same_value_write(self):
        result = await self.adapter.probe()
        self.assertTrue(result.success)
        self.assertEqual((result.before, result.after), (35, 35))
        self.assertEqual(self.session.writes, [(13099, 35)])
        self.assertEqual(result.model, "SH25T")

    async def test_probe_readback_mismatch_blocks_success(self):
        self.session.readback = 36
        result = await self.adapter.probe()
        self.assertFalse(result.success)
        self.assertEqual(result.after, 36)

    async def test_unknown_model_never_writes(self):
        self.session.code = 247
        result = await self.adapter.probe()
        self.assertFalse(result.success)
        self.assertEqual(self.session.writes, [])

    async def test_invalid_soc_never_writes(self):
        self.session.value = 65535
        result = await self.adapter.probe()
        self.assertFalse(result.success)
        self.assertEqual(self.session.writes, [])

    async def test_serial_change_never_writes(self):
        self.adapter.expected_serial = "DIFFERENT"
        with self.assertRaises(modbus.ModbusError):
            await self.adapter.apply(60)
        self.assertEqual(self.session.writes, [])

    async def test_successful_write_physically_verified(self):
        self.assertEqual(await self.adapter.apply(60), (60, True))
        self.assertEqual(await self.adapter.apply(60), (60, False))
        self.assertEqual(self.session.writes, [(13099, 60)])

    async def test_setup_write_failure_no_retry(self):
        self.session.fail_write = True
        result = await self.adapter.probe()
        self.assertFalse(result.success)
        self.assertTrue(result.write_attempted)
        self.assertFalse(result.write_acknowledged)
        self.assertEqual(len(self.session.writes), 1)

    async def test_ihm_native_is_explicitly_blocked(self):
        with self.assertRaises(modbus.ModbusError):
            profiles.create_adapter({**CONNECTION, "profile": "sungrow_ihm_native"})
        self.assertIsInstance(profiles.create_adapter(
            {**CONNECTION, "profile": "sungrow_sh_with_ihm"}), profiles.SungrowSH)

