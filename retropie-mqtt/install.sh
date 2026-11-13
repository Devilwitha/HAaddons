#!/usr/bin/env bash
# Installiert retropie-mqtt auf einem RetroPie.
#   sudo ./install.sh              installieren / aktualisieren
#   sudo ./install.sh --uninstall  entfernen
set -euo pipefail

[ "$(id -u)" -eq 0 ] || exec sudo bash "$0" "$@"

SRC=$(cd "$(dirname "$0")" && pwd)
BIN=/usr/local/bin/retropie-mqtt
CONF=/etc/retropie-mqtt.conf
SERVICE=/etc/systemd/system/retropie-mqtt.service
RP_CONFIGS=/opt/retropie/configs/all
RA_CFG=$RP_CONFIGS/retroarch.cfg
MARK="# retropie-mqtt"

RP_USER=${SUDO_USER:-pi}
[ "$RP_USER" = root ] && RP_USER=pi
id "$RP_USER" >/dev/null 2>&1 || { echo "Benutzer $RP_USER existiert nicht" >&2; exit 1; }
RP_GROUP=$(id -gn "$RP_USER")

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

# Installiert eine Datei und entfernt dabei Windows-Zeilenenden (CRLF)
install_lf() { # <quelle> <ziel> <modus>
  sed 's/\r$//' "$1" >"$2.tmp" && chmod "$3" "$2.tmp" && mv "$2.tmp" "$2"
}

uninstall() {
  say "Entferne retropie-mqtt"
  [ -x "$BIN" ] && [ -r "$CONF" ] && "$BIN" remove || true
  systemctl disable --now retropie-mqtt 2>/dev/null || true
  rm -f "$SERVICE" "$BIN"
  systemctl daemon-reload
  for h in runcommand-onstart.sh runcommand-onend.sh; do
    [ -f "$RP_CONFIGS/$h" ] && sed -i "/$MARK\$/d" "$RP_CONFIGS/$h"
  done
  rm -f "$CONF"
  echo "Fertig. network_cmd_enable in $RA_CFG wurde nicht zurückgesetzt."
  exit 0
}

[ "${1:-}" = --uninstall ] && uninstall

[ -d "$RP_CONFIGS" ] || { echo "$RP_CONFIGS nicht gefunden – ist das ein RetroPie?" >&2; exit 1; }

say "Installiere Pakete"
apt-get update -qq
apt-get install -y -qq mosquitto-clients python3 >/dev/null

say "Installiere Skript nach $BIN"
install_lf "$SRC/retropie-mqtt.sh" "$BIN" 755

if [ -f "$CONF" ]; then
  say "Behalte bestehende Konfiguration $CONF"
elif [ -t 0 ]; then
  say "MQTT-Zugangsdaten (Mosquitto-Add-on: einen HA-Benutzer verwenden)"
  read -rp "Broker (IP/Hostname von Home Assistant): " host
  read -rp "Port [1883]: " port
  read -rp "Benutzer: " user
  read -rsp "Passwort: " pass; echo
  read -rp "Gerätename in Home Assistant [RetroPie]: " name
  read -rp "Neustart/Herunterfahren aus HA erlauben? [J/n]: " power
  name=${name:-RetroPie}
  id=$(printf '%s' "$name" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '_' | sed 's/^_*//;s/_*$//')
  case ${power:-j} in [nN]*) power=no ;; *) power=yes ;; esac
  (
    umask 027
    {
      echo "# retropie-mqtt Konfiguration (Vorlage: retropie-mqtt.conf.example)"
      printf 'MQTT_HOST=%q\n'   "$host"
      printf 'MQTT_PORT=%q\n'   "${port:-1883}"
      printf 'MQTT_USER=%q\n'   "$user"
      printf 'MQTT_PASS=%q\n'   "$pass"
      printf 'DEVICE_NAME=%q\n' "$name"
      printf 'DEVICE_ID=%q\n'   "${id:-retropie}"
      printf 'ALLOW_POWER=%q\n' "$power"
    } >"$CONF"
  )
else
  install_lf "$SRC/retropie-mqtt.conf.example" "$CONF" 640
  echo "Kein Terminal – bitte $CONF anpassen und 'sudo systemctl restart retropie-mqtt' ausführen."
fi
chown root:"$RP_GROUP" "$CONF"
chmod 640 "$CONF"

say "Richte runcommand-Hooks ein"
add_hook() { # <datei> <zeile>
  local f=$RP_CONFIGS/$1
  [ -f "$f" ] || { printf '#!/usr/bin/env bash\n' >"$f"; chown "$RP_USER:$RP_GROUP" "$f"; }
  sed -i "/$MARK\$/d" "$f"
  printf '%s %s\n' "$2" "$MARK" >>"$f"
  chmod +x "$f"
}
add_hook runcommand-onstart.sh "$BIN start \"\$1\" \"\$2\" \"\$3\" \"\$4\" </dev/null >/dev/null 2>&1 &"
add_hook runcommand-onend.sh   "$BIN end </dev/null >/dev/null 2>&1 &"

say "Aktiviere RetroArch Network Commands (Pause, Speichern, Beenden …)"
set_ra() { # <schlüssel> <wert>
  if grep -qE "^[#[:space:]]*$1[[:space:]]*=" "$RA_CFG"; then
    sed -i -E "s|^[#[:space:]]*$1[[:space:]]*=.*|$1 = \"$2\"|" "$RA_CFG"
  else
    printf '%s = "%s"\n' "$1" "$2" >>"$RA_CFG"
  fi
}
if [ -f "$RA_CFG" ]; then
  [ -f "$RA_CFG.bak-retropie-mqtt" ] || cp -p "$RA_CFG" "$RA_CFG.bak-retropie-mqtt"
  set_ra network_cmd_enable true
  set_ra network_cmd_port 55355
else
  echo "$RA_CFG nicht gefunden – RetroArch-Befehle bleiben deaktiviert."
fi

say "Installiere systemd-Dienst"
install_lf "$SRC/retropie-mqtt.service" "$SERVICE" 644
systemctl daemon-reload
systemctl enable retropie-mqtt >/dev/null

say "Teste Verbindung"
if "$BIN" test; then
  systemctl restart retropie-mqtt
  echo
  echo "Fertig! Das Gerät erscheint in Home Assistant unter"
  echo "Einstellungen → Geräte & Dienste → MQTT."
  echo "Logs: journalctl -u retropie-mqtt -f   bzw.   journalctl -t retropie-mqtt"
else
  echo "Verbindung fehlgeschlagen – $CONF prüfen, dann: sudo systemctl restart retropie-mqtt"
  exit 1
fi
