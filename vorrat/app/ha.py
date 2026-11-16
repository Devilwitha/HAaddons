"""Vorräte in Home Assistant.

Mit MQTT-Broker (z. B. Mosquitto-Add-on) entsteht per Discovery das Gerät „Vorrat“ mit
  - sensor.vorrat_<ort>            Anzahl aller Vorräte am Ort (Attribute: alle Vorräte)
  - sensor.vorrat_<ort>_<vorrat>   Anzahl eines Vorrats (kommt und geht mit der Liste)
  - button.vorrat_<ort>_zaehlen    jetzt zählen (z. B. per Automation, wenn die Kühlschranktür zugeht)
  - button.vorrat_alle_zaehlen, sensor.vorrat_status, sensor.vorrat_letzte_aenderung
Ohne MQTT gehen die Sensoren über die REST-API (ohne Knöpfe).

Einkaufsliste: Ist todo_entity gesetzt, kommt ein Vorrat unter seinem Mindestbestand auf diese
To-do-Liste und wieder herunter, sobald genug da ist (nur Einträge, die das Add-on selbst angelegt hat).
"""
import asyncio
import json
import logging
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path

from inventory import slugify

try:
    import paho.mqtt.client as mqtt
except ImportError:
    mqtt = None

log = logging.getLogger("ha")

SUPERVISOR = os.environ.get("VORRAT_SUPERVISOR_URL", "http://supervisor")
TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
DISCOVERY = "homeassistant"
NODE = "vorrat"
TOPIC = f"bollisoft/{NODE}"


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds") if ts else None


