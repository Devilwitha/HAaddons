"""HA PC Agent – meldet diesen PC an das eigene Home Assistant.

Transparentes Selbst-Überwachungs-Werkzeug (wie HASS.Agent): sichtbares Tray-Icon,
Einstellungsfenster, alles abschaltbar. Verbindet sich per MQTT mit dem eigenen
Home Assistant und legt dort per MQTT-Discovery automatisch ein Gerät an mit:

  - Kamera: regelmäßiger Screenshot des Desktops
  - Sensoren: CPU, RAM, aktives Fenster, Betriebszeit (Akku nur bei Laptops)
  - Schalter/Knöpfe: Herunterfahren, Neustart, Sperren, Energie sparen
  - Medien/Ton, Programme & Spiele

Läuft unter Windows und Linux (inkl. Steam Deck / SteamOS und Bazzite). Es läuft
nur gegen das in den Einstellungen eingetragene Home Assistant. Nichts wird an
Dritte gesendet.
"""
from __future__ import annotations

import io
import json
import logging
import logging.handlers
import os
import socket
import sys
import threading
import time
from pathlib import Path

import paho.mqtt.client as mqtt
import psutil
from PIL import Image, ImageDraw

import osbackend as osb
import updater
from version import __version__ as APP_VERSION

APP_NAME = "HA PC Agent"
DISCOVERY_PREFIX = "homeassistant"
MAX_LAUNCHERS = 32
log = logging.getLogger("pc-agent")

DEFAULT_CONFIG = {
    "mqtt": {"host": "", "port": 1883, "username": "", "password": ""},
    "device_name": "",
    "screenshot": {"enabled": True, "interval_seconds": 10, "max_width": 1280,
                   "jpeg_quality": 55, "all_monitors": True},
    "sensors_interval_seconds": 15,
    "allow_power_commands": True,
    "media_controls": True,
    "auto_update": True,
    "launchers": [],
}


# --------------------------------------------------------------------------- Pfade
def config_path() -> Path:
    return osb.data_dir() / "config.json"


