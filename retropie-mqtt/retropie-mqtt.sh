#!/usr/bin/env bash
# retropie-mqtt – verbindet RetroPie über MQTT mit Home Assistant
#
#   retropie-mqtt start <system> <emulator> <rom> [befehl]   aus runcommand-onstart.sh
#   retropie-mqtt end                                        aus runcommand-onend.sh
#   retropie-mqtt daemon    Discovery senden und auf Befehle aus HA warten (systemd)
#   retropie-mqtt offline   "offline" + "idle" senden (beim Stoppen des Dienstes)
#   retropie-mqtt remove    alle Entitäten aus Home Assistant entfernen
#   retropie-mqtt test      Verbindung zum Broker testen

set -u

CONF="${RETROPIE_MQTT_CONF:-/etc/retropie-mqtt.conf}"

log() { logger -t retropie-mqtt -- "$*" 2>/dev/null; echo "retropie-mqtt: $*" >&2; }
die() { log "$*"; exit 1; }

[ -r "$CONF" ] || die "Konfiguration $CONF fehlt oder ist nicht lesbar"
# shellcheck source=/dev/null
. "$CONF"

: "${MQTT_HOST:?MQTT_HOST ist in der Konfiguration nicht gesetzt}"
MQTT_PORT="${MQTT_PORT:-1883}"
MQTT_USER="${MQTT_USER:-}"
MQTT_PASS="${MQTT_PASS:-}"
DEVICE_ID="${DEVICE_ID:-retropie}"
DEVICE_NAME="${DEVICE_NAME:-RetroPie}"
DISCOVERY_PREFIX="${DISCOVERY_PREFIX:-homeassistant}"
RETROARCH_PORT="${RETROARCH_PORT:-55355}"
ALLOW_POWER="${ALLOW_POWER:-yes}"

[[ $DEVICE_ID =~ ^[A-Za-z0-9_-]+$ ]] || die "DEVICE_ID darf nur A-Z, a-z, 0-9, _ und - enthalten"

BASE="retropie/$DEVICE_ID"
T_STATE="$BASE/state"
T_AVAIL="$BASE/availability"
T_CMD="$BASE/cmd"

AUTH=()
[ -n "$MQTT_USER" ] && AUTH+=(-u "$MQTT_USER")
[ -n "$MQTT_PASS" ] && AUTH+=(-P "$MQTT_PASS")

# Alle Discovery-Entitäten (für "remove" und ALLOW_POWER=no)
ENTITIES=(
  binary_sensor:playing sensor:game sensor:system sensor:emulator sensor:since
  button:pause button:save_state button:load_state button:reset button:screenshot
  button:quit button:reboot button:shutdown
)

# ---------------------------------------------------------------- Hilfsfunktionen

# pub <topic> <payload> [retain] – leerer Payload löscht eine retained Nachricht
pub() {
  local args=(-h "$MQTT_HOST" -p "$MQTT_PORT" -q 1 -t "$1")
  args+=(${AUTH[@]+"${AUTH[@]}"})
  [ "${3:-}" = retain ] && args+=(-r)
  if [ -n "$2" ]; then args+=(-m "$2"); else args+=(-n); fi
  timeout 15 mosquitto_pub "${args[@]}" </dev/null || log "Senden an $1 fehlgeschlagen"
}

