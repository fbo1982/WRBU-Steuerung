"""Device adapters. Add another adapter and factory entry for future brands."""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Protocol

from .modbus import ModbusError, ModbusSession

# Sungrow Residential Hybrid Protocol V1.1.11, Appendix 1.
# These codes identify the device; only the common backup-SOC register is used.
SH_MODELS = {
    0xD17: "SH3.0RS", 0xD0D: "SH3.6RS", 0xD18: "SH4.0RS",
    0xD0F: "SH5.0RS", 0xD10: "SH6.0RS", 0xD1A: "SH8.0RS", 0xD1B: "SH10RS",
    **{0xE00 + i: name for i, name in enumerate(["SH5.0RT", "SH6.0RT", "SH8.0RT", "SH10RT"])},
    **{0xE08 + i: name for i, name in enumerate(["SH5.0RT-V122", "SH6.0RT-V122", "SH8.0RT-V122", "SH10RT-V122"])},
    **{0xE0C + i: name for i, name in enumerate(["SH5.0RT-V112", "SH6.0RT-V112", "SH8.0RT-V112", "SH10RT-V112"])},
    **{0xE10 + i: name for i, name in enumerate(["SH5.0RT-20", "SH6.0RT-20", "SH8.0RT-20", "SH10RT-20"])},
    **{0xE20 + i: name for i, name in enumerate(["SH5T", "SH6T", "SH8T", "SH10T", "SH12T", "SH15T", "SH20T"])},
    0xE28: "SH25T",
}


@dataclass
class TestResult:
    success: bool = False
    model: str = ""
    serial: str = ""
    before: int | None = None
    after: int | None = None
    write_attempted: bool = False
    write_acknowledged: bool = False
    error: str = ""
    checked_at: str = ""

    def as_dict(self):
        return asdict(self)


class BackupAdapter(Protocol):
    """Interface independent of manufacturer and Modbus register layout."""
    async def identify(self) -> tuple[str, str]: ...
    async def probe(self) -> TestResult: ...
    async def apply(self, value: int) -> tuple[int, bool]: ...


class SungrowSH:
    ADDRESS = 13099  # Documentation register 13100, U16, 1 %, FC03/06.

    def __init__(self, connection: dict, expected_serial: str | None = None,
                 session_factory=ModbusSession):
        self.connection = connection
        self.expected_serial = expected_serial
        self.session_factory = session_factory

    def session(self):
        return self.session_factory(
            self.connection["host"], self.connection["port"], self.connection["unit"],
        )

    async def _identify(self, session) -> tuple[str, str]:
        code = (await session.read(4999, input_register=True))[0]
        if code not in SH_MODELS:
            raise ModbusError(f"Kein unterstützter SH-Gerätetyp: 0x{code:04X}")
        words = await session.read(4989, 10, input_register=True)
        raw = b"".join(w.to_bytes(2, "big") for w in words)
        try:
            serial = raw.split(b"\x00", 1)[0].decode("ascii").strip()
        except UnicodeDecodeError as err:
            raise ModbusError("Seriennummer unlesbar") from err
        if not serial or not serial.isalnum():
            raise ModbusError("Seriennummer fehlt oder ist ungültig")
        if self.expected_serial is not None and serial != self.expected_serial:
            raise ModbusError("Seriennummer hat sich geändert; kein Schreibzugriff")
        return SH_MODELS[code], serial

    async def identify(self) -> tuple[str, str]:
        async with self.session() as session:
            return await self._identify(session)

    async def _read_soc(self, session) -> int:
        value = (await session.read(self.ADDRESS))[0]
        if not 0 <= value <= 100:
            raise ModbusError(f"Ungültiger Backup-SOC: {value}")
        return value

    async def probe(self) -> TestResult:
        """Exactly one same-value write, no automatic write retry during setup."""
        result = TestResult(checked_at=datetime.now(timezone.utc).isoformat())
        try:
            async with self.session() as session:
                result.model, result.serial = await self._identify(session)
                result.before = await self._read_soc(session)
                result.write_attempted = True
                await session.write(self.ADDRESS, result.before)
                result.write_acknowledged = True
                result.after = await self._read_soc(session)
                if result.after != result.before:
                    raise ModbusError("Rücklesewert weicht vom geschriebenen Wert ab")
                result.success = True
        except ModbusError as err:
            result.error = str(err)
        return result

    async def apply(self, value: int) -> tuple[int, bool]:
        if type(value) is not int or not 0 <= value <= 100:
            raise ValueError("Backup-SOC muss zwischen 0 und 100 liegen")
        async with self.session() as session:
            await self._identify(session)
            current = await self._read_soc(session)
            if current == value:
                return current, False
            await session.write(self.ADDRESS, value)
            actual = await self._read_soc(session)
            if actual != value:
                raise ModbusError(f"Rücklesefehler: erwartet {value}, gelesen {actual}")
            return actual, True


def create_adapter(connection: dict, expected_serial: str | None = None) -> BackupAdapter:
    profile = connection["profile"]
    if profile in ("sungrow_sh", "sungrow_sh_with_ihm"):
        return SungrowSH(connection, expected_serial)
    if profile == "sungrow_ihm_native":
        raise ModbusError(
            "Für den iHomeManager ist hier kein belegtes Backup-SOC-Register verfügbar. "
            "Bitte SH-Profil mit direkter IP des Wechselrichters/WiNet wählen, nicht die iHM-IP."
        )
    raise ModbusError("Unbekanntes Geräteprofil")