def resource_path(name: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return base / name


# --------------------------------------------------------------------------- Konfiguration
def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = _merge(base.get(k, {}), v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


def load_config() -> dict:
    p = config_path()
    if p.exists():
        try:
            return _merge(DEFAULT_CONFIG, json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            log.exception("config.json unlesbar – nutze Standardwerte")
    return _merge(DEFAULT_CONFIG, {"device_name": socket.gethostname()})


def save_config(cfg: dict):
    config_path().write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")


def config_is_valid(cfg: dict) -> bool:
    return bool(cfg.get("mqtt", {}).get("host"))


# --------------------------------------------------------------------------- Screenshot / Sensoren
def grab_screenshot(opts: dict) -> bytes | None:
    img = osb.capture_image(bool(opts.get("all_monitors", True)))
    if img is None:
        return None
    max_w = int(opts.get("max_width", 1280))
    if max_w and img.width > max_w:
        h = int(img.height * max_w / img.width)
        img = img.resize((max_w, h), Image.LANCZOS)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=int(opts.get("jpeg_quality", 55)))
    return buf.getvalue()


def read_sensors() -> dict:
    data = {
        "cpu": round(psutil.cpu_percent(interval=None), 1),
        "ram": round(psutil.virtual_memory().percent, 1),
        "active_window": osb.active_window_title() or "—",
        "uptime": round((time.time() - psutil.boot_time()) / 3600, 1),
    }
    try:
        bat = psutil.sensors_battery()
    except Exception:
        bat = None
    if bat is not None:
        data["battery"] = round(bat.percent, 0)
        data["battery_charging"] = "Ja" if bat.power_plugged else "Nein"
    temp = osb.read_temperature()
    if temp is not None:
        data["temperature"] = temp
    return data


def has_battery() -> bool:
    try:
        return psutil.sensors_battery() is not None
    except Exception:
        return False


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
        self.status = "getrennt"
        self._stop = threading.Event()
        self._media_keys = cfg.get("media_controls", True) and osb.media_controls_available()
        self._audio = cfg.get("media_controls", True) and osb.AUDIO_AVAILABLE
        self._has_temp = osb.read_temperature() is not None

        m = cfg.get("mqtt", {})
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=self.node_id)
        if m.get("username"):
            self.client.username_pw_set(m["username"], m.get("password", ""))
        self.client.will_set(self.avail_topic, "offline", retain=True)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        self._host = m.get("host", "127.0.0.1")
        self._port = int(m.get("port", 1883))

    def _device_block(self) -> dict:
        return {"identifiers": [self.node_id], "name": self.device_name,
                "manufacturer": "HA PC Agent", "model": osb.os_model(), "sw_version": APP_VERSION}

    def _publish_discovery(self):
        dev = self._device_block()
        avail = [{"topic": self.avail_topic, "payload_available": "online",
                  "payload_not_available": "offline"}]

        def base_entity(obj, name):
            return {"unique_id": f"{self.node_id}_{obj}", "object_id": f"{self.node_id}_{obj}",
                    "name": name, "availability": avail, "device": dev}

        entities = []
        sensors = [("cpu", "CPU-Auslastung", "%", "mdi:chip", None),
                   ("ram", "RAM-Auslastung", "%", "mdi:memory", None),
                   ("active_window", "Aktives Fenster", None, "mdi:window-restore", None),
                   ("uptime", "Betriebszeit", "h", "mdi:clock-outline", None)]
        if self._has_temp:
            sensors += [("temperature", "Temperatur", "°C", "mdi:thermometer", "temperature")]
        if has_battery():
            sensors += [("battery", "Akku", "%", None, "battery"),
                        ("battery_charging", "Lädt", None, "mdi:power-plug", None)]
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

        if self.cfg.get("screenshot", {}).get("enabled", True):
            cam = base_entity("screen", "Bildschirm")
            cam["topic"] = f"{self.base}/camera/screen"
            entities.append(("camera", "screen", cam))
            sw = base_entity("screenshots", "Screenshots senden")
            sw["state_topic"] = f"{self.base}/switch/screenshots/state"
            sw["command_topic"] = f"{self.base}/switch/screenshots/set"
            sw["icon"] = "mdi:monitor-screenshot"
            entities.append(("switch", "screenshots", sw))

        if self.cfg.get("allow_power_commands", True):
            for obj, name, icon in [("shutdown", "Herunterfahren", "mdi:power"),
                                    ("restart", "Neustart", "mdi:restart"),
                                    ("lock", "Sperren", "mdi:lock"),
                                    ("sleep", "Energie sparen", "mdi:sleep"),
                                    ("cancel_shutdown", "Herunterfahren abbrechen", "mdi:close-octagon")]:
                b = base_entity(obj, name)
                b["command_topic"] = f"{self.base}/button/{obj}"
                b["icon"] = icon
                entities.append(("button", obj, b))

        if self._media_keys:
            for obj, name, icon in [("media_play_pause", "Wiedergabe/Pause", "mdi:play-pause"),
                                    ("media_next", "Nächster Titel", "mdi:skip-next"),
                                    ("media_prev", "Vorheriger Titel", "mdi:skip-previous")]:
                b = base_entity(obj, name)
                b["command_topic"] = f"{self.base}/button/{obj}"
                b["icon"] = icon
                entities.append(("button", obj, b))
        if self._audio:
            n = base_entity("volume", "Lautstärke")
            n["state_topic"] = f"{self.base}/number/volume/state"
            n["command_topic"] = f"{self.base}/number/volume/set"
            n.update({"min": 0, "max": 100, "step": 1, "mode": "slider",
                      "unit_of_measurement": "%", "icon": "mdi:volume-high"})
            entities.append(("number", "volume", n))
            for obj, name, icon in [("mute", "Ton stumm", "mdi:volume-mute"),
                                    ("mic_mute", "Mikrofon stumm", "mdi:microphone-off")]:
                s = base_entity(obj, name)
                s["state_topic"] = f"{self.base}/switch/{obj}/state"
                s["command_topic"] = f"{self.base}/switch/{obj}/set"
                s["icon"] = icon
                entities.append(("switch", obj, s))

        # Programme / Spiele – entfernte zuerst löschen, dann aktuelle anlegen
        launchers = (self.cfg.get("launchers") or [])[:MAX_LAUNCHERS]
        for i in range(MAX_LAUNCHERS):
            self.client.publish(f"{DISCOVERY_PREFIX}/button/{self.node_id}/launch_{i}/config",
                                "", retain=True)
        for i, item in enumerate(launchers):
            if not item.get("target"):
                continue
            b = base_entity(f"launch_{i}", item.get("name") or f"Programm {i + 1}")
            b["command_topic"] = f"{self.base}/button/launch_{i}"
            b["icon"] = "mdi:steam" if str(item.get("target", "")).lower().startswith("steam://") else "mdi:rocket-launch"
            entities.append(("button", f"launch_{i}", b))

        for component, obj, payload in entities:
            self.client.publish(f"{DISCOVERY_PREFIX}/{component}/{self.node_id}/{obj}/config",
                                json.dumps(payload), retain=True)
        log.info("%d Entitäten per Discovery angemeldet", len(entities))

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code != 0:
            self.status = f"abgelehnt ({reason_code})"
            log.error("MQTT-Verbindung abgelehnt: %s", reason_code)
            return
        self.status = "verbunden"
        log.info("Mit MQTT verbunden (%s:%s)", self._host, self._port)
        client.publish(self.avail_topic, "online", retain=True)
        self._publish_discovery()
        for obj in ("shutdown", "restart", "lock", "sleep", "cancel_shutdown"):
            client.subscribe(f"{self.base}/button/{obj}")
        client.subscribe(f"{self.base}/switch/screenshots/set")
        client.publish(f"{self.base}/switch/screenshots/state",
                       "OFF" if self.screenshots_paused else "ON", retain=True)
        if self._media_keys:
            for obj in ("media_play_pause", "media_next", "media_prev"):
                client.subscribe(f"{self.base}/button/{obj}")
        if self._audio:
            client.subscribe(f"{self.base}/number/volume/set")
            client.subscribe(f"{self.base}/switch/mute/set")
            client.subscribe(f"{self.base}/switch/mic_mute/set")
            self._publish_audio_state()
        for i in range(len(self.cfg.get("launchers") or [])):
            client.subscribe(f"{self.base}/button/launch_{i}")

    def _publish_audio_state(self):
        if not self._audio:
            return
        try:
            self.client.publish(f"{self.base}/number/volume/state", str(osb.get_volume()))
            self.client.publish(f"{self.base}/switch/mute/state", "ON" if osb.get_mute() else "OFF")
            self.client.publish(f"{self.base}/switch/mic_mute/state", "ON" if osb.get_mic_mute() else "OFF")
        except Exception:
            log.exception("Audio-Status")

    def _on_disconnect(self, client, userdata, *args):
        if not self._stop.is_set():
            self.status = "verbindet"

    def _on_message(self, client, userdata, msg):
        topic = msg.topic
        payload = msg.payload.decode(errors="replace").strip()
        log.info("Befehl: %s %s", topic, payload)
        if topic.endswith("/switch/screenshots/set"):
            self.screenshots_paused = payload.upper() == "OFF"
            client.publish(f"{self.base}/switch/screenshots/state",
                           "OFF" if self.screenshots_paused else "ON", retain=True)
            return
        # Medien / Lautstärke
        if topic.endswith("/number/volume/set"):
            try:
                osb.set_volume(int(float(payload)))
            except Exception:
                log.exception("Lautstärke")
            self._publish_audio_state()
            return
        if topic.endswith("/switch/mute/set"):
            osb.set_mute(payload.upper() == "ON")
            self._publish_audio_state()
            return
        if topic.endswith("/switch/mic_mute/set"):
            osb.set_mic_mute(payload.upper() == "ON")
            self._publish_audio_state()
            return
        if "/button/media_" in topic:
            osb.media_key(topic.rsplit("/button/media_", 1)[-1])
            return
        if "/button/launch_" in topic:
            try:
                idx = int(topic.rsplit("/button/launch_", 1)[-1])
            except ValueError:
                return
            ls = self.cfg.get("launchers") or []
            if 0 <= idx < len(ls):
                osb.run_launcher(ls[idx].get("target"))
            return
        if not self.cfg.get("allow_power_commands", True):
            return
        action = topic.rsplit("/", 1)[-1]
        {"shutdown": osb.do_shutdown, "restart": osb.do_restart, "lock": osb.do_lock,
         "sleep": osb.do_sleep, "cancel_shutdown": osb.do_cancel_shutdown}.get(action, lambda: None)()

    def _sensor_loop(self):
        interval = int(self.cfg.get("sensors_interval_seconds", 15))
        psutil.cpu_percent(interval=None)
        while not self._stop.wait(2):
            try:
                for obj, val in read_sensors().items():
                    self.client.publish(f"{self.base}/sensor/{obj}", str(val))
                self._publish_audio_state()
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

    def start(self):
        self.status = "verbindet"
        self.client.connect_async(self._host, self._port, keepalive=30)
        self.client.loop_start()
        threading.Thread(target=self._sensor_loop, daemon=True).start()
        threading.Thread(target=self._screenshot_loop, daemon=True).start()

    def stop(self):
        self._stop.set()
        self.status = "getrennt"
        try:
            self.client.publish(self.avail_topic, "offline", retain=True)
            time.sleep(0.2)
            self.client.loop_stop()
            self.client.disconnect()
        except Exception:
            pass


def test_connection(mqtt_cfg: dict, timeout: float = 8) -> tuple[bool, str]:
    """Kurzer Verbindungstest. Gibt (ok, Meldung) zurück."""
    result: dict = {}
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="pc_agent_test")
    if mqtt_cfg.get("username"):
        c.username_pw_set(mqtt_cfg["username"], mqtt_cfg.get("password", ""))

    def on_connect(cl, u, f, rc, props=None):
        result["rc"] = rc
        cl.disconnect()

    c.on_connect = on_connect
    try:
        c.connect(mqtt_cfg.get("host", ""), int(mqtt_cfg.get("port", 1883)), int(timeout))
    except Exception as e:
        return False, f"Keine Verbindung: {e}"
    c.loop_start()
    t0 = time.time()
    while "rc" not in result and time.time() - t0 < timeout:
        time.sleep(0.1)
    c.loop_stop()
    if "rc" not in result:
        return False, "Zeitüberschreitung – Host/Port prüfen."
    rc = result["rc"]
    ok = getattr(rc, "value", rc) == 0
    return (True, "Verbindung erfolgreich.") if ok else (False, f"Abgelehnt: {rc} (Benutzer/Passwort?)")