json_escape() {
  local s=${1//\\/\\\\}
  s=${s//\"/\\\"}
  s=${s//[$'\t\r\n']/ }
  printf '%s' "$s"
}

# state_payload <state> [system] [emulator] [spiel] [rom]
state_payload() {
  printf '{"state":"%s","system":"%s","emulator":"%s","game":"%s","rom":"%s","since":"%s"}' \
    "$1" "$(json_escape "${2:-}")" "$(json_escape "${3:-}")" \
    "$(json_escape "${4:-}")" "$(json_escape "${5:-}")" "$(date -Iseconds)"
}

# Spielname aus der gamelist.xml (gescrapter Name), sonst Dateiname ohne Endung
game_name() {
  local system=$1 rom=$2 name=""
  if [ -n "$rom" ] && command -v python3 >/dev/null; then
    name=$(python3 - "$system" "$rom" 2>/dev/null <<'PY'
import os, sys, xml.etree.ElementTree as ET
system, rom = sys.argv[1], sys.argv[2]
base = os.path.basename(rom)
home = os.path.expanduser("~")
for f in (os.path.join(os.path.dirname(rom), "gamelist.xml"),
          os.path.join(home, "RetroPie/roms", system, "gamelist.xml"),
          os.path.join(home, ".emulationstation/gamelists", system, "gamelist.xml"),
          os.path.join("/etc/emulationstation/gamelists", system, "gamelist.xml")):
    try:
        for g in ET.parse(f).getroot().iter("game"):
            if os.path.basename((g.findtext("path") or "").strip()) == base:
                n = (g.findtext("name") or "").strip()
                if n:
                    print(n)
                    sys.exit(0)
    except Exception:
        pass
PY
)
  fi
  if [ -z "$name" ]; then
    name=$(basename -- "$rom")
    name=${name%.*}
  fi
  printf '%s' "$name"
}

# Serialisiert start/end, damit "idle" nie von einem verspäteten "playing" überholt wird
take_lock() {
  command -v flock >/dev/null || return 0
  exec 9>"${XDG_RUNTIME_DIR:-/tmp}/retropie-mqtt.lock" && flock -w 30 9
}

# ---------------------------------------------------------------- Discovery

device_json() {
  local model
  model=$(tr -d '\0' 2>/dev/null </proc/device-tree/model) || model=""
  [ -n "$model" ] || model="Linux"
  printf '"device":{"identifiers":["%s"],"name":"%s","manufacturer":"RetroPie","model":"%s"}' \
    "$DEVICE_ID" "$(json_escape "$DEVICE_NAME")" "$(json_escape "$model")"
}

# disc <component> <key> <json-felder ohne Klammern>
disc() {
  pub "$DISCOVERY_PREFIX/$1/$DEVICE_ID/$2/config" \
    "{$3,\"unique_id\":\"${DEVICE_ID}_$2\",\"availability_topic\":\"$T_AVAIL\",$DEV_JSON,\"origin\":{\"name\":\"retropie-mqtt\"}}" \
    retain
}

publish_discovery() {
  local DEV_JSON st b key name payload icon extra
  DEV_JSON=$(device_json)
  st="\"state_topic\":\"$T_STATE\""

  disc binary_sensor playing "\"name\":\"Spielt\",$st,\"value_template\":\"{{ value_json.state }}\",\"payload_on\":\"playing\",\"payload_off\":\"idle\",\"device_class\":\"running\",\"json_attributes_topic\":\"$T_STATE\""
  disc sensor game     "\"name\":\"Spiel\",$st,\"value_template\":\"{{ value_json.game if value_json.game else 'Kein Spiel' }}\",\"icon\":\"mdi:gamepad-variant\""
  disc sensor system   "\"name\":\"System\",$st,\"value_template\":\"{{ value_json.system if value_json.system else '-' }}\",\"icon\":\"mdi:controller-classic\""
  disc sensor emulator "\"name\":\"Emulator\",$st,\"value_template\":\"{{ value_json.emulator if value_json.emulator else '-' }}\",\"icon\":\"mdi:chip\",\"entity_category\":\"diagnostic\""
  disc sensor since    "\"name\":\"Letzte Änderung\",$st,\"value_template\":\"{{ value_json.since }}\",\"device_class\":\"timestamp\",\"entity_category\":\"diagnostic\""

  for b in \
    "pause|Pause|PAUSE_TOGGLE|mdi:pause|" \
    "save_state|Spielstand speichern|SAVE_STATE|mdi:content-save|" \
    "load_state|Spielstand laden|LOAD_STATE|mdi:folder-open|" \
    "reset|Spiel zurücksetzen|RESET|mdi:restore|" \
    "screenshot|Screenshot|SCREENSHOT|mdi:camera|" \
    "quit|Spiel beenden|QUIT|mdi:exit-to-app|"; do
    IFS='|' read -r key name payload icon extra <<<"$b"
    disc button "$key" "\"name\":\"$name\",\"command_topic\":\"$T_CMD\",\"payload_press\":\"$payload\",\"icon\":\"$icon\""
  done

  if [ "$ALLOW_POWER" = yes ]; then
    disc button reboot   "\"name\":\"Neustart\",\"command_topic\":\"$T_CMD\",\"payload_press\":\"REBOOT\",\"device_class\":\"restart\",\"entity_category\":\"config\""
    disc button shutdown "\"name\":\"Herunterfahren\",\"command_topic\":\"$T_CMD\",\"payload_press\":\"SHUTDOWN\",\"icon\":\"mdi:power\",\"entity_category\":\"config\""
  else
    pub "$DISCOVERY_PREFIX/button/$DEVICE_ID/reboot/config" "" retain
    pub "$DISCOVERY_PREFIX/button/$DEVICE_ID/shutdown/config" "" retain
  fi
}

# ---------------------------------------------------------------- Befehle aus HA

ra_cmd() {
  # RetroArch Network Commands (network_cmd_enable = "true" in retroarch.cfg)
  printf '%s' "$1" >"/dev/udp/127.0.0.1/$RETROARCH_PORT" 2>/dev/null \
    || log "RetroArch-Befehl $1 konnte nicht gesendet werden"
}

quit_game() {
  if pgrep -x retroarch >/dev/null; then
    ra_cmd QUIT
    sleep 1
    pgrep -x retroarch >/dev/null && ra_cmd QUIT   # falls "Beenden zweimal drücken" aktiv ist
    sleep 5
    pgrep -x retroarch >/dev/null && pkill -TERM -x retroarch
  else
    # Standalone-Emulator: Kindprozesse von runcommand beenden
    local rc
    rc=$(pgrep -f '/runcommand\.sh' | head -n1)
    [ -n "$rc" ] && pkill -TERM -P "$rc"
  fi
}

handle_cmd() {
  case $1 in
    PAUSE_TOGGLE|SAVE_STATE|LOAD_STATE|RESET|SCREENSHOT|MENU_TOGGLE|FAST_FORWARD|STATE_SLOT_PLUS|STATE_SLOT_MINUS)
      log "Befehl: $1"; ra_cmd "$1" ;;
    QUIT)
      log "Befehl: QUIT"; quit_game & ;;
    REBOOT|SHUTDOWN)
      if [ "$ALLOW_POWER" != yes ]; then log "$1 ignoriert (ALLOW_POWER=no)"; return; fi
      log "Befehl: $1"
      pub "$T_STATE" "$(state_payload idle)" retain
      pub "$T_AVAIL" offline retain
      if [ "$1" = REBOOT ]; then systemctl reboot; else systemctl poweroff; fi ;;
    *)
      log "Unbekannter Befehl ignoriert: $1" ;;
  esac
}

