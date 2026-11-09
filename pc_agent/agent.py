"""HA PC Agent – meldet diesen Windows-PC an das eigene Home Assistant.

Transparentes Selbst-Überwachungs-Werkzeug (wie HASS.Agent): sichtbares Tray-Icon,
Hinweis beim Start, alles abschaltbar. Verbindet sich per MQTT mit dem eigenen
Home Assistant, legt dort per MQTT-Discovery automatisch ein Gerät an mit:

  - Kamera: regelmäßiger Screenshot des Desktops
  - Sensoren: CPU, RAM, Akku, aktives Fenster, Betriebszeit
  - Schalter/Knöpfe: Herunterfahren, Neustart, Sperren, Energie sparen

Es läuft nur gegen das in config.json eingetragene Home Assistant. Nichts wird
an Dritte gesendet. Beenden jederzeit über das Tray-Icon.
"""
from __future__ import annotations

import base64
import ctypes
import io
import json
import logging
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import paho.mqtt.client as mqtt
import psutil
from PIL import Image, ImageDraw, ImageGrab

APP_NAME = "HA PC Agent"
DISCOVERY_PREFIX = "homeassistant"
log = logging.getLogger("pc-agent")


# --------------------------------------------------------------------------- Konfiguration
def config_path() -> Path:
    # Neben der Programmdatei, oder in %APPDATA%\HA-PC-Agent
    here = Path(__file__).with_name("config.json")
    if here.exists():
        return here
    appdata = Path(os.environ.get("APPDATA", str(Path.home()))) / "HA-PC-Agent"
    appdata.mkdir(parents=True, exist_ok=True)
    return appdata / "config.json"


def load_config() -> dict:
    p = config_path()
    if not p.exists():
        example = Path(__file__).with_name("config.example.json")
        if example.exists():
            p.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
        raise SystemExit(
            f"Keine Konfiguration gefunden. Bitte Vorlage ausfüllen:\n  {p}\n"
            "MQTT-Host, Benutzer und Passwort eintragen, dann neu starten."
        )
    cfg = json.loads(p.read_text(encoding="utf-8"))
    cfg.setdefault("screenshot", {})
    return cfg


# --------------------------------------------------------------------------- Windows-Aktionen
def _is_windows() -> bool:
    return os.name == "nt"


def do_lock():
    if _is_windows():
        ctypes.windll.user32.LockWorkStation()


def do_sleep():
    if _is_windows():
        # Energie sparen (kein Ruhezustand): SetSuspendState(Hibernate=0, Force=0, WakeupEventsDisabled=0)
        ctypes.windll.powrprof.SetSuspendState(0, 0, 0)


def do_shutdown():
    if _is_windows():
        subprocess.run(["shutdown", "/s", "/t", "5"], check=False)


def do_restart():
    if _is_windows():
        subprocess.run(["shutdown", "/r", "/t", "5"], check=False)


def do_cancel_shutdown():
    if _is_windows():
        subprocess.run(["shutdown", "/a"], check=False)


def active_window_title() -> str:
    if not _is_windows():
        return ""
    try:
        u = ctypes.windll.user32
        hwnd = u.GetForegroundWindow()
        length = u.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        u.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value[:255]
    except Exception:
        return ""


def notify(title: str, message: str):
    """Kurze Windows-Benachrichtigung (ohne Zusatzpakete, best effort)."""
    log.info("%s: %s", title, message)


# --------------------------------------------------------------------------- Screenshot
def grab_screenshot(opts: dict) -> bytes | None:
    try:
        img = ImageGrab.grab(all_screens=bool(opts.get("all_monitors", True)))
    except Exception:
        log.exception("Screenshot fehlgeschlagen")
        return None
    max_w = int(opts.get("max_width", 1280))
    if max_w and img.width > max_w:
        h = int(img.height * max_w / img.width)
        img = img.resize((max_w, h), Image.LANCZOS)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=int(opts.get("jpeg_quality", 55)))
    return buf.getvalue()


# --------------------------------------------------------------------------- Sensoren
def read_sensors() -> dict:
    data = {
        "cpu_percent": round(psutil.cpu_percent(interval=None), 1),
        "ram_percent": round(psutil.virtual_memory().percent, 1),
        "active_window": active_window_title() or "—",
        "uptime_hours": round((time.time() - psutil.boot_time()) / 3600, 1),
    }
    bat = psutil.sensors_battery()
    if bat is not None:
        data["battery_percent"] = round(bat.percent, 0)
        data["battery_charging"] = "Ja" if bat.power_plugged else "Nein"
    return data