# --------------------------------------------------------------------------- Manager
class Manager:
    def __init__(self):
        self.agent: Agent | None = None

    def restart(self):
        self.stop()
        cfg = load_config()
        if not config_is_valid(cfg):
            log.info("Keine gültige Konfiguration – Agent nicht gestartet.")
            return
        self.agent = Agent(cfg)
        self.agent.start()

    def stop(self):
        if self.agent:
            self.agent.stop()
            self.agent = None

    @property
    def status(self) -> str:
        return self.agent.status if self.agent else "nicht eingerichtet"

    @property
    def paused(self) -> bool:
        return bool(self.agent and self.agent.screenshots_paused)

    def set_paused(self, paused: bool):
        if self.agent:
            self.agent.screenshots_paused = paused
            self.agent.client.publish(f"{self.agent.base}/switch/screenshots/state",
                                      "OFF" if paused else "ON", retain=True)


# --------------------------------------------------------------------------- Tray-Icon
def tray_image():
    ico = resource_path("icon.ico")
    if ico.exists():
        try:
            return Image.open(ico)
        except Exception:
            pass
    img = Image.new("RGB", (64, 64), (38, 38, 36))
    d = ImageDraw.Draw(img)
    d.rectangle((12, 16, 52, 44), outline=(217, 119, 87), width=3)
    d.line((26, 48, 38, 48), fill=(217, 119, 87), width=3)
    d.ellipse((28, 26, 36, 34), fill=(217, 119, 87))
    return img


