"""Global DWD source. Full addresses stay local; only town names leave the app."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import time

import aiohttp

from . import VERSION
from .events import EVENTS

DWD_URL = "https://maps.dwd.de/geoserver/dwd/ows"
SEVERITY = {"Minor": 1, "Moderate": 2, "Severe": 3, "Extreme": 4}


class WeatherError(ValueError):
    """Unknown weather is never an all-clear."""


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def extract_town(address):
    """Accept 'street number, postcode town'; do not send street/house/postcode."""
    if not isinstance(address, str):
        raise WeatherError("Bitte Straße, Hausnummer, PLZ und Ort eingeben.")
    address = " ".join(address.split())
    match = re.search(r"\b\d{5}\b", address)
    if not 10 <= len(address) <= 250 or not match or not re.search(r"\d", address[:match.start()]):
        raise WeatherError("Bitte das Format Straße Hausnummer, PLZ Ort verwenden.")
    town = re.split(r"[,;]", address[match.end():].strip())[0].strip()
    town = re.sub(r"\s+Deutschland$", "", town, flags=re.IGNORECASE)
    # Reject remaining numbers or unexpected address fragments instead of transmitting them.
    if not 2 <= len(town) <= 100 or not all(c.isalpha() or c in " -.'()/" for c in town):
        raise WeatherError("Der Ortsname hinter der PLZ konnte nicht erkannt werden.")
    return address, town


def features(data):
    if not isinstance(data, dict) or data.get("type") != "FeatureCollection" or not isinstance(data.get("features"), list):
        raise WeatherError("Ungültige Antwort des DWD.")
    rows = data["features"]
    if any(not isinstance(row, dict) or not isinstance(row.get("properties"), dict) for row in rows):
        raise WeatherError("DWD-Antwort enthält ungültige Warngebiets-/Warndaten.")
    count = data.get("numberMatched", data.get("totalFeatures", len(rows)))
    if isinstance(count, (int, float)) and count > len(rows):
        raise WeatherError("DWD-Antwort wurde abgeschnitten; bitte einen genaueren Ort angeben.")
    return rows


def wfs_params(layer, **extra):
    return {"service": "WFS", "version": "2.0.0", "request": "GetFeature",
            "typeNames": "dwd:" + layer, "outputFormat": "application/json", **extra}


def parse_time(value):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError("Timezone missing")
        return result.astimezone(timezone.utc)
    except (TypeError, AttributeError, ValueError) as err:
        raise WeatherError("DWD-Warnung enthält keine gültige Gültigkeitszeit.") from err


def parse_warnings(data, cells):
    warnings = []
    seen = set()
    for row in features(data):
        p = row["properties"]
        if str(p.get("WARNCELLID")) not in cells:
            raise WeatherError("DWD-Antwort gehört nicht zur eingestellten Warnregion.")
        if p.get("STATUS") not in ("Actual", "Exercise", "System", "Test", "Draft") or p.get("MSGTYPE") not in ("Alert", "Update", "Cancel", "Ack", "Error"):
            raise WeatherError("DWD-Antwort enthält keinen gültigen Meldungsstatus.")
        if p.get("STATUS") != "Actual" or p.get("MSGTYPE") not in ("Alert", "Update"):
            continue
        if p.get("URGENCY") == "Future":
            continue  # DWD CAP defines this as advance information, not a current warning.
        raw_code, raw_groups = p.get("EC_II"), p.get("EC_GROUP")
        if (type(raw_code) not in (str, int) or not re.fullmatch(r"[0-9]{1,3}", str(raw_code))
                or not 1 <= int(raw_code) <= 999 or not isinstance(raw_groups, str)):
            raise WeatherError("DWD-Ereigniskennung oder Ereignisgruppen fehlen/ungültig; Reserve bleibt unverändert.")
        code = int(raw_code)
        groups = list(dict.fromkeys(raw_groups.split(";")))
        if not groups or len(groups) > 32 or any(not re.fullmatch(r"[A-Z_]{1,64}", group) for group in groups):
            raise WeatherError("DWD-Ereignisgruppen sind ungültig; Reserve bleibt unverändert.")
        if code in (98, 99) or "TEST" in groups:
            continue
        if p.get("URGENCY") != "Immediate" or p.get("SEVERITY") not in SEVERITY:
            raise WeatherError("DWD-Warnstufe oder Meldungstyp ist unbekannt.")
        start, end = parse_time(p.get("ONSET")), parse_time(p.get("EXPIRES"))
        if end <= start:
            raise WeatherError("Ungültiger DWD-Warnzeitraum.")
        warning = {"level": SEVERITY[p["SEVERITY"]], "start": start, "end": end,
                   "event_code": code, "groups": groups,
                   "event": str(p.get("EVENT") or EVENTS.get(code) or f"Wetterereignis (DWD {code})"),
                   "headline": str(p.get("HEADLINE") or "Wetterwarnung")}
        key = (code, tuple(groups), warning["level"], start, end, warning["headline"])
        if key not in seen:
            seen.add(key)
            warnings.append(warning)
    return warnings


class HTTPClient:
    async def __call__(self, params):
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25),
                    headers={"User-Agent": f"WRBU-Steuerung/{VERSION} (+https://github.com/fbo1982/WRBU-Steuerung)"}) as session:
                async with session.get(DWD_URL, params=params) as response:
                    response.raise_for_status()
                    body = bytearray()
                    async for chunk in response.content.iter_chunked(65536):
                        body.extend(chunk)
                        if len(body) > 2_000_000:
                            raise WeatherError("DWD-Antwort ist zu groß.")
                    return json.loads(body)
        except (aiohttp.ClientError, TimeoutError, OSError, ValueError) as err:
            raise WeatherError("DWD derzeit nicht erreichbar oder Antwort ungültig.") from err


class WeatherService:
    def __init__(self, directory, changed, *, fetch=None, poll_seconds=300, stale_seconds=900):
        self.directory = Path(directory)
        self.path = self.directory / "weather.json"
        self.cache_path = self.directory / "weather_regions.json"
        self.config = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else None
        if self.config:
            self._validate_config(self.config)
        self.cache = json.loads(self.cache_path.read_text(encoding="utf-8")) if self.cache_path.exists() else {}
        self.changed, self.fetch = changed, fetch or HTTPClient()
        self.poll_seconds, self.stale_seconds = poll_seconds, stale_seconds
        self._candidates = {}
        self._lookup_lock = asyncio.Lock()
        self._refresh_lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._warnings = []
        self._valid = False
        self._fetched = 0.0
        self.last_update = None
        self.error = "Noch kein Wetterstandort gespeichert." if not self.config else "DWD-Abfrage wird gestartet."

    @staticmethod
    def _validate_config(config):
        if (not config.get("warncell_ids") or not config.get("region_name")
                or any(not re.fullmatch(r"\d{9}", c) for c in config["warncell_ids"])):
            raise WeatherError("Gespeicherter Wetterstandort ist ungültig; Datei bleibt erhalten.")

    def _active(self):
        if not self.config or not self._valid or time.monotonic() - self._fetched >= self.stale_seconds:
            return None
        now = datetime.now(timezone.utc)
        return [w for w in self._warnings if w["start"] <= now < w["end"]]

    def level(self):
        active = self._active()
        return None if active is None else str(max((w["level"] for w in active), default=0))

    def snapshot(self):
        active = self._active()
        level = None if active is None else str(max((w["level"] for w in active), default=0))
        return {"config": deepcopy(self.config), "level": level, "ready": level is not None,
                "error": (self.error or "DWD-Daten sind veraltet; Reserve bleibt unverändert.") if level is None else "", "last_update": self.last_update,
                "headlines": [w["headline"] for w in active] if active is not None else [],
                "warnings": [{**w, "start": w["start"].isoformat(), "end": w["end"].isoformat()} for w in active] if active is not None else None,
                "poll_seconds": self.poll_seconds}

    async def search(self, address):
        address, town = extract_town(address)
        key = town.casefold()
        async with self._lookup_lock:
            self._candidates = {k: v for k, v in self._candidates.items() if v["expires"] > time.monotonic()}
            if key not in self.cache:
                # Only the public town name is sent; full address/postcode never enter request parameters.
                escaped = town.replace("'", "''")
                data = await self.fetch(wfs_params("Warngebiete_Gemeinden", count=250,
                    CQL_FILTER=f"NAME ILIKE '%{escaped}%'",
                    propertyName="WARNCELLID,NAME,BUNDESLAND,KREIS"))
                grouped = {}
                for row in features(data):
                    p = row["properties"]
                    cell = str(p.get("WARNCELLID"))
                    if not re.fullmatch(r"\d{9}", cell) or not p.get("NAME"):
                        raise WeatherError("DWD-Warngebiet hat keine gültige Kennung.")
                    name = str(p["NAME"])
                    group = (name, str(p.get("KREIS") or ""), str(p.get("BUNDESLAND") or ""))
                    region = grouped.setdefault(group, {"region_name": name,
                        "label": " · ".join(s for s in group if s), "warncell_ids": []})
                    if cell not in region["warncell_ids"]:
                        region["warncell_ids"].append(cell)
                regions = list(grouped.values())
                exact = [r for r in regions if re.sub(r"^(Gemeinde|Stadt|Kreisfreie Stadt)\s+", "", r["region_name"]).casefold() == key]
                regions = exact or regions
                if not regions:
                    raise WeatherError("Kein DWD-Warngebiet für diesen Ort gefunden. Bitte den vollständigen Ortsnamen prüfen.")
                cache = {**self.cache, key: regions}
                if len(cache) > 128:
                    cache.pop(next(iter(cache)))
                await asyncio.to_thread(write_json, self.cache_path, cache)
                self.cache = cache
            result = []
            for region in self.cache[key]:
                token = secrets.token_urlsafe(24)
                self._candidates[token] = {**deepcopy(region), "address": address,
                    "town": town, "expires": time.monotonic() + 600}
                result.append({"token": token, "label": region["label"]})
            return result

    async def configure(self, token):
        async with self._lookup_lock:
            candidate = self._candidates.get(token)
            if not candidate or candidate["expires"] <= time.monotonic():
                raise WeatherError("Adressprüfung ist abgelaufen. Bitte die Adresse erneut prüfen.")
            config = {k: candidate[k] for k in ("address", "town", "label", "region_name", "warncell_ids")}
            self._validate_config(config)
            await asyncio.to_thread(write_json, self.path, config)
            self.config, self._valid, self._warnings = config, False, []
            self.error = "Standort gespeichert. Aktuelle DWD-Daten werden geladen."
            self._candidates.pop(token)
            self.changed()
            self._wake.set()
            return self.snapshot()

    async def refresh(self):
        async with self._refresh_lock:
            config = deepcopy(self.config)
            if not config:
                return
            try:
                cells = config["warncell_ids"]
                data = await self.fetch(wfs_params("Warnungen_Gemeinden", count=500,
                    CQL_FILTER="WARNCELLID IN (" + ",".join(cells) + ")",
                    propertyName="WARNCELLID,STATUS,MSGTYPE,URGENCY,SEVERITY,ONSET,EXPIRES,HEADLINE,EVENT,EC_II,EC_GROUP"))
                warnings = parse_warnings(data, cells)
                if self.config != config:
                    return
                self._warnings, self._valid = warnings, True
                self._fetched = time.monotonic()
                self.last_update = datetime.now(timezone.utc).isoformat()
                self.error = ""
            except (WeatherError, KeyError, TypeError) as err:
                if self.config == config:
                    self._valid = False
                    self.error = str(err)
            self.changed()

    async def run(self):
        next_poll = 0.0
        while True:
            self._wake.clear()
            if self.config and time.monotonic() >= next_poll:
                await self.refresh()
                next_poll = time.monotonic() + self.poll_seconds
            wait = max(0.1, next_poll - time.monotonic()) if self.config else 86400
            now = datetime.now(timezone.utc)
            if self.level() is not None:
                boundaries = [(point - now).total_seconds() for w in self._warnings
                              for point in (w["start"], w["end"]) if point > now]
                if boundaries:
                    wait = min(wait, min(boundaries))
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=max(0.1, wait))
                next_poll = 0.0
            except TimeoutError:
                pass
            self.changed()
