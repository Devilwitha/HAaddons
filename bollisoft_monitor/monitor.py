"""Bollisoft Server Monitor - Home-Assistant-Add-on.

- Port 8765: Der Agent auf dem Windows-Server meldet alle paar Sekunden (POST /api/report mit
  Bearer-Token) und bekommt in der Antwort die offenen Befehle.
- Ingress (Seitenleiste "Server"): Uebersicht und Knoepfe, nur ueber Home Assistant erreichbar.
- Home Assistant: per MQTT-Discovery ein Geraet mit Sensoren und Knoepfen (wenn ein MQTT-Broker
  eingerichtet ist), sonst Sensoren ueber die REST-API. Befehle gehen auch ueber den Dienst
  hassio.addon_stdin (input: restart_server, cancel_restart, restart_task, ollama_unload).
"""
import asyncio
import hmac
import json
import logging
import os
import re
import secrets
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from aiohttp import ClientSession, ClientTimeout, web

try:
    import paho.mqtt.client as mqtt
except ImportError:  # pragma: no cover - im Add-on immer installiert
    mqtt = None

VERSION = "1.1.1"
OPTIONS_PATH = Path(os.environ.get("MONITOR_OPTIONS", "/data/options.json"))
WEB_DIR = Path(__file__).resolve().parent / "web"
AGENT_PORT = int(os.environ.get("MONITOR_AGENT_PORT", "8765"))
INGRESS_PORT = int(os.environ.get("MONITOR_INGRESS_PORT", "8099"))
INGRESS_PEER = "172.30.32.2"
SUPERVISOR = "http://supervisor"
TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
COMMAND_SECONDS = 120       # nicht abgeholte Befehle verfallen (Agent offline)
BODY_MAX = 1024 * 1024
TOPIC = "bollisoft_monitor"
DISCOVERY = "homeassistant"
ACTIONS = {
    "restart_server": "Server neu starten",
    "cancel_restart": "Neustart abbrechen",
    "restart_task": "Bollisoft-Server neu starten",
    "ollama_unload": "Ollama-Modelle entladen",
}
GIB = 1024 ** 3

log = logging.getLogger("monitor")


