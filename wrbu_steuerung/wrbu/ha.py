"""Home Assistant event stream through the authenticated Supervisor proxy."""
import asyncio
from datetime import datetime
import logging
from zoneinfo import ZoneInfo

import aiohttp

LOGGER = logging.getLogger(__name__)


def next_month_check(now):
    if now.day == 1 and (now.hour, now.minute, now.second) < (0, 5, 0):
        return now.replace(hour=0, minute=5, second=0, microsecond=0)
    year, month = (now.year + 1, 1) if now.month == 12 else (now.year, now.month + 1)
    return datetime(year, month, 1, 0, 5, tzinfo=now.tzinfo)


class HABridge:
    def __init__(self, manager, token, url="ws://supervisor/core/websocket"):
        self.manager, self.token, self.url = manager, token, url
        self.connected = False
        self.error = "Verbindung zu Home Assistant wird aufgebaut."
        self.zone = ZoneInfo("Europe/Berlin")
        self.states = {}
        self.registry = {}
        self._received = set()
        self.calendar_changed = asyncio.Event()

    @property
    def ready(self):
        return self.connected and {"states", "registry", "config"} <= self._received

    def is_warning(self, entity):
        return self.ready and entity in self.registry

    def level(self, entity):
        if not self.is_warning(entity):
            return None
        value = self.states.get(entity, {}).get("state")
        return value if value in ("0", "1", "2", "3", "4") else None

    def warnings(self):
        return [
            {"entity_id": key, "name": self.states.get(key, {}).get("attributes", {}).get("friendly_name", key),
             "region": self.states.get(key, {}).get("attributes", {}).get("region_name", ""),
             "level": self.level(key)}
            for key in sorted(self.registry)
        ] if self.ready else []

    def accept_result(self, kind, result):
        if kind == "states":
            self.states = {state["entity_id"]: state for state in result}
        elif kind == "registry":
            self.registry = {row["entity_id"]: row for row in result
                if row.get("platform") == "dwd_weather_warnings"
                and str(row.get("unique_id", "")).endswith("-current_warning_level")
                and not row.get("disabled_by")}
        elif kind == "config":
            self.zone = ZoneInfo(result["time_zone"])
            self.calendar_changed.set()
        self._received.add(kind)

    async def run(self):
        async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=None, sock_connect=10)) as session:
            while True:
                try:
                    if not self.token:
                        raise ValueError("SUPERVISOR_TOKEN fehlt. Bitte als Home-Assistant-App starten.")
                    self._received.clear()
                    async with session.ws_connect(self.url, heartbeat=30) as ws:
                        async with asyncio.timeout(10):
                            hello = await ws.receive_json()
                            if hello.get("type") != "auth_required":
                                raise ValueError("Unerwartete HA-Authentifizierungsantwort.")
                            await ws.send_json({"type": "auth", "access_token": self.token})
                            auth = await ws.receive_json()
                            if auth.get("type") != "auth_ok":
                                raise ValueError("Home-Assistant-Authentifizierung fehlgeschlagen.")
                        pending = {2: "states", 3: "config", 4: "registry"}
                        for command in (
                            {"id": 1, "type": "subscribe_events", "event_type": "state_changed"},
                            {"id": 2, "type": "get_states"},
                            {"id": 3, "type": "get_config"},
                            {"id": 4, "type": "config/entity_registry/list"},
                            {"id": 5, "type": "subscribe_events", "event_type": "entity_registry_updated"},
                            {"id": 6, "type": "subscribe_events", "event_type": "core_config_updated"},
                        ):
                            await ws.send_json(command)
                        self.connected = True
                        next_id = 7
                        async for message in ws:
                            if message.type != aiohttp.WSMsgType.TEXT:
                                if message.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                    break
                                continue
                            data = message.json()
                            if data.get("type") == "result":
                                if not data.get("success"):
                                    raise ValueError("Home Assistant hat eine benötigte API-Anfrage abgelehnt.")
                                kind = pending.pop(data["id"], None)
                                if kind:
                                    self.accept_result(kind, data["result"])
                                    if self.ready:
                                        self.error = ""
                                        self.manager.trigger()
                            elif data.get("type") == "event":
                                event = data["event"]
                                event_type = event.get("event_type")
                                if event_type == "state_changed":
                                    change = event["data"]
                                    entity = change["entity_id"]
                                    if change.get("new_state") is None:
                                        self.states.pop(entity, None)
                                    else:
                                        self.states[entity] = change["new_state"]
                                    for identity, device in list(self.manager.devices.items()):
                                        if device["connection"]["warning_entity"] == entity:
                                            self.manager.trigger(identity)
                                elif event_type in ("entity_registry_updated", "core_config_updated"):
                                    kind = "registry" if event_type == "entity_registry_updated" else "config"
                                    command = "config/entity_registry/list" if kind == "registry" else "get_config"
                                    pending[next_id] = kind
                                    await ws.send_json({"id": next_id, "type": command})
                                    next_id += 1
                except (aiohttp.ClientError, OSError, TimeoutError, ValueError, KeyError):
                    # Never log auth payloads or the Supervisor credential.
                    self.error = "Home Assistant nicht erreichbar oder API-Anmeldung fehlgeschlagen."
                    LOGGER.warning("%s Erneuter Verbindungsversuch in 5 Sekunden.", self.error)
                finally:
                    self.connected = False
                    self.manager.trigger()
                await asyncio.sleep(5)

    async def calendar(self):
        while True:
            self.calendar_changed.clear()
            now = datetime.now(self.zone)
            due = next_month_check(now)
            seconds = max(0.1, due.timestamp() - now.timestamp())
            try:
                await asyncio.wait_for(self.calendar_changed.wait(), timeout=seconds)
            except TimeoutError:
                self.manager.trigger()

