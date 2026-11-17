"""Geräte mit Weboberfläche aus Home Assistant (configuration_url in der Geräteliste)."""
import asyncio
import logging
import os
import time

import aiohttp

log = logging.getLogger("hass")

SUPERVISOR = os.environ.get("BROWSER_SUPERVISOR_URL", "http://supervisor")
TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")


class Devices:
    def __init__(self, session):
        self.session = session
        self.cache = []
        self.loaded = 0.0
        self.error = ""
        self.lock = asyncio.Lock()

    async def _commands(self, *commands):
        """Mehrere Befehle über eine WebSocket-Verbindung (Supervisor-Proxy)."""
        url = SUPERVISOR.replace("http", "ws", 1) + "/core/websocket"
        results = {}
        async with self.session.ws_connect(url, timeout=10, heartbeat=None, max_msg_size=0) as ws:
            msg = await ws.receive_json(timeout=10)
            if msg.get("type") == "auth_required":
                await ws.send_json({"type": "auth", "access_token": TOKEN})
                msg = await ws.receive_json(timeout=10)
            if msg.get("type") != "auth_ok":
                raise RuntimeError("Anmeldung an Home Assistant fehlgeschlagen")
            for n, command in enumerate(commands, start=1):
                await ws.send_json({"id": n, **command})
            while len(results) < len(commands):
                msg = await ws.receive_json(timeout=15)
                if msg.get("type") == "result":
                    if not msg.get("success"):
                        raise RuntimeError((msg.get("error") or {}).get("message") or "Fehler")
                    results[msg["id"]] = msg.get("result") or []
        return [results[n] for n in range(1, len(commands) + 1)]

    async def list(self):
        async with self.lock:
            if time.monotonic() - self.loaded < 120 and self.loaded:
                return self.cache, self.error
            if not TOKEN:
                return [], "Ohne Home Assistant gestartet"
            try:
                devices, areas = await self._commands({"type": "config/device_registry/list"},
                                                      {"type": "config/area_registry/list"})
            except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError, TypeError, ValueError) as err:
                log.warning("Geräteliste nicht geladen: %s", err)
                self.error = "Geräteliste aus Home Assistant nicht erreichbar"
                self.loaded = time.monotonic() - 90
                return self.cache, self.error
            area_names = {a.get("area_id"): a.get("name", "") for a in areas}
            seen, items = set(), []
            for dev in devices:
                url = (dev.get("configuration_url") or "").strip()
                if not url.lower().startswith(("http://", "https://")) or dev.get("disabled_by"):
                    continue
                if url in seen:
                    continue
                seen.add(url)
                maker = " ".join(x for x in (dev.get("manufacturer"), dev.get("model")) if x)
                items.append({"name": dev.get("name_by_user") or dev.get("name") or url, "url": url,
                              "info": maker, "area": area_names.get(dev.get("area_id"), "")})
            items.sort(key=lambda d: (d["area"] == "", d["area"].lower(), d["name"].lower()))
            self.cache, self.error, self.loaded = items, "", time.monotonic()
            return items, ""
