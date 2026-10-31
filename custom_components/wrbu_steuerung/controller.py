"""Event-driven reserve control; serialize work and retry only failed changes."""
import asyncio
import logging
from datetime import datetime, timezone

from homeassistant.core import callback
from homeassistant.helpers.event import (
    async_call_later, async_track_state_change_event, async_track_time_change,
)
from homeassistant.helpers.start import async_at_started
from homeassistant.util import dt as dt_util

from .const import RETRY_SECONDS
from .modbus import ModbusError
from .policy import target_for

_LOGGER = logging.getLogger(__name__)


class Controller:
    def __init__(self, hass, connection, categories, adapter):
        self.hass, self.connection = hass, connection
        self.categories, self.adapter = categories, adapter
        self.current = None
        self.desired = None
        self.category = None
        self.status = "startet"
        self.error = ""
        self.last_verified = None
        self._pending = False
        self._ready = self._stopped = False
        self._lock = asyncio.Lock()
        self._retry = None
        self._unsubs = []
        self._tasks = set()
        self.subscribers = []

    @callback
    def publish(self):
        for subscriber in list(self.subscribers):
            subscriber()

    @callback
    def start(self):
        self._unsubs.append(async_track_state_change_event(
            self.hass, [self.connection["warning_entity"]], self._changed,
        ))
        self._unsubs.append(async_track_time_change(
            self.hass, self._calendar, hour=0, minute=5, second=0,
        ))
        self._unsubs.append(async_at_started(self.hass, self._started))

    @callback
    def _started(self, _hass):
        self._ready = True
        self._queue()

    @callback
    def _changed(self, _event):
        self._queue()

    @callback
    def _calendar(self, now):
        if now.day == 1:
            self._queue()

    @callback
    def _queue(self):
        if self._ready and not self._stopped:
            task = self.hass.async_create_task(self.evaluate())
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    @callback
    def _cancel_retry(self):
        if self._retry is not None:
            self._retry()
            self._retry = None

    @callback
    def _retry_due(self, _now):
        self._retry = None
        self._queue()

    @callback
    def _schedule_retry(self):
        if self._retry is None and not self._stopped:
            self._retry = async_call_later(self.hass, RETRY_SECONDS, self._retry_due)

    async def evaluate(self):
        async with self._lock:
            if self._stopped:
                return
            warning = self.hass.states.get(self.connection["warning_entity"])
            level = warning.state if warning else None
            desired, category = target_for(self.categories, dt_util.now().month, level)
            self.category = category
            if desired is None:
                self._cancel_retry()
                self.status = "pausiert"
                self.error = ("DWD nicht verfügbar oder aktueller Monat ohne Kategorie")
                self.publish()
                return
            if desired != self.desired:
                self.desired = desired
                self._pending = True
                self._cancel_retry()
            if not self._pending or self._retry is not None:
                if not self._pending:
                    self.status, self.error = "bereit", ""
                self.publish()
                return
            try:
                actual, written = await self.adapter.apply(desired)
            except ModbusError as err:
                self.status, self.error = "fehler", str(err)
                self._schedule_retry()
                self.publish()
                _LOGGER.warning("%s: %s; Retry in 30 s", self.connection["name"], err)
                return
            self.current = actual
            self.last_verified = datetime.now(timezone.utc).isoformat()
            self.status = "gesetzt" if written else "bereits_korrekt"
            self.error = ""
            self._pending = False
            self.publish()

    async def manual_set(self, value: int):
        """The optional HA number service uses the same lock and verified path."""
        async with self._lock:
            if self._stopped:
                raise ModbusError("Integration wurde entladen")
            try:
                actual, _written = await self.adapter.apply(value)
            except ModbusError as err:
                self.status, self.error = "fehler", str(err)
                self.publish()
                raise
            self.current = actual
            self.last_verified = datetime.now(timezone.utc).isoformat()
            self.status, self.error = "manuell_gesetzt", ""
            self.publish()

    async def stop(self):
        self._stopped = True
        self._cancel_retry()
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