# --------------------------------------------------------------------------- MQTT-Agent
class Agent:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        name = cfg.get("device_name") or socket.gethostname()
        self.node_id = "pc_" + "".join(c if c.isalnum() else "_" for c in name.lower())
        self.base = f"ha_pc_agent/{self.node_id}"
        self.avail_topic = f"{self.base}/availability"
        self.device_name = name
        self.screenshots_paused = False
        self._stop = threading.Event()

        m = cfg.get("mqtt", {})
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=self.node_id)
        if m.get("username"):
            self.client.username_pw_set(m["username"], m.get("password", ""))
        self.client.will_set(self.avail_topic, "offline", retain=True)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self._host = m.get("host", "127.0.0.1")
        self._port = int(m.get("port", 1883))

    # ---- Discovery --------------------------------------------------------
    def _device_block(self) -> dict:
        return {
            "identifiers": [self.node_id],
            "name": self.device_name,
            "manufacturer": "HA PC Agent",
            "model": "Windows PC",
            "sw_version": "1.0.0",
        }

    def _publish_discovery(self):
        dev = self._device_block()
        avail = [{"topic": self.avail_topic, "payload_available": "online",
                  "payload_not_available": "offline"}]

        def cfg_topic(component, obj):
            return f"{DISCOVERY_PREFIX}/{component}/{self.node_id}/{obj}/config"

        def base_entity(obj, name):
            return {"unique_id": f"{self.node_id}_{obj}", "object_id": f"{self.node_id}_{obj}",
                    "name": name, "availability": avail, "device": dev}

        entities = []

        # Sensoren – Akku nur, wenn vorhanden (Laptop)
        has_battery = False
        try:
            has_battery = psutil.sensors_battery() is not None
        except Exception:
            pass
        sensors = [
            ("cpu", "CPU-Auslastung", "%", "mdi:chip", None),
            ("ram", "RAM-Auslastung", "%", "mdi:memory", None),
            ("active_window", "Aktives Fenster", None, "mdi:window-restore", None),
            ("uptime", "Betriebszeit", "h", "mdi:clock-outline", None),
        ]
        if has_battery:
            sensors += [
                ("battery", "Akku", "%", None, "battery"),
                ("battery_charging", "Lädt", None, "mdi:power-plug", None),
            ]
        for obj, name, unit, icon, dclass in sensors:
            e = base_entity(obj, name)
            e["state_topic"] = f"{self.base}/sensor/{obj}"
            if unit:
                e["unit_of_measurement"] = unit
            if icon:
                e["icon"] = icon
            if dclass:
                e["device_class"] = dclass
            entities.append(("sensor", obj, e))

        # Kamera (Screenshot)
        if self.cfg.get("screenshot", {}).get("enabled", True):
            cam = base_entity("screen", "Bildschirm")
            cam["topic"] = f"{self.base}/camera/screen"
            entities.append(("camera", "screen", cam))

            sw = base_entity("screenshots", "Screenshots senden")
            sw["state_topic"] = f"{self.base}/switch/screenshots/state"
            sw["command_topic"] = f"{self.base}/switch/screenshots/set"
            sw["icon"] = "mdi:monitor-screenshot"
            entities.append(("switch", "screenshots", sw))

        # Power-Knöpfe
        if self.cfg.get("allow_power_commands", True):
            buttons = [
                ("shutdown", "Herunterfahren", "mdi:power"),
                ("restart", "Neustart", "mdi:restart"),
                ("lock", "Sperren", "mdi:lock"),
                ("sleep", "Energie sparen", "mdi:sleep"),
                ("cancel_shutdown", "Herunterfahren abbrechen", "mdi:close-octagon"),
            ]
            for obj, name, icon in buttons:
                b = base_entity(obj, name)
                b["command_topic"] = f"{self.base}/button/{obj}"
                b["icon"] = icon
                entities.append(("button", obj, b))

        for component, obj, payload in entities:
            self.client.publish(cfg_topic(component, obj), json.dumps(payload), retain=True)
        log.info("%d Entitäten per Discovery angemeldet", len(entities))

    # ---- Callbacks --------------------------------------------------------
    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code != 0:
            log.error("MQTT-Verbindung abgelehnt: %s", reason_code)
            return
        log.info("Mit MQTT verbunden (%s:%s)", self._host, self._port)
        client.publish(self.avail_topic, "online", retain=True)
        self._publish_discovery()
        for obj in ("shutdown", "restart", "lock", "sleep", "cancel_shutdown"):
            client.subscribe(f"{self.base}/button/{obj}")
        client.subscribe(f"{self.base}/switch/screenshots/set")
        client.publish(f"{self.base}/switch/screenshots/state",
                       "OFF" if self.screenshots_paused else "ON", retain=True)

    def _on_message(self, client, userdata, msg):
        topic, payload = msg.topic, msg.payload.decode(errors="replace").strip()
        log.info("Befehl empfangen: %s %s", topic, payload)
        if topic.endswith("/switch/screenshots/set"):
            self.screenshots_paused = payload.upper() == "OFF"
            client.publish(f"{self.base}/switch/screenshots/state",
                           "OFF" if self.screenshots_paused else "ON", retain=True)
            return
        if not self.cfg.get("allow_power_commands", True):
            return
        action = topic.rsplit("/", 1)[-1]
        {"shutdown": do_shutdown, "restart": do_restart, "lock": do_lock,
         "sleep": do_sleep, "cancel_shutdown": do_cancel_shutdown}.get(action, lambda: None)()

    # ---- Schleifen --------------------------------------------------------
    def _sensor_loop(self):
        interval = int(self.cfg.get("sensors_interval_seconds", 15))
        psutil.cpu_percent(interval=None)  # erster Aufruf grundiert die Messung
        while not self._stop.wait(2):
            try:
                for key, val in read_sensors().items():
                    obj = {"cpu_percent": "cpu", "ram_percent": "ram", "battery_percent": "battery",
                           "battery_charging": "battery_charging", "active_window": "active_window",
                           "uptime_hours": "uptime"}.get(key, key)
                    self.client.publish(f"{self.base}/sensor/{obj}", str(val))
            except Exception:
                log.exception("Sensoren")
            self._stop.wait(max(1, interval - 2))

    def _screenshot_loop(self):
        sc = self.cfg.get("screenshot", {})
        if not sc.get("enabled", True):
            return
        interval = int(sc.get("interval_seconds", 10))
        while not self._stop.wait(interval):
            if self.screenshots_paused:
                continue
            data = grab_screenshot(sc)
            if data:
                self.client.publish(f"{self.base}/camera/screen", data)

    # ---- Start/Stop -------------------------------------------------------
    def start(self):
        self.client.connect_async(self._host, self._port, keepalive=30)
        self.client.loop_start()
        threading.Thread(target=self._sensor_loop, daemon=True).start()
        threading.Thread(target=self._screenshot_loop, daemon=True).start()

    def stop(self):
        self._stop.set()
        try:
            self.client.publish(self.avail_topic, "offline", retain=True)
            time.sleep(0.3)
            self.client.loop_stop()
            self.client.disconnect()
        except Exception:
            pass