class HomeAssistant:
    def __init__(self, version, data_dir):
        self.version = version
        self.data_dir = Path(data_dir)
        self.session = None
        self.client = None
        self.mode = "off"
        self.loop = None
        self.on_scan = None                 # Rückruf on_scan(Ort oder None für alle)
        self.loc_by_slug = {}
        self.announced = {}                 # Discovery-Topic -> Konfiguration (JSON)
        self.rest_sent = {}
        self.ha_online = True
        self._lock = asyncio.Lock()
        self.todo_file = self.data_dir / "todo.json"
        self.entities_file = self.data_dir / "mqtt_entities.json"
        self.last = None                    # letzter veröffentlichter Stand (für erneutes Senden)

    # ------------------------------------------------------------------ Start
    async def start(self, session, use_mqtt, on_scan):
        self.session = session
        self.loop = asyncio.get_running_loop()
        self.on_scan = on_scan
        if not TOKEN:
            log.info("Kein Supervisor-Token - Home Assistant wird nicht angebunden")
            return
        settings = await self._mqtt_settings() if use_mqtt and mqtt else None
        if settings:
            self._start_mqtt(settings)
            self.mode = "mqtt"
            log.info("Home Assistant über MQTT (%s), Gerät „Vorrat“", settings.get("host"))
        else:
            self.mode = "rest"
            log.info("Home Assistant über die REST-API (kein MQTT, keine Knöpfe)")

    async def _mqtt_settings(self):
        try:
            async with self.session.get(f"{SUPERVISOR}/services/mqtt", headers=self._auth()) as resp:
                if resp.status != 200:
                    return None
                return (await resp.json()).get("data") or None
        except Exception as err:            # Supervisor nicht erreichbar
            log.debug("MQTT-Dienst abfragen: %s", err)
            return None

    def _auth(self):
        return {"Authorization": f"Bearer {TOKEN}"}

    def _start_mqtt(self, settings):
        client_id = f"{NODE}-{secrets.token_hex(3)}"
        try:
            self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
        except AttributeError:              # paho-mqtt 1.x
            self.client = mqtt.Client(client_id=client_id)
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
        client.publish(f"{TOPIC}/availability", "online", retain=True)
        client.subscribe(f"{DISCOVERY}/status")
        client.subscribe(f"{TOPIC}/+/scan")
        client.subscribe(f"{TOPIC}/scan_all")
        self.announced = {}
        if self.last is not None:
            self.loop.call_soon_threadsafe(self._republish)

    def _on_message(self, _client, _userdata, message):
        payload = message.payload.decode("utf-8", "replace")
        if message.topic == f"{DISCOVERY}/status":
            if payload == "online":         # Home Assistant neu gestartet
                self.announced = {}
                self.loop.call_soon_threadsafe(self._republish)
            return
        if message.topic == f"{TOPIC}/scan_all":
            self.loop.call_soon_threadsafe(self.on_scan, None)
            return
        slug = message.topic[len(TOPIC) + 1:-len("/scan")]
        location = self.loc_by_slug.get(slug)
        if location:
            self.loop.call_soon_threadsafe(self.on_scan, location)

    def _republish(self):
        if self.last is not None:
            self.loop.create_task(self.publish(*self.last))

    async def stop(self):
        if self.client is not None:
            self.client.publish(f"{TOPIC}/availability", "offline", retain=True)
            self.client.loop_stop()

    # ------------------------------------------------------------------ Veröffentlichen
    async def publish(self, inventory, locations, status, todo_entity):
        """inventory: Inventory, locations: Orte aus der Konfiguration, status: Text für sensor.vorrat_status."""
        self.last = (inventory, locations, status, todo_entity)
        self.loc_by_slug = {slugify(name): name for name in locations}
        if self.mode == "off":
            return
        async with self._lock:
            entities = self._entities(inventory, locations, status)
            if self.mode == "mqtt":
                self._publish_mqtt(entities)
            else:
                await self._publish_rest(entities)
            if todo_entity:
                await self._sync_todo(inventory, locations, todo_entity)

    def _entities(self, inventory, locations, status):
        """Liste von (domain, key, Konfiguration, state_topic-Werte)."""
        device = {"identifiers": [f"bollisoft_{NODE}"], "name": "Vorrat", "manufacturer": "BolliSoft",
                  "model": "Vorrat (Kameras + KI)", "sw_version": self.version}
        out = []
        history = inventory.data["history"]
        last = history[-1] if history else None
        last_text = (f"{last['location']}: {last['item']} {last['old']} → {last['new']}" if last else "–")
        out.append(("sensor", "status", {"name": "Status", "icon": "mdi:camera-iris"}, status, {}))
        out.append(("sensor", "letzte_aenderung", {"name": "Letzte Änderung", "icon": "mdi:history"},
                    last_text, {"verlauf": [f"{h['location']}: {h['item']} {h['old']} → {h['new']}"
                                            for h in history[-10:]][::-1]}))
        out.append(("button", "alle_zaehlen", {"name": "Alle zählen", "icon": "mdi:camera-burst",
                                               "command_topic": f"{TOPIC}/scan_all"}, None, None))
        for location in locations:
            loc = inventory.location(location)
            lslug = slugify(location)
            items = {name: item for name, item in loc["items"].items() if not item.get("ignored")}
            total = sum(item["count"] for item in items.values())
            attrs = {"vorraete": {name: item["count"] for name, item in sorted(items.items())},
                     "fehlt": inventory.low_items(location), "letzte_zaehlung": iso(loc.get("last_scan")),
                     "fehler": loc.get("error") or ""}
            out.append(("sensor", lslug, {"name": location, "icon": "mdi:fridge-outline",
                                          "unit_of_measurement": "Stk"}, total, attrs))
            out.append(("button", f"{lslug}_zaehlen", {"name": f"{location} jetzt zählen", "icon": "mdi:camera",
                                                       "command_topic": f"{TOPIC}/{lslug}/scan"}, None, None))
            for name, item in sorted(items.items()):
                out.append(("sensor", f"{lslug}_{slugify(name)}",
                            {"name": f"{location} {name}", "icon": "mdi:package-variant-closed",
                             "unit_of_measurement": "Stk", "state_class": "measurement"},
                            item["count"], {"ort": location, "mindestens": item.get("min", 0)}))
        for entry in out:
            entry[2]["device"] = device
        return out

    # ------------------------------------------------------------------ MQTT
    def _publish_mqtt(self, entities):
        if self.client is None or not self.client.is_connected():
            return
        wanted = {}
        states = {}
        for domain, key, config, state, attrs in entities:
            topic = f"{DISCOVERY}/{domain}/{NODE}/{key}/config"
            config = dict(config, unique_id=f"bollisoft_{NODE}_{key}", default_entity_id=f"{domain}.{NODE}_{key}",
                          availability_topic=f"{TOPIC}/availability")
            if domain == "sensor":
                config.update(state_topic=f"{TOPIC}/state", value_template=f"{{{{ value_json.get('{key}') }}}}",
                              json_attributes_topic=f"{TOPIC}/attributes/{key}")
                states[key] = state
                self.client.publish(f"{TOPIC}/attributes/{key}", json.dumps(attrs or {}, ensure_ascii=False),
                                    retain=True)
            else:
                config["payload_press"] = "PRESS"
            payload = json.dumps(config, ensure_ascii=False)
            wanted[topic] = payload
            if self.announced.get(topic) != payload:
                self.client.publish(topic, payload, retain=True)
        self.client.publish(f"{TOPIC}/state", json.dumps(states, ensure_ascii=False), retain=True)
        # Entitäten gelöschter Vorräte/Orte entfernen (auch solche von vor einem Neustart)
        for topic in set(self._load_entities()) | set(self.announced):
            if topic not in wanted:
                self.client.publish(topic, "", retain=True)
        self.announced = wanted
        self._save_entities(list(wanted))

    def _load_entities(self):
        try:
            return json.loads(self.entities_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

    def _save_entities(self, topics):
        try:
            self.entities_file.write_text(json.dumps(sorted(topics)), encoding="utf-8")
        except OSError as err:
            log.debug("Entitätenliste speichern: %s", err)

    # ------------------------------------------------------------------ REST-API
    async def _publish_rest(self, entities):
        wanted = set()
        for domain, key, config, state, attrs in entities:
            if domain != "sensor":
                continue
            entity_id = f"sensor.{NODE}_{key}"
            wanted.add(entity_id)
            body = {"state": "unknown" if state is None else str(state),
                    "attributes": {**(attrs or {}), "friendly_name": f"Vorrat {config['name']}", "icon": config["icon"]}}
            for field in ("unit_of_measurement", "state_class"):
                if config.get(field):
                    body["attributes"][field] = config[field]
            if self.rest_sent.get(entity_id) == body:
                continue
            try:
                async with self.session.post(f"{SUPERVISOR}/core/api/states/{entity_id}", json=body,
                                             headers=self._auth()) as resp:
                    if resp.status < 300:
                        self.rest_sent[entity_id] = body
                    else:
                        log.warning("Home Assistant: %s für %s", resp.status, entity_id)
            except Exception as err:        # HA startet gerade neu o. Ä.
                log.warning("Home Assistant nicht erreichbar: %s", err)
                return
        for entity_id in [e for e in self.rest_sent if e not in wanted]:
            try:
                async with self.session.delete(f"{SUPERVISOR}/core/api/states/{entity_id}", headers=self._auth()):
                    pass
            except Exception as err:
                log.debug("Entität %s entfernen: %s", entity_id, err)
            self.rest_sent.pop(entity_id, None)

    # ------------------------------------------------------------------ Einkaufsliste
    async def _sync_todo(self, inventory, locations, entity_id):
        try:
            added = set(json.loads(self.todo_file.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            added = set()
        low = set()
        for location in locations:
            low.update(inventory.low_items(location))
        changed = False
        for name in sorted(low - added):
            if await self._service("todo", "add_item", {"entity_id": entity_id, "item": name}):
                added.add(name)
                changed = True
                log.info("Einkaufsliste: %s hinzugefügt", name)
        for name in sorted(added - low):
            await self._service("todo", "remove_item", {"entity_id": entity_id, "item": name})
            added.discard(name)
            changed = True
            log.info("Einkaufsliste: %s entfernt (wieder genug da)", name)
        if changed:
            self.todo_file.write_text(json.dumps(sorted(added), ensure_ascii=False), encoding="utf-8")

    async def _service(self, domain, service, data):
        try:
            async with self.session.post(f"{SUPERVISOR}/core/api/services/{domain}/{service}", json=data,
                                         headers=self._auth()) as resp:
                if resp.status < 300:
                    return True
                log.warning("%s.%s: HTTP %s %s", domain, service, resp.status, (await resp.text())[:200])
        except Exception as err:
            log.warning("%s.%s: %s", domain, service, err)
        return False