def load_options():
    try:
        options = json.loads(OPTIONS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        options = {}
    return {"token": str(options.get("token") or ""), "offline_after": int(options.get("offline_after") or 60),
            "use_mqtt": bool(options.get("use_mqtt", True))}


def slug(text):
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "x"


def iso(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat(timespec="seconds")


# --- Werte fuer Home Assistant ----------------------------------------------------
# (key, Domaene, Name, Einheit, device_class, state_class, Icon, Kategorie)

def entities(report, last_command):
    """Alle Entitaeten aus der letzten Meldung -> [(spec, Wert, Attribute)]."""
    system, ollama = report.get("system", {}), report.get("ollama", {})
    cpu, memory = system.get("cpu", {}), system.get("memory", {})
    loaded = ollama.get("loaded", [])
    size = sum(m.get("size", 0) for m in loaded)
    vram = sum(m.get("size_vram", 0) for m in loaded)
    network = report.get("network", {})
    top = report.get("top_processes", [])
    rows = [
        (("cpu", "sensor", "CPU-Auslastung", "%", None, "measurement", "mdi:cpu-64-bit", None),
         cpu.get("percent"), {"Kerne": cpu.get("per_core"), "Takt_MHz": cpu.get("freq_mhz"),
                              "Prozessor": system.get("cpu_model"),
                              "Kerne_Threads": f"{system.get('cores')}/{system.get('threads')}"}),
        (("memory", "sensor", "RAM-Auslastung", "%", None, "measurement", "mdi:memory", None),
         memory.get("percent"), {"belegt_GiB": round(memory.get("used", 0) / GIB, 1),
                                 "gesamt_GiB": round(memory.get("total", 0) / GIB, 1),
                                 "Auslagerung_%": memory.get("swap_percent")}),
        (("memory_used", "sensor", "RAM belegt", "GiB", "data_size", "measurement", "mdi:memory", None),
         round(memory.get("used", 0) / GIB, 1), None),
        (("boot", "sensor", "Gestartet", None, "timestamp", None, "mdi:clock-start", None),
         iso(system["boot_time"]) if system.get("boot_time") else None, None),
        (("uptime", "sensor", "Laufzeit", "h", "duration", None, "mdi:timer-outline", None),
         round(system.get("uptime_s", 0) / 3600, 1), None),
        (("processes", "sensor", "Prozesse", None, None, "measurement", "mdi:format-list-numbered", "diagnostic"),
         system.get("processes"), None),
        (("net_down", "sensor", "Netzwerk empfangen", "kB/s", "data_rate", "measurement", "mdi:download", None),
         round(network.get("recv_bps", 0) / 1000, 1), None),
        (("net_up", "sensor", "Netzwerk gesendet", "kB/s", "data_rate", "measurement", "mdi:upload", None),
         round(network.get("sent_bps", 0) / 1000, 1), None),
        (("reboot_pending", "binary_sensor", "Neustart ausstehend", None, "problem", None, "mdi:restart-alert", None),
         bool(system.get("reboot_pending")), None),
        (("ollama_online", "binary_sensor", "Ollama", None, "connectivity", None, "mdi:robot", None),
         bool(ollama.get("online")), {"Version": ollama.get("version"), "installiert": ollama.get("installed")}),
        (("ollama_model", "sensor", "Ollama aktives Modell", None, None, None, "mdi:brain", None),
         ", ".join(m["name"] for m in loaded if "embed" not in m["name"]) or "keins",
         {"geladen": [f"{m['name']} ({m['size'] / GIB:.1f} GiB, {m.get('parameters', '')} "
                      f"{m.get('quantization', '')}, Kontext {m.get('context', 0)})" for m in loaded]}),
        (("ollama_loaded", "sensor", "Ollama geladene Modelle", None, None, "measurement", "mdi:layers", None),
         len(loaded), None),
        (("ollama_memory", "sensor", "Ollama Speicher", "GiB", "data_size", "measurement", "mdi:memory", None),
         round(size / GIB, 1) if ollama.get("online") else None, {"Prozesse_MB": ollama.get("mem_mb")}),
        (("ollama_gpu", "sensor", "Ollama auf GPU", "%", None, "measurement", "mdi:expansion-card", None),
         round(vram / size * 100) if size else 0, {"VRAM_GiB": round(vram / GIB, 1)}),
        (("ollama_cpu", "sensor", "Ollama CPU", "%", None, "measurement", "mdi:cpu-64-bit", None),
         ollama.get("cpu"), None),
        (("ollama_version", "sensor", "Ollama Version", None, None, None, "mdi:tag", "diagnostic"),
         ollama.get("version") or None, None),
        (("top_process", "sensor", "Top-Prozess", None, None, None, "mdi:chart-bar", None),
         f"{top[0]['name']} ({top[0]['cpu']} %)" if top else None,
         {"Prozesse": [f"{p['name']}: {p['cpu']} % CPU, {p['mem_mb']} MB" for p in top]}),
        (("os", "sensor", "Betriebssystem", None, None, None, "mdi:microsoft-windows", "diagnostic"),
         system.get("os"), None),
        (("gpu", "sensor", "Grafik", None, None, None, "mdi:expansion-card", "diagnostic"),
         (report.get("gpu") or {}).get("Name"), report.get("gpu") or None),
        (("last_command", "sensor", "Letzter Befehl", None, None, None, "mdi:console", None),
         (last_command or {}).get("text"), last_command),
    ]
    if system.get("temperature") is not None:
        rows.append((("cpu_temperature", "sensor", "Temperatur", "°C", "temperature", "measurement",
                      "mdi:thermometer", None), system["temperature"], None))
    for disk in report.get("disks", []):
        mount = disk.get("mount", "?")
        rows.append(((f"disk_{slug(mount)}", "sensor", f"Laufwerk {mount}", "%", None, "measurement",
                      "mdi:harddisk", None), disk.get("percent"),
                     {"frei_GiB": round(disk.get("free", 0) / GIB, 1), "gesamt_GiB": round(disk.get("total", 0) / GIB, 1),
                      "Dateisystem": disk.get("fstype")}))
    for check in report.get("checks", []):
        rows.append(((f"check_{slug(check['name'])}", "binary_sensor", f"{check['name']} erreichbar", None,
                      "connectivity", None, "mdi:web", None), bool(check.get("ok")),
                     {"HTTP": check.get("status"), "Antwort_ms": check.get("ms"), "Adresse": check.get("url")}))
    rows += website_entities(report.get("website") or {})
    return rows


def website_entities(site):
    """Benutzer, Forum und Platzbedarf der Webseite (nur, was der Agent liefert)."""
    rows = []
    if "users_total" in site:
        posts = site.get("latest_posts") or []
        rows += [
            (("web_users_online", "sensor", "Benutzer online", None, None, "measurement", "mdi:account-clock", None),
             site.get("users_online"), {"Benutzer": site.get("online_names"),
                                        "Zeitfenster_min": site.get("online_minutes")}),
            (("web_users_total", "sensor", "Benutzer gesamt", None, None, "total", "mdi:account-group", None),
             site.get("users_total"), {"neu_7_Tage": site.get("users_new_7d")}),
            (("web_forum_posts", "sensor", "Forenbeiträge gesamt", None, None, "total", "mdi:forum", None),
             site.get("forum_posts"), {"Themen": site.get("forum_threads"), "neu_7_Tage": site.get("forum_posts_7d")}),
            (("web_forum_threads", "sensor", "Forenthemen gesamt", None, None, "total", "mdi:forum-outline", None),
             site.get("forum_threads"), None),
            (("web_latest_post", "sensor", "Letzter Forenbeitrag", None, None, None, "mdi:message-text", None),
             f"{posts[0]['author']}: {posts[0]['title']}"[:250] if posts else None,
             {"Beitraege": [{"Zeit": p["created_at"], "von": p["author"], "Thema": p["title"], "Text": p["text"],
                             "Link": p["url"]} for p in posts]}),
        ]
    if site.get("error"):
        rows.append((("web_error", "sensor", "Webseiten-Datenbank", None, None, None, "mdi:database-alert", "diagnostic"),
                     site["error"][:250], None))
    storage = site.get("storage")
    if storage:
        rows.append((("web_storage", "sensor", "Webseite Speicher", "GiB", "data_size", "measurement",
                      "mdi:folder-information", None), round(storage["total"] / GIB, 2),
                     {"Datenbank_MB": round(site.get("db_size", 0) / 1024 ** 2, 1), "gemessen": iso(storage["measured"]),
                      **{f"{name}_GiB": round(size / GIB, 2) for name, size in list(storage["folders"].items())[:12]}}))
    return rows


# --- Zustand -------------------------------------------------------------------

class Monitor:
    def __init__(self, options):
        self.options = options
        self.report = None
        self.last_seen = 0.0
        self.commands = []          # {id, action, created, state: offen|abgeholt|ok|fehler, message}
        self.restart_at = None      # geplanter Neustart (Unix-Zeit)
        self.lock = threading.Lock()
        self.loop = None
        self.publishers = []

    @property
    def connected(self):
        return self.report is not None and time.time() - self.last_seen < self.options["offline_after"]

    def capabilities(self):
        return (self.report or {}).get("agent", {}).get("capabilities", [])

    def add_command(self, action, source):
        if action not in ACTIONS:
            return None, "Unbekannter Befehl"
        # Nur an einen verbundenen Server: ein Neustart darf nicht liegen bleiben und Minuten
        # spaeter beim naechsten Melden ausgefuehrt werden
        if not self.connected:
            log.warning("Befehl %s von %s abgelehnt: Server nicht verbunden", action, source)
            return None, "Server nicht verbunden"
        if action not in self.capabilities():
            return None, "Auf dem Server nicht freigegeben"
        with self.lock:
            command = {"id": secrets.token_hex(8), "action": action, "label": ACTIONS[action],
                       "created": time.time(), "state": "offen", "message": "", "source": source}
            self.commands = (self.commands + [command])[-20:]
        log.info("Befehl %s von %s", action, source)
        return command, ""

    def take_commands(self):
        now, taken = time.time(), []
        with self.lock:
            for command in self.commands:
                if command["state"] != "offen":
                    continue
                if now - command["created"] > COMMAND_SECONDS:
                    command.update(state="fehler", message="Server hat den Befehl nicht abgeholt")
                else:
                    command["state"] = "abgeholt"
                    taken.append({"id": command["id"], "action": command["action"]})
        return taken

    def expire_commands(self):
        now = time.time()
        with self.lock:
            for command in self.commands:
                if command["state"] == "offen" and now - command["created"] > COMMAND_SECONDS:
                    command.update(state="fehler", message="Server hat den Befehl nicht abgeholt")

    def apply_results(self, results):
        with self.lock:
            by_id = {c["id"]: c for c in self.commands}
            for result in results or []:
                command = by_id.get(str(result.get("id")))
                if not command:
                    continue
                command.update(state="ok" if result.get("ok") else "fehler", message=str(result.get("message", ""))[:300])
                log.info("Ergebnis %s: %s", command["action"], command["message"])
                if result.get("ok") and command["action"] == "restart_server":
                    delay = (self.report or {}).get("agent", {}).get("restart_delay", 60)
                    self.restart_at = float(result.get("time") or time.time()) + delay
                elif result.get("ok") and command["action"] == "cancel_restart":
                    self.restart_at = None

    def receive(self, report):
        boot = report.get("system", {}).get("boot_time") or 0
        if self.restart_at and boot > self.restart_at - 300:
            self.restart_at = None          # Server ist neu gestartet
        self.report = report
        self.last_seen = time.time()
        self.apply_results(report.get("results"))

    def last_command(self):
        with self.lock:
            done = [c for c in self.commands if c["state"] in ("ok", "fehler")]
        if not done:
            return None
        command = done[-1]
        return {"text": f"{command['label']}: {command['message'] or command['state']}"[:250],
                "Zeit": iso(command["created"]), "ok": command["state"] == "ok"}

    def snapshot(self):
        with self.lock:
            commands = [dict(c) for c in self.commands[-10:]]
        return {"connected": self.connected, "last_seen": self.last_seen or None, "now": time.time(),
                "restart_at": self.restart_at, "report": self.report, "commands": commands,
                "actions": {key: label for key, label in ACTIONS.items() if key in self.capabilities()},
                "version": VERSION, "token_missing": not self.options["token"]}


# --- MQTT ----------------------------------------------------------------------

class MqttPublisher:
    def __init__(self, monitor, settings):
        self.monitor = monitor
        self.announced = set()
        self.last_buttons = None
        try:
            self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"bollisoft-monitor-{secrets.token_hex(3)}")
        except AttributeError:      # paho-mqtt 1.x
            self.client = mqtt.Client(client_id=f"bollisoft-monitor-{secrets.token_hex(3)}")
        if settings.get("username"):
            self.client.username_pw_set(settings["username"], settings.get("password") or "")
        if settings.get("ssl"):
            self.client.tls_set()
        self.client.will_set(f"{TOPIC}/availability", "offline", retain=True)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.connect_async(settings.get("host", "core-mosquitto"), int(settings.get("port") or 1883))
        self.client.loop_start()

    def _on_connect(self, client, *_args):
        log.info("MQTT verbunden")
        self.announced.clear()
        self.last_buttons = None
        client.publish(f"{TOPIC}/availability", "online", retain=True)
        client.subscribe(f"{TOPIC}/command")
        client.subscribe(f"{DISCOVERY}/status")
        self.publish()

    def _on_message(self, _client, _userdata, message):
        payload = message.payload.decode("utf-8", "replace").strip()
        if message.topic == f"{DISCOVERY}/status":
            if payload == "online":         # Home Assistant neu gestartet: alles neu ankuendigen
                self.announced.clear()
                self.last_buttons = None
                self.publish()
            return
        self.monitor.add_command(payload, "MQTT")

    def device(self):
        report = self.monitor.report or {}
        agent = report.get("agent", {})
        return {"identifiers": [f"bollisoft_monitor_{slug(report.get('system', {}).get('hostname', 'server'))}"],
                "name": agent.get("name") or "Bollisoft Server", "manufacturer": "Bollisoft",
                "model": report.get("system", {}).get("cpu_model") or "Windows-Server",
                "sw_version": f"Agent {agent.get('version', '?')} / Add-on {VERSION}"}

    def _announce(self, spec):
        key, domain, name, unit, device_class, state_class, icon, category = spec
        config = {
            "name": name, "unique_id": f"bollisoft_monitor_{key}", "default_entity_id": f"{domain}.bollisoft_{key}",
            "state_topic": f"{TOPIC}/state", "value_template": f"{{{{ value_json.{key} }}}}",
            "json_attributes_topic": f"{TOPIC}/attributes/{key}", "icon": icon, "device": self.device(),
        }
        if key != "connected":
            config.update(availability=[{"topic": f"{TOPIC}/availability"}, {"topic": f"{TOPIC}/agent"}],
                          availability_mode="all")
        else:
            config["availability_topic"] = f"{TOPIC}/availability"
        for field, value in (("unit_of_measurement", unit), ("device_class", device_class),
                             ("state_class", state_class), ("entity_category", category)):
            if value:
                config[field] = value
        if domain == "binary_sensor":
            config.update(payload_on="ON", payload_off="OFF")
        self.client.publish(f"{DISCOVERY}/{domain}/bollisoft_monitor/{key}/config", json.dumps(config), retain=True)
        self.announced.add(key)

    def _buttons(self):
        caps = set(self.monitor.capabilities())
        if caps == self.last_buttons:
            return
        for action, label in ACTIONS.items():
            topic = f"{DISCOVERY}/button/bollisoft_monitor/{action}/config"
            if action not in caps:
                self.client.publish(topic, "", retain=True)       # entfernt den Knopf
                continue
            config = {"name": label, "unique_id": f"bollisoft_monitor_{action}", "default_entity_id": f"button.bollisoft_{action}",
                      "command_topic": f"{TOPIC}/command", "payload_press": action, "device": self.device(),
                      "availability": [{"topic": f"{TOPIC}/availability"}, {"topic": f"{TOPIC}/agent"}],
                      "availability_mode": "all",
                      "icon": {"restart_server": "mdi:restart", "cancel_restart": "mdi:cancel",
                               "restart_task": "mdi:application-cog", "ollama_unload": "mdi:eject"}[action]}
            if action in ("restart_server", "restart_task"):
                config["device_class"] = "restart"
            self.client.publish(topic, json.dumps(config), retain=True)
        self.last_buttons = caps

    def publish(self):
        if not self.client.is_connected():
            return
        monitor = self.monitor
        connected_spec = ("connected", "binary_sensor", "Verbunden", None, "connectivity", None, "mdi:lan-connect", None)
        if "connected" not in self.announced:
            self._announce(connected_spec)
        state = {"connected": "ON" if monitor.connected else "OFF"}
        self.client.publish(f"{TOPIC}/attributes/connected", json.dumps(
            {"zuletzt": iso(monitor.last_seen) if monitor.last_seen else None}))
        if monitor.report is not None:
            for spec, value, attributes in entities(monitor.report, monitor.last_command()):
                if spec[0] not in self.announced:
                    self._announce(spec)
                state[spec[0]] = ("ON" if value else "OFF") if spec[1] == "binary_sensor" else value
                self.client.publish(f"{TOPIC}/attributes/{spec[0]}", json.dumps(attributes or {}))
            self._buttons()
        self.client.publish(f"{TOPIC}/agent", "online" if monitor.connected else "offline", retain=True)
        self.client.publish(f"{TOPIC}/state", json.dumps(state), retain=True)


# --- REST-API (ohne MQTT) --------------------------------------------------------

class RestPublisher:
    def __init__(self, monitor):
        self.monitor = monitor
        self.sent = {}

    async def publish(self, session):
        monitor = self.monitor
        items = [(("connected", "binary_sensor", "Verbunden", None, "connectivity", None, "mdi:lan-connect", None),
                  monitor.connected, {"zuletzt": iso(monitor.last_seen) if monitor.last_seen else None})]
        if monitor.report is not None:
            items += entities(monitor.report, monitor.last_command())
        name = (monitor.report or {}).get("agent", {}).get("name") or "Bollisoft Server"
        for (key, domain, label, unit, device_class, state_class, icon, _category), value, attributes in items:
            if key != "connected" and not monitor.connected:
                state = "unavailable"
            elif domain == "binary_sensor":
                state = "on" if value else "off"
            else:
                state = "unknown" if value is None else str(value)
            body = {"state": state, "attributes": {**(attributes or {}), "friendly_name": f"{name} {label}", "icon": icon}}
            for field, item in (("unit_of_measurement", unit), ("device_class", device_class), ("state_class", state_class)):
                if item:
                    body["attributes"][field] = item
            entity_id = f"{domain}.bollisoft_{key}"
            if self.sent.get(entity_id) == body:
                continue
            try:
                async with session.post(f"{SUPERVISOR}/core/api/states/{entity_id}", json=body,
                                        headers={"Authorization": f"Bearer {TOKEN}"}) as response:
                    if response.status < 300:
                        self.sent[entity_id] = body
                    else:
                        log.warning("Home Assistant: %s fuer %s", response.status, entity_id)
            except Exception as exc:  # noqa: BLE001
                log.warning("Home Assistant nicht erreichbar: %s", exc)
                return


# --- HTTP ----------------------------------------------------------------------

def agent_app(monitor):
    async def report(request):
        token = monitor.options["token"]
        given = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if not token or not hmac.compare_digest(given.encode(), token.encode()):
            log.warning("Meldung mit falschem Token von %s", request.remote)
            return web.json_response({"error": "token"}, status=401)
        try:
            data = await request.json()
        except ValueError:
            return web.json_response({"error": "json"}, status=400)
        if not isinstance(data, dict):
            return web.json_response({"error": "json"}, status=400)
        was_connected = monitor.connected
        monitor.receive(data)
        if not was_connected:
            log.info("Server %s meldet sich", data.get("agent", {}).get("name"))
        commands = monitor.take_commands()
        monitor.loop.call_soon(publish_soon, monitor)
        return web.json_response({"commands": commands})

    app = web.Application(client_max_size=BODY_MAX)
    app.router.add_post("/api/report", report)
    app.router.add_get("/api/ping", lambda _r: web.json_response({"ok": True, "version": VERSION}))
    return app


def ingress_app(monitor):
    allow_any = os.environ.get("MONITOR_INGRESS_ANY") == "1"     # nur fuer lokale Tests

    @web.middleware
    async def only_ingress(request, handler):
        if not allow_any and request.remote != INGRESS_PEER:
            return web.Response(status=403, text="Nur ueber Home Assistant")
        return await handler(request)

    async def index(_request):
        return web.FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    async def state(_request):
        monitor.expire_commands()
        return web.json_response(monitor.snapshot())

    async def command(request):
        try:
            data = await request.json()
        except ValueError:
            data = {}
        result, problem = monitor.add_command(str(data.get("action", "")), "Seitenleiste")
        if not result:
            return web.json_response({"error": problem}, status=400)
        return web.json_response({"ok": True, "id": result["id"]})

    app = web.Application(middlewares=[only_ingress], client_max_size=64 * 1024)
    app.router.add_get("/", index)
    app.router.add_get("/api/state", state)
    app.router.add_post("/api/command", command)
    return app


def publish_soon(monitor):
    for publisher in monitor.publishers:
        if isinstance(publisher, MqttPublisher):
            publisher.publish()
        else:
            monitor.loop.create_task(publisher.publish(monitor.session))


async def mqtt_settings(session):
    if not TOKEN:
        return None
    try:
        async with session.get(f"{SUPERVISOR}/services/mqtt", headers={"Authorization": f"Bearer {TOKEN}"}) as response:
            if response.status != 200:
                return None
            return (await response.json()).get("data") or None
    except Exception:  # noqa: BLE001
        return None


def read_stdin(monitor):
    """hassio.addon_stdin: eine Zeile = ein Befehl."""
    for line in sys.stdin:
        action = line.strip().strip('"')
        if action:
            monitor.loop.call_soon_threadsafe(monitor.add_command, action, "Dienst addon_stdin")


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    options = load_options()
    monitor = Monitor(options)
    monitor.loop = asyncio.get_running_loop()
    if not options["token"]:
        log.warning("Kein Token gesetzt - Meldungen werden abgelehnt. Vorschlag: %s", secrets.token_urlsafe(24))
    monitor.session = ClientSession(timeout=ClientTimeout(total=10))
    settings = await mqtt_settings(monitor.session) if options["use_mqtt"] and mqtt else None
    if settings:
        monitor.publishers.append(MqttPublisher(monitor, settings))
        log.info("Home Assistant ueber MQTT (%s)", settings.get("host"))
    elif TOKEN:
        monitor.publishers.append(RestPublisher(monitor))
        log.info("Kein MQTT - Sensoren ueber die REST-API (Knoepfe in der Seitenleiste und per hassio.addon_stdin)")
    threading.Thread(target=read_stdin, args=(monitor,), daemon=True).start()

    runners = []
    for app, port in ((agent_app(monitor), AGENT_PORT), (ingress_app(monitor), INGRESS_PORT)):
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", port).start()
        runners.append(runner)
    log.info("Bollisoft Server Monitor %s: Agent-Port %s, Ingress %s", VERSION, AGENT_PORT, INGRESS_PORT)

    was_connected = False
    while True:
        await asyncio.sleep(10)
        monitor.expire_commands()
        if was_connected and not monitor.connected:
            log.warning("Server meldet sich nicht mehr")
        if was_connected != monitor.connected or not monitor.connected:
            publish_soon(monitor)       # "Verbunden" aus / Entitaeten nicht verfuegbar
        was_connected = monitor.connected


if __name__ == "__main__":
    asyncio.run(main())