# --------------------------------------------------------------------------- Tray-Icon
def make_icon_image():
    img = Image.new("RGB", (64, 64), (38, 38, 36))
    d = ImageDraw.Draw(img)
    d.rectangle((12, 16, 52, 44), outline=(217, 119, 87), width=3)
    d.line((26, 48, 38, 48), fill=(217, 119, 87), width=3)
    d.ellipse((28, 26, 36, 34), fill=(217, 119, 87))
    return img


def run_tray(agent: Agent):
    try:
        import pystray
    except Exception:
        # Ohne Tray einfach laufen lassen
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            agent.stop()
        return

    def on_toggle(icon, item):
        agent.screenshots_paused = not agent.screenshots_paused
        agent.client.publish(f"{agent.base}/switch/screenshots/state",
                             "OFF" if agent.screenshots_paused else "ON", retain=True)

    def on_open_config(icon, item):
        if _is_windows():
            os.startfile(str(config_path()))  # noqa: S606

    def on_quit(icon, item):
        agent.stop()
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem(lambda item: f"Gerät: {agent.device_name}", None, enabled=False),
        pystray.MenuItem(
            lambda item: "Screenshots: pausiert" if agent.screenshots_paused else "Screenshots: aktiv",
            on_toggle),
        pystray.MenuItem("Konfiguration öffnen", on_open_config),
        pystray.MenuItem("Beenden", on_quit),
    )
    icon = pystray.Icon(APP_NAME, make_icon_image(), APP_NAME, menu)
    icon.run()


# --------------------------------------------------------------------------- main
def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    cfg = load_config()
    agent = Agent(cfg)
    notify(APP_NAME, f"Verbinde mit {cfg.get('mqtt', {}).get('host')} …")
    agent.start()
    run_tray(agent)


if __name__ == "__main__":
    main()
