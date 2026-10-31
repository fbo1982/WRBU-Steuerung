"""Persistent devices, short-lived successful probes and event-driven control."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import ipaddress
import json
import logging
import os
from pathlib import Path
import secrets
import time

from .policy import validate_categories, target_for
from .profiles import create_adapter
from .modbus import ModbusError

LOGGER = logging.getLogger(__name__)
PROFILES = {
    "sungrow_sh": "Sungrow SH – direkter Zugriff",
    "sungrow_sh_with_ihm": "Sungrow SH mit iHomeManager – direkt auf WR zugreifen",
    "sungrow_ihm_native": "Über iHomeManager selbst – Backup-SOC derzeit nicht unterstützt",
}
DEFAULT_CATEGORIES = [
    {"name": "Winter", "months": [1, 12], "normal_soc": 40, "warning_soc": 60},
    {"name": "Übergangszeit", "months": [3, 11], "normal_soc": 30, "warning_soc": 60},
    {"name": "Übrige Monate", "months": [2, 4, 5, 6, 7, 8, 9, 10], "normal_soc": 25, "warning_soc": 60},
]


def normalize_connection(data):
    connection = {}
    connection["name"] = str(data.get("name", "")).strip()
    if not connection["name"] or len(connection["name"]) > 80:
        raise ValueError("Bitte einen Gerätenamen mit maximal 80 Zeichen eingeben.")
    connection["profile"] = data.get("profile")
    if connection["profile"] not in PROFILES:
        raise ValueError("Ungültiges Geräteprofil.")
    connection["host"] = str(ipaddress.ip_address(str(data.get("host", "")).strip()))
    for key, maximum in (("port", 65535), ("unit", 247)):
        value = data.get(key)
        if type(value) is not int or not 1 <= value <= maximum:
            raise ValueError(f"Ungültiger Wert für {key}.")
        connection[key] = value
    warning = data.get("warning_entity")
    if not isinstance(warning, str) or not warning.startswith("sensor."):
        raise ValueError("Bitte den aktuellen offiziellen DWD-Sensor auswählen.")
    connection["warning_entity"] = warning
    return connection


class Store:
    def __init__(self, path):
        self.path = Path(path)

    def load(self):
        if not self.path.exists():
            return {}
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if raw.get("schema_version") != 1:
            raise ValueError("Unbekannte Datenversion; gespeicherte Geräte nicht verändert.")
        devices = raw["devices"]
        serials = set()
        for identity, device in devices.items():
            if identity != device["id"] or not device["test_result"]["success"]:
                raise ValueError("Ungültige gespeicherte Geräteprüfung.")
            if device["serial"] in serials:
                raise ValueError("Doppelte Seriennummer im Datenspeicher.")
            serials.add(device["serial"])
            normalize_connection(device["connection"])
            if not validate_categories(device["categories"]):
                raise ValueError("Ein gespeichertes Gerät hat keine Kategorien.")
        return devices

    def write(self, devices):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump({"schema_version": 1, "devices": devices}, handle,
                          ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)


class Manager:
    def __init__(self, store, *, adapter_factory=create_adapter, retry_seconds=30):
        self.store = store
        self.devices = store.load()
        self.factory = adapter_factory
        self.retry_seconds = retry_seconds
        self.bridge = None
        self.runtime = {}
        self._desired = {}
        self._pending = set()
        self._proofs = {}
        self._retry = {}
        self._tasks = set()
        self._queued = set()
        self._lock = asyncio.Lock()
        self._stopped = False

    def now(self):
        return datetime.now(self.bridge.zone if self.bridge else timezone.utc)

    def snapshot(self):
        devices = []
        for identity, device in self.devices.items():
            devices.append({**deepcopy(device), "runtime": deepcopy(self.runtime.get(identity, {}))})
        return devices

    async def probe(self, data):
        connection = normalize_connection(data["connection"])
        editing = data.get("id")
        if editing is not None and editing not in self.devices:
            raise ValueError("Dieses Gerät existiert nicht mehr.")
        if not self.bridge or not self.bridge.is_warning(connection["warning_entity"]):
            raise ValueError("DWD-Sensor fehlt oder Home Assistant ist noch nicht verbunden.")
        async with self._lock:
            self._proofs = {k: v for k, v in self._proofs.items() if v["expires"] > time.monotonic()}
            adapter = self.factory(connection)
            model, serial = await adapter.identify()
            if any(d["serial"] == serial and key != editing for key, d in self.devices.items()):
                raise ValueError("Dieser Wechselrichter ist bereits angelegt.")
            if editing and serial != self.devices[editing]["serial"]:
                raise ValueError("Anderer Wechselrichter erkannt. Bitte als neues Gerät anlegen.")
            result = await self.factory(connection, serial).probe()
            report = result.as_dict()
            proof = None
            if result.success:
                # Bound to normalized connection and edit identity, never supplied by the browser.
                proof = secrets.token_urlsafe(32)
                self._proofs[proof] = {
                    "expires": time.monotonic() + 600, "connection": connection,
                    "id": editing, "result": report, "serial": serial, "model": model,
                }
            return {"result": report, "proof": proof}

    async def save(self, data):
        connection = normalize_connection(data["connection"])
        categories = validate_categories(data["categories"])
        if not categories:
            raise ValueError("Mindestens eine Kategorie anlegen.")
        async with self._lock:
            proof = self._proofs.get(data.get("proof", ""))
            if (proof is None or proof["expires"] < time.monotonic()
                    or proof["connection"] != connection or proof["id"] != data.get("id")):
                raise ValueError("Bitte diese Verbindungsdaten zuerst erfolgreich prüfen (Test gilt 10 Minuten).")
            identity = data.get("id") or secrets.token_hex(8)
            if data.get("id") and identity not in self.devices:
                raise ValueError("Das Gerät wurde inzwischen gelöscht.")
            if any(d["serial"] == proof["serial"] and key != identity for key, d in self.devices.items()):
                raise ValueError("Dieser Wechselrichter ist bereits angelegt.")
            device = {"id": identity, "connection": connection, "categories": categories,
                      "serial": proof["serial"], "model": proof["model"],
                      "test_result": proof["result"]}
            candidate = deepcopy(self.devices)
            candidate[identity] = device
            await asyncio.to_thread(self.store.write, candidate)
            self.devices = candidate
            self._proofs.pop(data["proof"])
            self._cancel_retry(identity)
            self._desired.pop(identity, None)
            self._pending.discard(identity)
            self.runtime[identity] = {"status": "gespeichert", "current": proof["result"]["after"]}
        self.trigger(identity)
        return deepcopy(device)

    async def delete(self, identity):
        async with self._lock:
            if identity not in self.devices:
                raise ValueError("Gerät nicht gefunden.")
            candidate = deepcopy(self.devices)
            candidate.pop(identity)
            await asyncio.to_thread(self.store.write, candidate)
            self.devices = candidate
            self._cancel_retry(identity)
            self._desired.pop(identity, None)
            self._pending.discard(identity)
            self.runtime.pop(identity, None)

    def _cancel_retry(self, identity):
        timer = self._retry.pop(identity, None)
        if timer:
            timer.cancel()

    def _schedule_retry(self, identity):
        if identity not in self._retry and not self._stopped:
            def due():
                self._retry.pop(identity, None)
                self.trigger(identity)
            self._retry[identity] = asyncio.get_running_loop().call_later(self.retry_seconds, due)

    def trigger(self, identity=None):
        if self._stopped:
            return
        for key in ([identity] if identity else list(self.devices)):
            # Coalesce bursts while a write is in flight. Evaluate reads fresh HA data.
            if key in self._queued:
                continue
            self._queued.add(key)
            task = asyncio.create_task(self._run(key))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _run(self, identity):
        try:
            # Mark as dequeued after taking the lock so a later in-flight change
            # may queue one more evaluation without losing the new warning state.
            async with self._lock:
                self._queued.discard(identity)
                await self._evaluate_locked(identity)
        except Exception:
            LOGGER.exception("Unerwarteter Steuerungsfehler für Gerät %s", identity)

    async def _evaluate_locked(self, identity):
        if self._stopped or identity not in self.devices:
            return
        device = self.devices[identity]
        level = self.bridge.level(device["connection"]["warning_entity"]) if self.bridge else None
        desired, category = target_for(device["categories"], self.now().month, level)
        state = self.runtime.setdefault(identity, {})
        state.update(category=category, warning_level=level, desired=desired)
        if desired is None:
            self._cancel_retry(identity)
            state.update(status="pausiert", error="DWD nicht verfügbar oder Monat ohne Kategorie.")
            return
        if desired != self._desired.get(identity):
            self._desired[identity] = desired
            self._pending.add(identity)
            self._cancel_retry(identity)
        if identity not in self._pending:
            state.update(status="bereit", error="")
            return
        if identity in self._retry:
            return
        try:
            actual, written = await self.factory(device["connection"], device["serial"]).apply(desired)
        except ModbusError as err:
            state.update(status="fehler", error=str(err))
            LOGGER.warning("%s: %s; Wiederholung in 30 Sekunden", device["connection"]["name"], err)
            self._schedule_retry(identity)
            return
        self._pending.discard(identity)
        state.update(status="gesetzt" if written else "bereits korrekt", current=actual,
                     error="", last_verified=datetime.now(timezone.utc).isoformat())

    async def stop(self):
        self._stopped = True
        for key in list(self._retry):
            self._cancel_retry(key)
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

