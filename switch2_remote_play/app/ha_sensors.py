"""Sensoren für Home Assistant (BolliSoft-Add-ons).

Ist ein MQTT-Broker eingerichtet (z. B. Mosquitto-Add-on), werden die Sensoren per MQTT-Discovery
als eigenes Gerät angelegt. Sonst gehen sie über die REST-API von Home Assistant
(POST /api/states, Entitäten ohne Gerät; nach einem Neustart von HA kommen sie mit dem nächsten
Update wieder).

Diese Datei ist in mehreren Add-ons identisch (gba_emulator, n64_emulator, switch2_remote_play).
"""
import asyncio
import json
import logging
import os
import secrets
from collections import namedtuple

try:
    import paho.mqtt.client as mqtt
except ImportError:              # ohne paho: nur REST-API
    mqtt = None

SUPERVISOR = os.environ.get("BOLLISOFT_SUPERVISOR_URL", "http://supervisor")    # überschreibbar für Tests
TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
DISCOVERY = "homeassistant"

log = logging.getLogger("sensors")

Sensor = namedtuple("Sensor", "key domain name icon unit device_class state_class category",
                    defaults=(None, None, None, None))


class SensorHub:
    """Hält die aktuellen Werte und liefert sie an Home Assistant.

    node:   eindeutiger Kurzname, z. B. "gba_emulator" -> Entitäten sensor.gba_emulator_<key>
    sensors: Liste von Sensor(...)
    """

    def __init__(self, node, device_name, model, version, sensors):
        self.node = node
        self.device_name = device_name
        self.model = model
        self.version = version
        self.sensors = {s.key: s for s in sensors}
        self.values = {}
        self.mode = None
        self.session = None
        self.client = None
        self.announced = False
        self._rest_sent = {}
        self._rest_lock = asyncio.Lock()
        self.topic = f"bollisoft/{node}"

    # ------------------------------------------------------------------ Start
    async def start(self, session, use_mqtt=True):
        self.session = session
        if not TOKEN:
            log.info("Kein Supervisor-Token - Sensoren für Home Assistant sind aus")
            return
        settings = await self._mqtt_settings() if use_mqtt and mqtt else None
        if settings:
            self._start_mqtt(settings)
            self.mode = "mqtt"
            log.info("Sensoren über MQTT (%s) als Gerät „%s“", settings.get("host"), self.device_name)
        else:
            self.mode = "rest"
            log.info("Sensoren über die REST-API von Home Assistant (kein MQTT)")

    async def _mqtt_settings(self):
        try:
            async with self.session.get(f"{SUPERVISOR}/services/mqtt",
                                        headers={"Authorization": f"Bearer {TOKEN}"}) as response:
                if response.status != 200:
                    return None
                return (await response.json()).get("data") or None
        except Exception as err:        # Supervisor nicht erreichbar
            log.debug("MQTT-Dienst abfragen: %s", err)
            return None

    def _start_mqtt(self, settings):
        client_id = f"{self.node}-{secrets.token_hex(3)}"
        try:
            self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
        except AttributeError:          # paho-mqtt 1.x
            self.client = mqtt.Client(client_id=client_id)
        if settings.get("username"):
            self.client.username_pw_set(settings["username"], settings.get("password") or "")
        if settings.get("ssl"):
            self.client.tls_set()
        self.client.will_set(f"{self.topic}/availability", "offline", retain=True)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.connect_async(settings.get("host", "core-mosquitto"), int(settings.get("port") or 1883))
        self.client.loop_start()

    def _on_connect(self, client, *_args):
        self.announced = False
        client.publish(f"{self.topic}/availability", "online", retain=True)
        client.subscribe(f"{DISCOVERY}/status")
        self._publish_mqtt()

    def _on_message(self, _client, _userdata, message):
        if message.topic == f"{DISCOVERY}/status" and message.payload.decode("utf-8", "replace") == "online":
            self.announced = False              # Home Assistant neu gestartet
            self._publish_mqtt()

    # ------------------------------------------------------------------ Werte
    def set(self, key, state, attributes=None):
        self.values[key] = (state, attributes or {})

    async def publish(self):
        if self.mode == "mqtt":
            self._publish_mqtt()
        elif self.mode == "rest":
            await self._publish_rest()

    async def stop(self):
        if self.client is not None:
            self.client.publish(f"{self.topic}/availability", "offline", retain=True)
            self.client.loop_stop()

    # ------------------------------------------------------------------ MQTT
    def _device(self):
        return {"identifiers": [f"bollisoft_{self.node}"], "name": self.device_name,
                "manufacturer": "BolliSoft", "model": self.model, "sw_version": self.version}

    def _announce(self):
        for key, s in self.sensors.items():
            config = {
                "name": s.name,
                "unique_id": f"bollisoft_{self.node}_{key}",
                "default_entity_id": f"{s.domain}.{self.node}_{key}",
                "state_topic": f"{self.topic}/state",
                "value_template": f"{{{{ value_json.{key} }}}}",
                "json_attributes_topic": f"{self.topic}/attributes/{key}",
                "availability_topic": f"{self.topic}/availability",
                "icon": s.icon,
                "device": self._device(),
            }
            for field, value in (("unit_of_measurement", s.unit), ("device_class", s.device_class),
                                 ("state_class", s.state_class), ("entity_category", s.category)):
                if value:
                    config[field] = value
            if s.domain == "binary_sensor":
                config.update(payload_on="ON", payload_off="OFF")
            self.client.publish(f"{DISCOVERY}/{s.domain}/{self.node}/{key}/config", json.dumps(config), retain=True)
        self.announced = True

    def _publish_mqtt(self):
        if self.client is None or not self.client.is_connected():
            return
        if not self.announced:
            self._announce()
        state = {}
        for key, s in self.sensors.items():
            value, attributes = self.values.get(key, (None, {}))
            if s.domain == "binary_sensor":
                value = "ON" if value else "OFF"
            state[key] = value
            self.client.publish(f"{self.topic}/attributes/{key}", json.dumps(attributes), retain=True)
        self.client.publish(f"{self.topic}/state", json.dumps(state), retain=True)

    # ------------------------------------------------------------------ REST-API
    async def _publish_rest(self):
        async with self._rest_lock:
            for key, s in self.sensors.items():
                value, attributes = self.values.get(key, (None, {}))
                if s.domain == "binary_sensor":
                    state = "on" if value else "off"
                else:
                    state = "unknown" if value is None else str(value)
                body = {"state": state, "attributes": {**attributes, "friendly_name": f"{self.device_name} {s.name}",
                                                       "icon": s.icon}}
                for field, item in (("unit_of_measurement", s.unit), ("device_class", s.device_class),
                                    ("state_class", s.state_class)):
                    if item:
                        body["attributes"][field] = item
                entity_id = f"{s.domain}.{self.node}_{key}"
                if self._rest_sent.get(entity_id) == body:
                    continue
                try:
                    async with self.session.post(f"{SUPERVISOR}/core/api/states/{entity_id}", json=body,
                                                 headers={"Authorization": f"Bearer {TOKEN}"}) as response:
                        if response.status < 300:
                            self._rest_sent[entity_id] = body
                        else:
                            log.warning("Home Assistant: %s für %s", response.status, entity_id)
                except Exception as err:      # HA startet gerade neu o. Ä.
                    log.warning("Home Assistant nicht erreichbar: %s", err)
                    return
