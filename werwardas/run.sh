#!/usr/bin/with-contenv bashio
# Startet den WerWarDas-Kameradienst. Die MQTT-Zugangsdaten kommen vom Mosquitto-Add-on.
set -e

export MQTT_HOST="$(bashio::services mqtt 'host')"
export MQTT_PORT="$(bashio::services mqtt 'port')"
export MQTT_USER="$(bashio::services mqtt 'username')"
export MQTT_PASSWORD="$(bashio::services mqtt 'password')"

python3 /make_config.py /data/options.json /data/config.yaml
bashio::log.info "Starte WerWarDas (Broker ${MQTT_HOST}:${MQTT_PORT})"
exec python3 -m werwardas -c /data/config.yaml
