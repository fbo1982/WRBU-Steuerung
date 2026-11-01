"""Ingress-only web application; no public port and no user access token."""
import asyncio
from datetime import datetime
import logging
import os
from pathlib import Path

from aiohttp import web

from . import VERSION
from .ha import HABridge
from .manager import DEFAULT_CATEGORIES, Manager, PROFILES, Store
from .modbus import ModbusError
from .weather import WeatherService

STATIC = Path(__file__).parent / "static"


def create_app(manager, bridge, *, ingress_only=True):
    @web.middleware
    async def guard(request, handler):
        if request.path == "/health":
            return web.json_response({"status": "ok", "version": VERSION})
        # Use the TCP peer, not a client-controlled forwarded header.
        if ingress_only and request.remote != "172.30.32.2":
            raise web.HTTPForbidden(text="Nur über Home Assistant Ingress erreichbar.")
        if request.method not in ("GET", "HEAD") and request.headers.get("X-WRBU-Request") != "1":
            raise web.HTTPForbidden(text="Anfrage ohne App-Kennung abgelehnt.")
        try:
            return await handler(request)
        except (ValueError, KeyError, TypeError, ModbusError) as err:
            return web.json_response({"error": str(err)}, status=400)
        except OSError:
            return web.json_response({"error": "Einstellungen konnten nicht dauerhaft gespeichert werden."}, status=503)

    app = web.Application(middlewares=[guard], client_max_size=64 * 1024)

    async def index(request):
        return web.FileResponse(STATIC / "index.html", headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'self'; style-src 'self'; script-src 'self'; object-src 'none'; base-uri 'self'",
        })

    async def status(request):
        return web.json_response({
            "version": VERSION, "ha_ready": bridge.ready, "ha_error": bridge.error,
            "timezone": str(bridge.zone), "server_time": datetime.now(bridge.zone).isoformat(),
            "weather": manager.weather.snapshot() if manager.weather else None, "profiles": PROFILES,
            "devices": manager.snapshot(), "default_categories": DEFAULT_CATEGORIES,
        }, headers={"Cache-Control": "no-store"})

    async def probe(request):
        return web.json_response(await manager.probe(await request.json()))

    async def start_probe(request):
        return web.json_response(await manager.start_probe(await request.json()), status=202)

    async def probe_status(request):
        return web.json_response(manager.probe_status(request.match_info["identity"]),
                                 headers={"Cache-Control": "no-store"})

    async def save(request):
        return web.json_response(await manager.save(await request.json()))

    async def start_save(request):
        return web.json_response(await manager.start_save(await request.json()), status=202)

    async def save_status(request):
        return web.json_response(manager.save_status(request.match_info["identity"]),
                                 headers={"Cache-Control": "no-store"})

    async def delete(request):
        await manager.delete(request.match_info["identity"])
        return web.json_response({"success": True})

    async def weather_search(request):
        data = await request.json()
        return web.json_response({"candidates": await manager.weather.search(data["address"])})

    async def weather_save(request):
        data = await request.json()
        return web.json_response(await manager.weather.configure(data["token"]))

    async def health(request):
        return web.json_response({"status": "ok", "version": VERSION})

    app.router.add_get("/", index)
    app.router.add_get("/health", health)
    app.router.add_get("/api/status", status)
    app.router.add_post("/api/weather/search", weather_search)
    app.router.add_post("/api/weather/location", weather_save)
    app.router.add_post("/api/probe", probe)
    app.router.add_post("/api/probes", start_probe)
    app.router.add_get("/api/probes/{identity}", probe_status)
    app.router.add_post("/api/devices", save)
    app.router.add_post("/api/saves", start_save)
    app.router.add_get("/api/saves/{identity}", save_status)
    app.router.add_delete("/api/devices/{identity}", delete)
    app.router.add_static("/static/", STATIC, show_index=False)
    return app


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    store = Store(Path("/data") / "devices.json")
    # Fail visibly on malformed persisted configuration; never silently overwrite it.
    manager = Manager(store)
    bridge = HABridge(manager, os.environ.get("SUPERVISOR_TOKEN", ""))
    manager.bridge = bridge
    manager.weather = WeatherService(Path("/data"), manager.trigger)
    app = create_app(manager, bridge)

    async def lifecycle(app):
        manager.start_intervals()
        manager.trigger()
        tasks = [asyncio.create_task(bridge.run()), asyncio.create_task(bridge.calendar()),
                 asyncio.create_task(manager.weather.run())]
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await manager.stop()

    app.cleanup_ctx.append(lifecycle)
    web.run_app(app, host="0.0.0.0", port=8099, access_log=None)


if __name__ == "__main__":
    main()