# ---------------------------------------------------------------- Unterbefehle

cmd_start() {
  local system=${1:-} emulator=${2:-} rom=${3:-} game
  take_lock
  game=$(game_name "$system" "$rom")
  pub "$T_STATE" "$(state_payload playing "$system" "$emulator" "$game" "$rom")" retain
}

cmd_end() {
  take_lock
  pub "$T_STATE" "$(state_payload idle)" retain
}

cmd_daemon() {
  command -v mosquitto_sub >/dev/null || die "mosquitto_sub fehlt (apt install mosquitto-clients)"
  local topic payload
  while true; do
    publish_discovery
    pgrep -f '/runcommand\.sh' >/dev/null || pub "$T_STATE" "$(state_payload idle)" retain
    ( sleep 3; pub "$T_AVAIL" online retain ) &

    # Der Last Will meldet "offline", wenn der Pi ohne sauberes Abmelden verschwindet.
    # Taucht "offline" auf, während wir verbunden sind, korrigieren wir es sofort.
    mosquitto_sub -h "$MQTT_HOST" -p "$MQTT_PORT" ${AUTH[@]+"${AUTH[@]}"} \
      -i "${DEVICE_ID}-retropie-mqtt" -q 1 -v \
      --will-topic "$T_AVAIL" --will-payload offline --will-qos 1 --will-retain \
      -t "$T_CMD" -t "$T_AVAIL" -t "$DISCOVERY_PREFIX/status" |
    while read -r topic payload; do
      case $topic in
        "$T_CMD")   handle_cmd "$payload" ;;
        "$T_AVAIL") [ "$payload" = offline ] && pub "$T_AVAIL" online retain ;;
        "$DISCOVERY_PREFIX/status")
          # HA wurde neu gestartet -> Discovery erneut senden
          if [ "$payload" = online ]; then publish_discovery; pub "$T_AVAIL" online retain; fi ;;
      esac
    done
    log "Verbindung zum Broker verloren, neuer Versuch in 10 s"
    sleep 10
  done
}

cmd_offline() {
  pub "$T_STATE" "$(state_payload idle)" retain
  pub "$T_AVAIL" offline retain
}

cmd_remove() {
  local e
  for e in "${ENTITIES[@]}"; do
    pub "$DISCOVERY_PREFIX/${e%%:*}/$DEVICE_ID/${e#*:}/config" "" retain
  done
  pub "$T_STATE" "" retain
  pub "$T_AVAIL" "" retain
  log "Entitäten aus Home Assistant entfernt"
}

cmd_test() {
  if timeout 15 mosquitto_pub -h "$MQTT_HOST" -p "$MQTT_PORT" ${AUTH[@]+"${AUTH[@]}"} \
       -q 1 -t "$BASE/test" -m "ok" </dev/null; then
    echo "Verbindung zu $MQTT_HOST:$MQTT_PORT ok"
  else
    die "Keine Verbindung zu $MQTT_HOST:$MQTT_PORT (Host, Port, Benutzer, Passwort prüfen)"
  fi
}

case ${1:-} in
  start)   shift; cmd_start "$@" ;;
  end)     cmd_end ;;
  daemon)  cmd_daemon ;;
  offline) cmd_offline ;;
  remove)  cmd_remove ;;
  test)    cmd_test ;;
  *)       sed -n '2,9s/^# \{0,1\}//p' "$0"; exit 1 ;;
esac