# --------------------------------------------------------------------------- GUI (tkinter)
def run_gui(manager: Manager):
    import tkinter as tk
    from tkinter import ttk, messagebox

    manager.restart()

    root = tk.Tk()
    root.title(APP_NAME)
    root.minsize(440, 680)
    try:
        root.iconbitmap(str(resource_path("icon.ico")))
    except Exception:
        pass

    cfg = load_config()
    mq = cfg["mqtt"]
    sc = cfg["screenshot"]
    V = {
        "host": tk.StringVar(value=mq.get("host", "")),
        "port": tk.StringVar(value=str(mq.get("port", 1883))),
        "user": tk.StringVar(value=mq.get("username", "")),
        "pass": tk.StringVar(value=mq.get("password", "")),
        "name": tk.StringVar(value=cfg.get("device_name", "")),
        "shot_on": tk.BooleanVar(value=sc.get("enabled", True)),
        "interval": tk.StringVar(value=str(sc.get("interval_seconds", 10))),
        "width": tk.StringVar(value=str(sc.get("max_width", 1280))),
        "quality": tk.StringVar(value=str(sc.get("jpeg_quality", 55))),
        "sens": tk.StringVar(value=str(cfg.get("sensors_interval_seconds", 15))),
        "power": tk.BooleanVar(value=cfg.get("allow_power_commands", True)),
        "media": tk.BooleanVar(value=cfg.get("media_controls", True)),
        "autostart": tk.BooleanVar(value=osb.autostart_enabled()),
        "auto_update": tk.BooleanVar(value=cfg.get("auto_update", True)),
    }

    pad = {"padx": 10, "pady": 4}
    frm = ttk.Frame(root, padding=14)
    frm.pack(fill="both", expand=True)
    frm.columnconfigure(1, weight=1)
    row = 0

    def section(title):
        nonlocal row
        ttk.Label(frm, text=title, font=("Segoe UI", 10, "bold")).grid(
            row=row, column=0, columnspan=3, sticky="w", pady=(12, 2))
        row += 1

    def field(label, var, show=None, width=22):
        nonlocal row
        ttk.Label(frm, text=label).grid(row=row, column=0, sticky="w", **pad)
        e = ttk.Entry(frm, textvariable=var, show=show, width=width)
        e.grid(row=row, column=1, columnspan=2, sticky="ew", **pad)
        row += 1
        return e

    section("Home Assistant (MQTT)")
    field("Host / IP", V["host"])
    field("Port", V["port"], width=8)
    field("Benutzer", V["user"])
    pass_entry = field("Passwort", V["pass"], show="•")
    show_pw = tk.BooleanVar(value=False)
    ttk.Checkbutton(frm, text="Passwort anzeigen", variable=show_pw,
                    command=lambda: pass_entry.config(show="" if show_pw.get() else "•")
                    ).grid(row=row, column=1, sticky="w", **pad)
    row += 1

    section("Gerät")
    field("Name in HA", V["name"])

    section("Screenshots")
    ttk.Checkbutton(frm, text="Screenshots an HA senden", variable=V["shot_on"]
                    ).grid(row=row, column=0, columnspan=2, sticky="w", **pad)
    row += 1
    field("Takt (Sekunden)", V["interval"], width=8)
    field("Max. Breite (px)", V["width"], width=8)
    field("JPEG-Qualität (1–95)", V["quality"], width=8)
    if not osb.screenshot_available():
        ttk.Label(frm, text="Hinweis: kein Screenshot-Werkzeug gefunden "
                            "(z. B. spectacle/grim installieren).", foreground="#c96442"
                  ).grid(row=row, column=0, columnspan=3, sticky="w", **pad)
        row += 1

    section("Weiteres")
    field("Sensoren-Takt (Sekunden)", V["sens"], width=8)
    ttk.Checkbutton(frm, text="Herunterfahren / Neustart / Sperren erlauben", variable=V["power"]
                    ).grid(row=row, column=0, columnspan=3, sticky="w", **pad)
    row += 1
    media_text = "Medientasten & Lautstärke steuern"
    missing = []
    if not osb.AUDIO_AVAILABLE:
        missing.append("Lautstärke/Mikrofon")
    if not osb.media_controls_available():
        missing.append("Medientasten")
    if missing:
        media_text += f"  ({' & '.join(missing)} nicht verfügbar)"
    ttk.Checkbutton(frm, text=media_text, variable=V["media"]
                    ).grid(row=row, column=0, columnspan=3, sticky="w", **pad)
    row += 1
    autostart_text = ("Automatisch mit Windows starten" if osb.IS_WINDOWS
                      else "Automatisch beim Anmelden starten")
    ttk.Checkbutton(frm, text=autostart_text, variable=V["autostart"]
                    ).grid(row=row, column=0, columnspan=3, sticky="w", **pad)
    row += 1
    upd_text = "Automatisch über GitHub aktualisieren"
    if not osb.is_frozen():
        upd_text += "  (nur in der .exe)"
    ttk.Checkbutton(frm, text=upd_text, variable=V["auto_update"]
                    ).grid(row=row, column=0, columnspan=2, sticky="w", **pad)

    def on_check_update():
        status_var.set("Suche nach Update …")

        def work():
            try:
                res = updater.maybe_update(APP_VERSION, apply=osb.is_frozen(),
                                           on_apply=manager.stop)
                if res == "applying":
                    root.after(0, lambda: (status_var.set("Update wird installiert – Neustart …")))
                    time.sleep(0.5)
                    os._exit(0)
                elif res:
                    root.after(0, lambda: messagebox.showinfo(
                        APP_NAME, f"Update verfügbar: {res}\n(wird in der .exe automatisch installiert)"))
                    root.after(0, lambda: status_var.set(f"Update verfügbar: {res}"))
                else:
                    root.after(0, lambda: (status_var.set(f"Aktuell ({APP_VERSION})."),
                                           messagebox.showinfo(APP_NAME, f"Du hast die neueste Version ({APP_VERSION}).")))
            except Exception as e:
                root.after(0, lambda: status_var.set(f"Update-Prüfung fehlgeschlagen: {e}"))
        threading.Thread(target=work, daemon=True).start()

    ttk.Button(frm, text="Update suchen", command=on_check_update).grid(
        row=row, column=2, sticky="e", **pad)
    row += 1

    # --- Programme & Spiele -----------------------------------------------
    section("Programme & Spiele (je ein Button in HA)")
    launchers = [dict(x) for x in (cfg.get("launchers") or [])]
    lb = tk.Listbox(frm, height=5, activestyle="none")
    lb.grid(row=row, column=0, columnspan=3, sticky="ew", padx=10, pady=(0, 4))
    row += 1

    def refresh_lb():
        lb.delete(0, "end")
        for it in launchers:
            steam = str(it.get("target", "")).lower().startswith("steam://")
            lb.insert("end", ("🎮 " if steam else "▶ ") + (it.get("name") or it.get("target", "")))

    def add_file():
        from tkinter import filedialog
        if osb.IS_WINDOWS:
            ftypes = [("Programme", "*.exe *.lnk *.bat *.cmd"), ("Alle Dateien", "*.*")]
        else:
            ftypes = [("Programme/Dateien", "*.desktop *.sh *.AppImage"), ("Alle Dateien", "*.*")]
        p = filedialog.askopenfilename(title="Programm oder Datei wählen", filetypes=ftypes)
        if p:
            launchers.append({"name": Path(p).stem, "target": p})
            refresh_lb()

    def open_steam_dialog():
        from tkinter import simpledialog
        games = osb.steam_games()
        if not games:
            appid = simpledialog.askstring(
                APP_NAME, "Keine installierten Steam-Spiele gefunden.\nSteam-AppID von Hand eingeben:",
                parent=root)
            if appid and appid.strip().isdigit():
                launchers.append({"name": f"Steam {appid.strip()}",
                                  "target": f"steam://rungameid/{appid.strip()}"})
                refresh_lb()
            return
        dlg = tk.Toplevel(root)
        dlg.title("Steam-Spiel wählen")
        dlg.transient(root)
        dlg.grab_set()
        dlg.geometry("360x430")
        ttk.Label(dlg, text="Suchen:").pack(anchor="w", padx=10, pady=(10, 2))
        search = tk.StringVar()
        ent = ttk.Entry(dlg, textvariable=search)
        ent.pack(fill="x", padx=10)
        lst = tk.Listbox(dlg)
        lst.pack(fill="both", expand=True, padx=10, pady=8)
        view: list[dict] = []

        def fill(*_):
            q = search.get().lower()
            lst.delete(0, "end")
            view.clear()
            for g in games:
                if q in g["name"].lower():
                    view.append(g)
                    lst.insert("end", g["name"])

        def choose(*_):
            sel = lst.curselection()
            if not sel:
                return
            g = view[sel[0]]
            launchers.append({"name": g["name"], "target": f"steam://rungameid/{g['appid']}"})
            refresh_lb()
            dlg.destroy()

        fill()
        search.trace_add("write", fill)
        lst.bind("<Double-Button-1>", choose)
        bf = ttk.Frame(dlg)
        bf.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Button(bf, text="Hinzufügen", command=choose).pack(side="right")
        ttk.Button(bf, text="Abbrechen", command=dlg.destroy).pack(side="right", padx=6)
        ent.focus_set()

    def rename_sel():
        from tkinter import simpledialog
        sel = lb.curselection()
        if not sel:
            return
        cur = launchers[sel[0]]
        new = simpledialog.askstring(APP_NAME, "Anzeigename:", initialvalue=cur.get("name", ""), parent=root)
        if new:
            cur["name"] = new
            refresh_lb()

    def remove_sel():
        sel = lb.curselection()
        if sel:
            launchers.pop(sel[0])
            refresh_lb()

    refresh_lb()
    lbtn = ttk.Frame(frm)
    lbtn.grid(row=row, column=0, columnspan=3, sticky="ew", padx=10)
    row += 1
    ttk.Button(lbtn, text="Programm/Datei …", command=add_file).pack(side="left")
    ttk.Button(lbtn, text="Steam-Spiel …", command=open_steam_dialog).pack(side="left", padx=6)
    ttk.Button(lbtn, text="Umbenennen", command=rename_sel).pack(side="left")
    ttk.Button(lbtn, text="Entfernen", command=remove_sel).pack(side="right")

    status_var = tk.StringVar(value="")
    ttk.Label(frm, textvariable=status_var, foreground="#c96442").grid(
        row=row, column=0, columnspan=3, sticky="w", pady=(12, 2))
    row += 1

    def collect() -> dict:
        def num(s, d):
            try:
                return int(float(s))
            except Exception:
                return d
        return {
            "mqtt": {"host": V["host"].get().strip(), "port": num(V["port"].get(), 1883),
                     "username": V["user"].get().strip(), "password": V["pass"].get()},
            "device_name": V["name"].get().strip() or socket.gethostname(),
            "screenshot": {"enabled": V["shot_on"].get(), "interval_seconds": num(V["interval"].get(), 10),
                           "max_width": num(V["width"].get(), 1280), "jpeg_quality": num(V["quality"].get(), 55),
                           "all_monitors": True},
            "sensors_interval_seconds": num(V["sens"].get(), 15),
            "allow_power_commands": V["power"].get(),
            "media_controls": V["media"].get(),
            "auto_update": V["auto_update"].get(),
            "launchers": launchers,
        }

    def on_test():
        status_var.set("Teste Verbindung …")

        def work():
            ok, msg = test_connection(collect()["mqtt"])
            root.after(0, lambda: (status_var.set(msg),
                                   messagebox.showinfo(APP_NAME, msg) if ok
                                   else messagebox.showwarning(APP_NAME, msg)))
        threading.Thread(target=work, daemon=True).start()

    def on_save():
        new = collect()
        if not new["mqtt"]["host"]:
            messagebox.showwarning(APP_NAME, "Bitte Host / IP des Home Assistant eintragen.")
            return
        save_config(new)
        try:
            osb.set_autostart(V["autostart"].get())
        except Exception:
            log.exception("Autostart setzen fehlgeschlagen")
        manager.restart()
        status_var.set("Gespeichert. Agent neu verbunden.")

    btns = ttk.Frame(frm)
    btns.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(10, 0))
    ttk.Button(btns, text="Verbindung testen", command=on_test).pack(side="left")
    ttk.Button(btns, text="In Infobereich", command=lambda: root.withdraw()).pack(side="right")
    ttk.Button(btns, text="Speichern & Anwenden", command=on_save).pack(side="right", padx=6)

    # Laufender Verbindungsstatus
    conn_var = tk.StringVar()
    ttk.Label(frm, textvariable=conn_var, foreground="#75736b").grid(
        row=row + 1, column=0, columnspan=3, sticky="w", pady=(10, 0))

    def refresh_status():
        conn_var.set(f"Status: {manager.status}")
        root.after(2000, refresh_status)
    refresh_status()

    # --- Tray (optional; auf manchen Linux-Desktops nicht verfügbar) ---
    tray = None
    try:
        import pystray
        hinted = {"done": False}

        def show_window(icon=None, item=None):
            root.after(0, lambda: (root.deiconify(), root.lift(), root.focus_force()))

        def toggle_pause(icon, item):
            manager.set_paused(not manager.paused)

        def quit_app(icon=None, item=None):
            manager.stop()
            try:
                tray.stop()
            except Exception:
                pass
            root.after(0, root.destroy)

        tray = pystray.Icon(APP_NAME, tray_image(), APP_NAME, menu=pystray.Menu(
            pystray.MenuItem("Einstellungen öffnen", show_window, default=True),
            pystray.MenuItem(lambda i: "Screenshots: pausiert" if manager.paused else "Screenshots: aktiv",
                             toggle_pause),
            pystray.MenuItem("Beenden", quit_app),
        ))
        threading.Thread(target=tray.run, daemon=True).start()
    except Exception:
        log.warning("Kein Infobereich-Symbol verfügbar – Fenster schließen beendet das Programm.")

    def on_close():
        if tray is not None:
            root.withdraw()
            if not hinted["done"]:
                hinted["done"] = True
                try:
                    tray.notify("Läuft weiter im Infobereich. Zum Beenden dort das Menü nutzen.", APP_NAME)
                except Exception:
                    pass
        else:
            manager.stop()
            root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)

    # Beim ersten Start ohne gültige Konfiguration Fenster zeigen; sonst (mit Tray) verstecken
    if config_is_valid(cfg) and tray is not None:
        root.after(200, root.withdraw)

    root.mainloop()


