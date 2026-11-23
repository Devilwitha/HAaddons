"""Raspberry Pi 5 Bridge für Game Remote.

Startet im selben asyncio-Event-Loop:
  - WebSocket-Server (Standard 0.0.0.0:8765): Eingaben vom Home-Assistant-Add-on, Status zurück
  - Video-Server (Standard 0.0.0.0:8766): WebRTC (/offer) und MJPEG (/stream.mjpg)

WebSocket-Protokoll (JSON):
  Client -> Bridge
    {"type": "input", "buttons": ["A", "DPAD_UP", ...], "ls": [x, y], "rs": [x, y]}
        x, y von -100 bis 100, y nach oben positiv
    {"type": "heartbeat", "t": <Zahl>}          -> {"type": "pong", "t": <Zahl>}
    {"type": "system", "action": "pair"}        -> {"type": "info" | "error", "message": ...}
  Bridge -> Client (jede Sekunde und nach Änderungen)
    {"type": "status", "switch": {"state", "detail"}, "capture": {...}, "clients": n}

Mehrere Clients gleichzeitig (z. B. Handy mit Touch und PC mit Controller): Tasten werden
zusammengefasst, bei den Sticks gilt der stärkere Ausschlag.

Schnellster Weg für Eingaben: der WebRTC-Datenkanal "input" (direkt Browser -> Pi, ungeordnet, ohne
Wiederholung). Nachrichten wie oben plus "seq"; ältere seq werden verworfen. Der Browser wiederholt den
Zustand alle 50 ms - kommt CHANNEL_STALE Sekunden nichts, gilt der Kanal als neutral.
"""
import argparse
import asyncio
import json
import logging
import math
import signal
import sys
import time
from pathlib import Path

import yaml
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from controller_emulator import ControllerEmulator, clamp_stick
from system_tune import SystemStats, tune
from video_streamer import VideoStreamer

log = logging.getLogger("bridge")

HERE = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATHS = [Path("/etc/switch2-bridge/config.yaml"), HERE / "config.yaml"]
DEFAULTS = {
    "websocket": {"host": "0.0.0.0", "port": 8765},
    "video": {"host": "0.0.0.0", "port": 8766, "device": "auto", "width": 1280, "height": 720,
              "fps": 60, "fourcc": "MJPG", "jpeg_quality": 80, "jpeg_passthrough": True, "nv12": True,
              "capture_buffers": 3, "mjpeg_fps": 30, "webrtc_fps": 60,
              "webrtc_max_bitrate": 6000000, "webrtc_min_bitrate": 3000000, "webrtc_fixed_bitrate": True,
              "webrtc_threads": 4},
    "system": {"tune": True, "cpu_governor": "performance", "wifi_power_save_off": True},
    "audio": {"device": "auto"},
    "controller": {"enabled": True, "watchdog_seconds": 2.0, "input_rate_hz": 120,
                   "reconnect_file": "/var/lib/switch2-bridge/switch_addresses.json"},
    "log_level": "INFO",
}
MAX_MESSAGE = 64 * 1024
CHANNEL_STALE = 0.5


def parse_input(msg):
    buttons = msg.get("buttons") or []
    return {
        "buttons": {str(b) for b in buttons} if isinstance(buttons, list) else set(),
        "ls": clamp_stick(msg.get("ls")),
        "rs": clamp_stick(msg.get("rs")),
    }


def merge(base, extra):
    out = dict(base)
    for key, value in (extra or {}).items():
        out[key] = merge(out[key], value) if isinstance(out.get(key), dict) and isinstance(value, dict) else value
    return out


def load_config(path):
    candidates = [Path(path)] if path else DEFAULT_CONFIG_PATHS
    for candidate in candidates:
        if candidate.is_file():
            with candidate.open(encoding="utf-8") as handle:
                data = yaml.safe_load(handle) or {}
            log.info("Konfiguration: %s", candidate)
            return merge(DEFAULTS, data)
    log.warning("Keine config.yaml gefunden - nutze Standardwerte")
    return dict(DEFAULTS)


