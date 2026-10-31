"""HA timezone integration; weather and inverter setup are independent."""
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
        self._configured = False
        self.calendar_changed = asyncio.Event()

    @property
    def ready(self):
        return self.connected and self._configured

    def accept_config(self, result):
        self.zone = ZoneInfo(result["time_zone"])
        self._configured = True
        self.calendar_changed.set()

    async def run(self):
        async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=None, sock_connect=10)) as session:
            while True:
                try:
                    if not self.token:
                        raise ValueError("Supervisor-Anmeldung fehlt.")
                    self._configured = False
                    async with session.ws_connect(self.url, heartbeat=30) as ws:
                        async with asyncio.timeout(10):
                            hello = await ws.receive_json()
                            if hello.get("type") != "auth_required":
                                raise ValueError("Unerwartete HA-Antwort.")
                            await ws.send_json({"type": "auth", "access_token": self.token})
                            if (await ws.receive_json()).get("type") != "auth_ok":
                                raise ValueError("HA-Anmeldung fehlgeschlagen.")
                        await ws.send_json({"id": 1, "type": "subscribe_events", "event_type": "core_config_updated"})
                        await ws.send_json({"id": 2, "type": "get_config"})
                        self.connected = True
                        next_id = 3
                        async for message in ws:
                            if message.type != aiohttp.WSMsgType.TEXT:
                                if message.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                    break
                                continue
                            data = message.json()
                            if data.get("type") == "result":
                                if not data.get("success"):
                                    raise ValueError("HA-Konfiguration nicht verfügbar.")
                                if data["id"] != 1:
                                    self.accept_config(data["result"])
                                    self.error = ""
                                    self.manager.trigger()
                            elif data.get("type") == "event":
                                await ws.send_json({"id": next_id, "type": "get_config"})
                                next_id += 1
                except (aiohttp.ClientError, OSError, TimeoutError, ValueError, KeyError):
                    self.error = "HA-Zeitzone nicht erreichbar; verwende zuletzt bekannte Zeitzone."
                    LOGGER.warning("%s Wiederholung in 5 Sekunden.", self.error)
                finally:
                    self.connected = False
                await asyncio.sleep(5)

    async def calendar(self):
        while True:
            self.calendar_changed.clear()
            now = datetime.now(self.zone)
            seconds = max(0.1, next_month_check(now).timestamp() - now.timestamp())
            try:
                await asyncio.wait_for(self.calendar_changed.wait(), timeout=seconds)
            except TimeoutError:
                self.manager.trigger()

