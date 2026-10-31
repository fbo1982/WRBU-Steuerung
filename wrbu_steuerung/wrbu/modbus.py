"""Small bounded Modbus/TCP client for single U16 registers, FC03/04/06.

No external package, no retries hidden below the controller, no polling.
Every response is checked for transaction, protocol, unit, function and length.
"""
import asyncio
import struct


class ModbusError(Exception):
    """Communication or protocol error."""


class ModbusSession:
    """One TCP session; serialized requests and at least 1.05 s request spacing."""

    def __init__(self, host: str, port: int, unit: int, timeout: float = 5,
                 gap: float = 1.05):
        self.host, self.port, self.unit = host, port, unit
        self.timeout, self.gap = timeout, gap
        self.reader = self.writer = None
        self._transaction = 0
        self._next_request = 0.0
        self._lock = asyncio.Lock()

    async def __aenter__(self):
        try:
            async with asyncio.timeout(self.timeout):
                self.reader, self.writer = await asyncio.open_connection(self.host, self.port)
        except TimeoutError as err:
            raise ModbusError(f"Zeitüberschreitung: {self.host}:{self.port} ist innerhalb von {self.timeout:g} Sekunden nicht erreichbar. IP-Adresse und Port prüfen.") from err
        except OSError as err:
            raise ModbusError(f"Verbindung fehlgeschlagen: {err}") from err
        return self

    async def __aexit__(self, *args):
        if self.writer is not None:
            self.writer.close()
            try:
                async with asyncio.timeout(2):
                    await self.writer.wait_closed()
            except (OSError, TimeoutError):
                pass

    async def request(self, function: int, address: int, value: int) -> bytes:
        async with self._lock:
            loop = asyncio.get_running_loop()
            await asyncio.sleep(max(0, self._next_request - loop.time()))
            self._transaction = (self._transaction + 1) % 65536
            pdu = struct.pack(">BHH", function, address, value)
            packet = struct.pack(">HHHB", self._transaction, 0, len(pdu) + 1, self.unit) + pdu
            try:
                async with asyncio.timeout(self.timeout):
                    self.writer.write(packet)
                    await self.writer.drain()
                    header = await self.reader.readexactly(7)
                    tid, protocol, length, unit = struct.unpack(">HHHB", header)
                    if (tid, protocol, unit) != (self._transaction, 0, self.unit):
                        raise ModbusError(f"Antwort gehört nicht zur Anfrage (Geräte-ID erwartet {self.unit}, empfangen {unit}; Transaktion {tid}/{self._transaction}; Protokoll {protocol}).")
                    if not 2 <= length <= 254:
                        raise ModbusError("Ungültige Modbus-Antwortlänge")
                    response = await self.reader.readexactly(length - 1)
            except TimeoutError as err:
                raise ModbusError(f"Keine Modbus-Antwort innerhalb von {self.timeout:g} Sekunden (Geräte-ID {self.unit}, Funktion {function}, Adresse {address}). Geräte-ID und Modbus-Freigabe prüfen.") from err
            except asyncio.IncompleteReadError as err:
                raise ModbusError("Die Gegenstelle hat die Verbindung ohne vollständige Modbus-Antwort geschlossen.") from err
            except OSError as err:
                raise ModbusError(f"Modbus-Übertragung fehlgeschlagen: {err}") from err
            finally:
                self._next_request = loop.time() + self.gap
            if response[0] == (function | 0x80):
                code = response[1] if len(response) == 2 else "?"
                explanation = {1: "Funktion nicht unterstützt", 2: "Register nicht verfügbar",
                               3: "Wert ungültig", 4: "Gerätefehler", 6: "Gerät beschäftigt",
                               11: "Gateway-Zielgerät antwortet nicht"}.get(code, "Anfrage abgewiesen")
                raise ModbusError(f"Gerät meldet Modbus-Ausnahme {code}: {explanation} (Funktion {function}, Adresse {address}).")
            if response[0] != function:
                raise ModbusError("Falscher Modbus-Funktionscode")
            return response

    async def read(self, address: int, count: int = 1, *, input_register=False) -> list[int]:
        if not 1 <= count <= 125:
            raise ValueError("Invalid register count")
        response = await self.request(4 if input_register else 3, address, count)
        if len(response) != 2 + count * 2 or response[1] != count * 2:
            raise ModbusError("Registerantwort unvollständig")
        return list(struct.unpack(f">{count}H", response[2:]))

    async def write(self, address: int, value: int) -> None:
        response = await self.request(6, address, value)
        if response != struct.pack(">BHH", 6, address, value):
            raise ModbusError("Schreibbestätigung stimmt nicht mit Anfrage überein")

