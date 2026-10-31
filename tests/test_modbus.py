"""Real TCP round trips against a local Modbus test server (no device access)."""
import asyncio
import struct
import unittest
from harness import modbus, profiles, CONNECTION


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.mutate = lambda packet: packet
        self.requests = []
        self.value = 40
        self.delay = 0
        self.server = await asyncio.start_server(self.handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        self.handlers = set()

    async def asyncTearDown(self):
        self.server.close()
        await self.server.wait_closed()
        for task in list(self.handlers):
            task.cancel()
        if self.handlers:
            await asyncio.gather(*list(self.handlers), return_exceptions=True)

    async def handle(self, reader, writer):
        task = asyncio.current_task()
        self.handlers.add(task)
        try:
            while True:
                header = await reader.readexactly(7)
                tid, proto, length, unit = struct.unpack(">HHHB", header)
                request = await reader.readexactly(length - 1)
                function, address, value = struct.unpack(">BHH", request)
                self.requests.append((function, address, value))
                if function in (3, 4):
                    words = [self.value]
                    if function == 4 and address == 4999:
                        words = [0xE28]
                    elif function == 4 and address == 4989:
                        raw = b"SH250001".ljust(20, b"\0")
                        words = [int.from_bytes(raw[i:i+2], "big") for i in range(0, 20, 2)]
                    response = bytes([function, len(words) * 2]) + struct.pack(f">{len(words)}H", *words)
                else:
                    self.value = value
                    response = request
                await asyncio.sleep(self.delay)
                packet = struct.pack(">HHHB", tid, 0, len(response) + 1, unit) + response
                writer.write(self.mutate(packet))
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()
            await writer.wait_closed()
            self.handlers.discard(task)

    def session(self, timeout=0.5):
        return modbus.ModbusSession("127.0.0.1", self.port, 1, timeout=timeout, gap=0)

    async def test_read_write_read_actual_packets(self):
        async with self.session() as session:
            self.assertEqual(await session.read(13099), [40])
            await session.write(13099, 60)
            self.assertEqual(await session.read(13099), [60])
        self.assertEqual(self.requests, [(3, 13099, 1), (6, 13099, 60), (3, 13099, 1)])

    async def test_input_register_function(self):
        async with self.session() as session:
            await session.read(4999, input_register=True)
        self.assertEqual(self.requests, [(4, 4999, 1)])

    async def test_full_adapter_probe_over_tcp(self):
        adapter = profiles.SungrowSH(
            {**CONNECTION, "port": self.port},
            session_factory=lambda host, port, unit: self.session(),
        )
        result = await adapter.probe()
        self.assertTrue(result.success)
        self.assertEqual(result.serial, "SH250001")
        self.assertEqual((result.before, result.after), (40, 40))
        self.assertEqual(self.requests, [
            (4, 4999, 1), (4, 4989, 10), (3, 13099, 1),
            (6, 13099, 40), (3, 13099, 1),
        ])

    async def test_mismatched_transaction_protocol_or_unit_rejected(self):
        for index in (1, 3, 6):
            self.mutate = lambda packet, i=index: packet[:i] + bytes([packet[i] ^ 1]) + packet[i+1:]
            with self.subTest(index=index):
                async with self.session() as session:
                    with self.assertRaises(modbus.ModbusError):
                        await session.read(13099)

    async def test_oversized_frame_rejected(self):
        self.mutate = lambda p: p[:4] + struct.pack(">H", 65535) + p[6:]
        async with self.session() as session:
            with self.assertRaises(modbus.ModbusError):
                await session.read(13099)

    async def test_wrong_function_rejected(self):
        self.mutate = lambda p: p[:7] + b"\x04" + p[8:]
        async with self.session() as session:
            with self.assertRaises(modbus.ModbusError):
                await session.read(13099)

    async def test_exception_reply_rejected(self):
        self.mutate = lambda p: p[:4] + b"\x00\x03" + p[6:7] + b"\x83\x02"
        async with self.session() as session:
            with self.assertRaisesRegex(modbus.ModbusError, "Ausnahme 2"):
                await session.read(13099)

    async def test_bad_byte_count_rejected(self):
        self.mutate = lambda p: p[:8] + b"\x04" + p[9:]
        async with self.session() as session:
            with self.assertRaises(modbus.ModbusError):
                await session.read(13099)

    async def test_write_echo_must_match(self):
        self.mutate = lambda p: p[:-1] + bytes([p[-1] ^ 1])
        async with self.session() as session:
            with self.assertRaises(modbus.ModbusError):
                await session.write(13099, 60)

    async def test_timeout_no_automatic_write_retry(self):
        self.delay = 0.2
        async with self.session(timeout=0.05) as session:
            with self.assertRaises(modbus.ModbusError):
                await session.write(13099, 60)
        self.assertEqual(len(self.requests), 1)