class Bridge:
    def __init__(self, cfg, dry_run):
        self.dry_run = dry_run
        self.cfg = cfg
        self.controller = ControllerEmulator(cfg["controller"], dry_run=dry_run)
        self.video = VideoStreamer(cfg["video"], cfg.get("audio"))
        self.clients = {}                 # websocket -> letzter Eingabezustand
        self.channels = {}                # WebRTC-Datenkanal -> {"state", "seq", "seen"}
        self.video.on_channel = self.add_channel
        self.video.source.input_active = lambda: bool(self.controller.buttons)
        self.video.audio.input_active = self.video.source.input_active
        self.tasks = []
        self.stopping = asyncio.Event()

    # ------------------------------------------------------------------ Eingaben zusammenführen
    def apply_inputs(self):
        buttons = set()
        ls = (0.0, 0.0)
        rs = (0.0, 0.0)
        states = list(self.clients.values()) + [entry["state"] for entry in self.channels.values()]
        for state in states:
            if not state:
                continue
            buttons.update(state["buttons"])
            if math.hypot(*state["ls"]) > math.hypot(*ls):
                ls = state["ls"]
            if math.hypot(*state["rs"]) > math.hypot(*rs):
                rs = state["rs"]
        self.controller.set_inputs(buttons, ls, rs)

    # ------------------------------------------------------------------ WebRTC-Datenkanal
    def add_channel(self, channel, pc):
        self.channels[channel] = {"state": None, "seq": -1, "seen": time.monotonic()}
        log.info("Eingabe-Datenkanal offen (%d aktiv)", len(self.channels))

        @channel.on("message")
        def on_message(raw):
            entry = self.channels.get(channel)
            if entry is None or isinstance(raw, bytes):
                return
            try:
                msg = json.loads(raw)
            except ValueError:
                return
            if not isinstance(msg, dict):
                return
            if msg.get("type") == "heartbeat":
                # Ping direkt über den Datenkanal (für die Messanzeige)
                try:
                    channel.send(json.dumps({"type": "pong", "t": msg.get("t"), "via": "dc"}))
                except Exception as err:          # Kanal gerade zu
                    log.debug("Pong über Datenkanal: %s", err)
                return
            if msg.get("type") != "input":
                return
            seq = msg.get("seq")
            if isinstance(seq, int):
                if seq <= entry["seq"]:
                    return                    # veraltetes Paket (ungeordnete Zustellung)
                entry["seq"] = seq
            entry["seen"] = time.monotonic()
            entry["state"] = parse_input(msg)
            self.apply_inputs()

        def drop():
            if self.channels.pop(channel, None) is not None:
                self.apply_inputs()
                log.info("Eingabe-Datenkanal geschlossen (%d aktiv)", len(self.channels))

        channel.on("close", drop)

        @pc.on("connectionstatechange")
        def on_state():
            if pc.connectionState in ("failed", "closed"):
                drop()
            elif pc.connectionState == "disconnected":
                entry = self.channels.get(channel)
                if entry and entry["state"]:
                    entry["state"] = None         # kurz weg: loslassen, Kanal aber behalten
                    self.apply_inputs()

    async def channel_watch(self):
        """Kanäle ohne Lebenszeichen gelten als neutral (der Browser wiederholt den Zustand alle 50 ms)."""
        while True:
            await asyncio.sleep(0.1)
            now = time.monotonic()
            stale = [entry for entry in self.channels.values()
                     if entry["state"] and now - entry["seen"] > CHANNEL_STALE]
            if stale:
                for entry in stale:
                    entry["state"] = None
                log.warning("Eingabe-Datenkanal ohne Lebenszeichen - Eingaben losgelassen")
                self.apply_inputs()

    def status_message(self):
        return json.dumps({
            "type": "status",
            "switch": self.controller.status(),
            "capture": self.video.source.status(),
            "stream": self.video.stats(),
            "system": self.system.snapshot(),
            "clients": len(self.clients),
        })

    # ------------------------------------------------------------------ WebSocket
    async def handle_client(self, websocket):
        peer = websocket.remote_address[0] if websocket.remote_address else "?"
        self.clients[websocket] = None
        log.info("Client verbunden: %s (%d aktiv)", peer, len(self.clients))
        try:
            await websocket.send(self.status_message())
            async for raw in websocket:
                if isinstance(raw, bytes):
                    continue
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(msg, dict):
                    continue
                kind = msg.get("type")
                if kind == "input":
                    self.clients[websocket] = parse_input(msg)
                    self.apply_inputs()
                elif kind == "heartbeat":
                    self.controller.heartbeat()
                    await websocket.send(json.dumps({"type": "pong", "t": msg.get("t")}))
                elif kind == "system" and msg.get("action") == "pair":
                    ok, text = await self.controller.pair()
                    await websocket.send(json.dumps({"type": "info" if ok else "error", "message": text}))
                    await self.broadcast_status()
                elif kind == "system" and msg.get("action") == "capture_restart":
                    log.info("Capture-Card wird neu gestartet (angefordert von %s)", peer)
                    ok, text = await self.video.restart_capture()
                    await websocket.send(json.dumps({"type": "info" if ok else "error", "message": text}))
                    await self.broadcast_status()
                elif kind == "system" and msg.get("action") == "video_mode":
                    ok, text = self.video.set_mode(str(msg.get("mode") or ""))
                    await websocket.send(json.dumps({"type": "info" if ok else "error", "message": text}))
                    await self.broadcast_status()
        except ConnectionClosed:
            pass
        finally:
            self.clients.pop(websocket, None)
            if self.clients:
                self.apply_inputs()
            else:
                self.controller.release_all()
            log.info("Client getrennt: %s (%d aktiv)", peer, len(self.clients))

    async def broadcast_status(self):
        if not self.clients:
            return
        message = self.status_message()
        await asyncio.gather(*(ws.send(message) for ws in list(self.clients)), return_exceptions=True)

    async def status_loop(self):
        while True:
            await asyncio.sleep(1.0)
            await self.broadcast_status()

    # ------------------------------------------------------------------ Start / Stopp
    async def run(self):
        ws_cfg = self.cfg["websocket"]
        self.system = SystemStats()
        if not self.dry_run:
            await asyncio.get_running_loop().run_in_executor(None, tune, self.cfg.get("system"))
        await self.video.start()
        await self.controller.start()
        self.tasks = [asyncio.create_task(self.controller.run()), asyncio.create_task(self.status_loop()),
                      asyncio.create_task(self.channel_watch())]
        # Ohne Kompression: die Nachrichten sind winzig, Packen kostet nur Zeit
        async with serve(self.handle_client, ws_cfg["host"], int(ws_cfg["port"]),
                         max_size=MAX_MESSAGE, ping_interval=20, ping_timeout=20, compression=None):
            log.info("WebSocket-Server auf %s:%s", ws_cfg["host"], ws_cfg["port"])
            await self.stopping.wait()
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await self.controller.stop()
        await self.video.stop()
        log.info("Bridge beendet")


def main():
    parser = argparse.ArgumentParser(description="Raspberry Pi 5 Bridge für Game Remote")
    parser.add_argument("--config", help="Pfad zur config.yaml (Standard: /etc/switch2-bridge/config.yaml)")
    parser.add_argument("--dry-run", action="store_true", help="ohne Bluetooth-Controller (Testmodus)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        datefmt="%H:%M:%S", stream=sys.stdout)
    cfg = load_config(args.config)
    logging.getLogger().setLevel(str(cfg.get("log_level", "INFO")).upper())

    async def runner():
        bridge = Bridge(cfg, args.dry_run)
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, bridge.stopping.set)
            except (NotImplementedError, AttributeError, ValueError):
                pass                       # Windows: Strg+C beendet über KeyboardInterrupt
        await bridge.run()

    try:
        asyncio.run(runner())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
