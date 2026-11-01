"""Persistent devices, short-lived successful probes and event-driven control."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone, timedelta
import ipaddress
import json
import logging
import os
from pathlib import Path
import secrets
import time

from .policy import validate_categories, target_for
from .profiles import TestResult, create_adapter
from .modbus import ModbusError

LOGGER = logging.getLogger(__name__)
PROFILES = {
    "sungrow_sh": "Sungrow SH – direkter Zugriff",
    "sungrow_sh_with_ihm": "Sungrow SH über iHomeManager",
}
DEFAULT_CATEGORIES = [
    {"name": "Winter", "months": [1, 12], "normal_soc": 40, "warning_soc": 60},
    {"name": "Übergangszeit", "months": [3, 11], "normal_soc": 30, "warning_soc": 60},
    {"name": "Übrige Monate", "months": [2, 4, 5, 6, 7, 8, 9, 10], "normal_soc": 25, "warning_soc": 60},
]


def validate_interval(value):
    if type(value) is not int or not 0 <= value <= 1440:
        raise ValueError("Abfrageintervall: 0 (Aus) oder 1 bis 1440 ganze Minuten eingeben.")
    return value


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
            # V0.1.0 stored the DWD sensor on the inverter. Weather is now global.
            device["connection"] = normalize_connection(device["connection"])
            device["check_interval_minutes"] = validate_interval(device.get("check_interval_minutes", 0))
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
        self.weather = None
        self.runtime = {}
        self._desired = {}
        self._pending = set()
        self._proofs = {}
        self._retry = {}
        self._checks = {}
        self._forced = set()
        self._tasks = set()
        self._queued = set()
        self._lock = asyncio.Lock()
        self._probe_jobs = {}
        self._save_jobs = {}
        self._stopped = False

    def now(self):
        return datetime.now(self.bridge.zone if self.bridge else timezone.utc)

    def snapshot(self):
        devices = []
        for identity, device in self.devices.items():
            devices.append({**deepcopy(device), "runtime": deepcopy(self.runtime.get(identity, {}))})
        return devices

    async def probe(self, data, progress=None):
        progress = progress or (lambda stage, message: None)
        connection = normalize_connection(data["connection"])
        editing = data.get("id")
        if editing is not None and editing not in self.devices:
            raise ValueError("Dieses Gerät existiert nicht mehr.")
        async with self._lock:
            self._proofs = {k: v for k, v in self._proofs.items() if v["expires"] > time.monotonic()}
            adapter = self.factory(connection)
            if hasattr(adapter, "set_progress"):
                adapter.set_progress(progress)
            progress("connect", "Verbindung und Geräteidentität werden geprüft.")
            model, serial = await adapter.identify()
            if any(d["serial"] == serial and key != editing for key, d in self.devices.items()):
                raise ValueError("Dieser Wechselrichter ist bereits angelegt.")
            if editing and serial != self.devices[editing]["serial"]:
                raise ValueError("Anderer Wechselrichter erkannt. Bitte als neues Gerät anlegen.")
            adapter = self.factory(connection, serial)
            if hasattr(adapter, "set_progress"):
                adapter.set_progress(progress)
            progress("read_soc", "Backup-SOC-Prüfung wird ausgeführt.")
            result = await adapter.probe()
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

    async def start_probe(self, data):
        """Return immediately. Polling reads job memory and never repeats writes."""
        connection = normalize_connection(data["connection"])
        editing = data.get("id")
        now = time.monotonic()
        self._probe_jobs = {key: job for key, job in self._probe_jobs.items()
                            if job["state"] in ("queued", "running") or now-job["created"] < 600}
        for identity, job in self._probe_jobs.items():
            if job["state"] in ("queued", "running") and job["connection"] == connection and job["editing"] == editing:
                return self.probe_status(identity)
        if len(self._probe_jobs) >= 64:
            raise ValueError("Zu viele Prüfaufträge. Bitte laufende Prüfungen abwarten.")
        identity = secrets.token_urlsafe(24)
        job = {"id": identity, "connection": connection, "editing": editing,
               "created": now, "state": "queued", "stage": "queued",
               "message": "Prüfung wartet auf freie Kommunikation.", "result": None, "proof": None}
        self._probe_jobs[identity] = job

        def progress(stage, message):
            job.update(state="running", stage=stage, message=message)

        async def run():
            try:
                report = await self.probe({"connection": connection, "id": editing}, progress)
                job.update(report)
                success = report["result"]["success"]
                job.update(state="complete" if success else "failed",
                           stage=report["result"].get("stage") or job["stage"],
                           message="Prüfung erfolgreich. Speichern ist jetzt möglich." if success else report["result"]["error"])
            except (ModbusError, ValueError, KeyError, TypeError, OSError) as err:
                result = TestResult(error=str(err) or "Kommunikationsprüfung fehlgeschlagen.",
                                    stage=job["stage"], checked_at=datetime.now(timezone.utc).isoformat())
                job.update(state="failed", result=result.as_dict(), proof=None, message=result.error)
            except asyncio.CancelledError:
                job.update(state="failed", proof=None, message="Prüfung beim App-Stopp abgebrochen.")
                raise
            except Exception:
                LOGGER.exception("Unerwarteter Fehler in Kommunikationsprüfung %s", identity)
                result = TestResult(error="Interner Prüfungsfehler. Details stehen im App-Protokoll.", stage=job["stage"])
                job.update(state="failed", result=result.as_dict(), proof=None, message=result.error)

        task = asyncio.create_task(run())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return self.probe_status(identity)

    def probe_status(self, identity):
        job = self._probe_jobs.get(identity)
        if job is None:
            raise ValueError("Prüfauftrag nicht mehr vorhanden. Die App wurde eventuell neu gestartet; bitte erneut prüfen.")
        return {**{key: deepcopy(job[key]) for key in ("id", "state", "stage", "message", "result", "proof")},
                "elapsed": round(time.monotonic()-job["created"], 1)}

    async def save(self, data, progress=None):
        """Persist first, then explicitly check the currently applicable SOC once."""
        progress = progress or (lambda stage, message: None)
        connection = normalize_connection(data["connection"])
        categories = validate_categories(data["categories"])
        if not categories:
            raise ValueError("Mindestens eine Kategorie anlegen.")
        async with self._lock:
            identity = data.get("id")
            existing = self.devices.get(identity) if identity else None
            if identity and existing is None:
                raise ValueError("Das Gerät wurde inzwischen gelöscht.")
            interval = validate_interval(data.get("check_interval_minutes",
                existing.get("check_interval_minutes", 0) if existing else 0))
            endpoint_keys = ("profile", "host", "port", "unit")
            unchanged = existing is not None and all(
                connection[key] == existing["connection"][key] for key in endpoint_keys)
            proof = self._proofs.get(data.get("proof", ""))
            valid_proof = (proof is not None and proof["expires"] >= time.monotonic()
                           and proof["connection"] == connection and proof["id"] == identity)
            if not unchanged and not valid_proof:
                raise ValueError("Neue oder geänderte WR-Verbindungsdaten bitte zuerst erfolgreich prüfen.")
            if valid_proof:
                serial, model, test_result = proof["serial"], proof["model"], proof["result"]
            else:
                serial, model, test_result = existing["serial"], existing["model"], existing["test_result"]
            if existing is not None and serial != existing["serial"]:
                raise ValueError("Die Geräteidentität stimmt nicht mit dem gespeicherten WR überein.")
            identity = identity or secrets.token_hex(8)
            if any(d["serial"] == serial and key != identity for key, d in self.devices.items()):
                raise ValueError("Dieser Wechselrichter ist bereits angelegt.")
            device = {"id": identity, "connection": connection, "categories": categories,
                      "check_interval_minutes": interval,
                      "serial": serial, "model": model, "test_result": test_result}
            candidate = deepcopy(self.devices)
            candidate[identity] = device
            progress("save_config", "Kategorien und Einstellungen werden dauerhaft gespeichert.")
            await asyncio.to_thread(self.store.write, candidate)
            self.devices = candidate
            if valid_proof:
                self._proofs.pop(data["proof"], None)
            self._cancel_retry(identity)
            self._cancel_check(identity)
            self._desired.pop(identity, None)
            self._pending.discard(identity)
            state = self.runtime.setdefault(identity, {})
            state["status"] = "gespeichert"
            if valid_proof:
                state["current"] = proof["result"]["after"]
            # A save is an explicit reconciliation event, even if the target is
            # unchanged: detect manual drift by reading the real inverter.
            await self._evaluate_locked(identity, force=True, progress=progress)
            self._schedule_check(identity)
            state = self.runtime[identity]
            applied = state["status"] in ("gesetzt", "bereits korrekt")
            outcome = ("written" if state["status"] == "gesetzt" else
                       "already_correct" if applied else
                       "retry_pending" if state["status"] == "fehler" else "paused")
            if applied:
                message = (f"Einstellungen gespeichert. Backup-SOC auf {state['current']} % gesetzt und zurückgelesen."
                           if outcome == "written" else
                           f"Einstellungen gespeichert. Backup-SOC am WR ist bereits korrekt: {state['current']} %. Kein Schreiben nötig.")
            elif outcome == "retry_pending":
                message = "Einstellungen gespeichert. SOC-Abgleich fehlgeschlagen; Wiederholung in 30 Sekunden. " + state["error"]
            else:
                message = "Einstellungen gespeichert. Automatischer SOC-Abgleich pausiert: " + state["error"]
            result = {**deepcopy(device), "save_result": {
                "saved": True, "applied": applied, "outcome": outcome, "message": message,
                "category": state.get("category"), "desired": state.get("desired"),
                "current": state.get("current"), "warning_level": state.get("warning_level"),
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }}
            progress("complete", message)
            return result

    async def start_save(self, data):
        connection = normalize_connection(data["connection"])
        categories = validate_categories(data["categories"])
        payload = {"id": data.get("id"), "connection": connection,
                   "categories": categories, "proof": data.get("proof")}
        if "check_interval_minutes" in data:
            payload["check_interval_minutes"] = validate_interval(data["check_interval_minutes"])
        fingerprint = json.dumps(payload, sort_keys=True)
        now = time.monotonic()
        self._save_jobs = {key: job for key, job in self._save_jobs.items()
                           if job["state"] in ("queued", "running") or now-job["created"] < 600}
        for key, job in self._save_jobs.items():
            if job["state"] in ("queued", "running") and job["fingerprint"] == fingerprint:
                return self.save_status(key)
        if len(self._save_jobs) >= 64:
            raise ValueError("Bitte laufende Speichervorgänge abwarten.")
        identity = secrets.token_urlsafe(24)
        job = {"id": identity, "created": now, "state": "queued", "stage": "queued",
               "message": "Speichern wartet auf freie Kommunikation.", "fingerprint": fingerprint,
               "result": None, "error": ""}
        self._save_jobs[identity] = job

        def progress(stage, message):
            job.update(state="running", stage=stage, message=message)

        async def run():
            try:
                result = await self.save(payload, progress)
                job.update(state="complete", stage="complete", result=result,
                           message=result["save_result"]["message"])
            except (ValueError, KeyError, TypeError, OSError, ModbusError) as err:
                job.update(state="failed", error=str(err), message=str(err))
            except asyncio.CancelledError:
                job.update(state="failed", message="Speichern beim App-Stopp abgebrochen. Gespeicherten Stand nach dem Neustart prüfen.")
                raise
            except Exception:
                LOGGER.exception("Unerwarteter Speicherfehler")
                job.update(state="failed", error="Interner Speicherfehler. Gespeicherten Stand und App-Protokoll prüfen.",
                           message="Interner Speicherfehler. Gespeicherten Stand und App-Protokoll prüfen.")

        task = asyncio.create_task(run())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return self.save_status(identity)

    def save_status(self, identity):
        job = self._save_jobs.get(identity)
        if job is None:
            raise ValueError("Speichervorgang nicht mehr vorhanden. Gespeicherten Stand nach einem App-Neustart prüfen.")
        return {**{key: deepcopy(job[key]) for key in ("id", "state", "stage", "message", "result", "error")},
                "elapsed": round(time.monotonic()-job["created"], 1)}

    async def delete(self, identity):
        async with self._lock:
            if identity not in self.devices:
                raise ValueError("Gerät nicht gefunden.")
            candidate = deepcopy(self.devices)
            candidate.pop(identity)
            await asyncio.to_thread(self.store.write, candidate)
            self.devices = candidate
            self._cancel_retry(identity)
            self._cancel_check(identity)
            self._forced.discard(identity)
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

    def start_intervals(self):
        for identity in self.devices:
            self._schedule_check(identity)

    def _cancel_check(self, identity):
        timer = self._checks.pop(identity, None)
        if timer:
            timer.cancel()
        self.runtime.get(identity, {}).pop("next_check", None)

    def _schedule_check(self, identity):
        device = self.devices.get(identity)
        if self._stopped or not device or identity in self._checks:
            return
        seconds = device.get("check_interval_minutes", 0) * 60
        if not seconds:
            return
        self.runtime.setdefault(identity, {})["next_check"] = (
            datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()

        def due():
            self._checks.pop(identity, None)
            self.runtime.get(identity, {}).pop("next_check", None)
            self.trigger(identity, force=True)
        self._checks[identity] = asyncio.get_running_loop().call_later(seconds, due)

    def trigger(self, identity=None, *, force=False):
        if self._stopped:
            return
        for key in ([identity] if identity else list(self.devices)):
            if force:
                self._forced.add(key)
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
                force = identity in self._forced
                self._forced.discard(identity)
                # Periodic checks never bypass the existing 30-second retry.
                await self._evaluate_locked(identity, force=force and identity not in self._retry)
        except Exception:
            LOGGER.exception("Unerwarteter Steuerungsfehler für Gerät %s", identity)
        finally:
            self._schedule_check(identity)

    async def _evaluate_locked(self, identity, *, force=False, progress=None):
        if self._stopped or identity not in self.devices:
            return
        device = self.devices[identity]
        level = self.weather.level() if self.weather else None
        desired, category = target_for(device["categories"], self.now().month, level)
        state = self.runtime.setdefault(identity, {})
        state.update(category=category, warning_level=level, desired=desired)
        if desired is None:
            self._cancel_retry(identity)
            state.update(status="pausiert", error="Wetterstandort/DWD-Daten fehlen oder Monat ohne Kategorie. Kommunikationsprüfung bleibt möglich.")
            return
        if force or desired != self._desired.get(identity):
            self._desired[identity] = desired
            self._pending.add(identity)
            self._cancel_retry(identity)
        if identity not in self._pending:
            state.update(status="bereit", error="")
            return
        if identity in self._retry:
            return
        try:
            adapter = self.factory(device["connection"], device["serial"])
            if progress:
                progress("read_soc", f"Aktuell gültiger Sollwert: {desired} %. Backup-SOC am WR wird geprüft.")
                if hasattr(adapter, "set_progress"):
                    adapter.set_progress(progress)
            actual, written = await adapter.apply(desired)
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
        for key in list(self._checks):
            self._cancel_check(key)
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