# --------------------------------------------------------------------------- Auto-Update
def _update_loop(manager: Manager):
    """Prüft kurz nach dem Start und dann alle 6 h auf ein neues GitHub-Release."""
    time.sleep(15)
    while True:
        try:
            if load_config().get("auto_update", True):
                res = updater.maybe_update(APP_VERSION, apply=True, on_apply=manager.stop)
                if res == "applying":
                    log.info("Beende zum Update-Austausch.")
                    os._exit(0)
        except Exception:
            log.exception("Auto-Update")
        time.sleep(6 * 3600)


# --------------------------------------------------------------------------- Headless (Dienst / ohne Anzeige)
def run_headless(manager: Manager):
    log.info("Starte ohne Fenster (headless).")
    manager.restart()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        manager.stop()


# --------------------------------------------------------------------------- main
def main():
    handlers = [logging.handlers.RotatingFileHandler(
        osb.data_dir() / "agent.log", maxBytes=512_000, backupCount=2, encoding="utf-8")]
    if sys.stderr:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    manager = Manager()
    threading.Thread(target=_update_loop, args=(manager,), daemon=True).start()

    no_gui = "--no-gui" in sys.argv or "--service" in sys.argv
    if no_gui or not osb.has_display():
        run_headless(manager)
        return
    try:
        run_gui(manager)
    except Exception:
        log.exception("GUI konnte nicht starten – laufe ohne Fenster weiter")
        run_headless(manager)


if __name__ == "__main__":
    main()
