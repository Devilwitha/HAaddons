#!/usr/bin/env bash
# Game Remote - Raspberry Pi 5 Bridge installieren oder aktualisieren.
#
# Direkt aus GitHub:
#   curl -sSL https://raw.githubusercontent.com/Devilwitha/HAaddons/main/rpi5-bridge/install.sh | sudo bash
# Oder lokal:
#   sudo bash rpi5-bridge/install.sh
#
# Erneut ausführen = aktualisieren. Die Konfiguration in /etc/switch2-bridge/config.yaml bleibt erhalten.

set -euo pipefail

REPO_URL="https://github.com/Devilwitha/HAaddons.git"
BRANCH="${SWITCH2_BRANCH:-main}"
REPO_DIR="/opt/switch2-remote-play"
APP_DIR="/opt/switch2-bridge"
VENV="${APP_DIR}/venv"
CONF_DIR="/etc/switch2-bridge"
SERVICE="switch2-bridge"
AGENT_SERVICE="switch2-bt-agent"
WS_PORT=8765
VIDEO_PORT=8766

info() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

main() {
  # 1. Root-Rechte
  if [ "${EUID}" -ne 0 ]; then
    fail "Bitte mit Root-Rechten ausführen: curl -sSL https://raw.githubusercontent.com/Devilwitha/HAaddons/main/rpi5-bridge/install.sh | sudo bash"
  fi
  if [ -r /proc/device-tree/model ] && ! tr -d '\0' < /proc/device-tree/model | grep -q "Raspberry Pi 5"; then
    warn "Kein Raspberry Pi 5 erkannt ($(tr -d '\0' < /proc/device-tree/model)) - die Installation läuft trotzdem weiter."
  fi

  # 2. Systempakete
  info "Installiere Systempakete ..."
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -q < /dev/null
  apt-get install -y -q \
    git python3 python3-venv python3-pip python3-dev \
    bluez bluez-tools libdbus-glib-1-dev libdbus-1-dev python3-dbus \
    python3-opencv python3-numpy v4l-utils ffmpeg < /dev/null

  # 3. Repository holen oder aktualisieren (BolliSoft-Add-on-Repository, nur der neueste Stand).
  #    Lokale Änderungen in ${REPO_DIR} werden dabei verworfen - Einstellungen liegen in ${CONF_DIR}.
  if [ -d "${REPO_DIR}/.git" ]; then
    info "Aktualisiere ${REPO_DIR} ..."
    git -C "${REPO_DIR}" fetch --quiet --depth 1 origin "${BRANCH}"
    git -C "${REPO_DIR}" reset --quiet --hard FETCH_HEAD
  else
    info "Lade ${REPO_URL} nach ${REPO_DIR} ..."
    rm -rf "${REPO_DIR}"
    git clone --quiet --depth 1 --branch "${BRANCH}" "${REPO_URL}" "${REPO_DIR}"
  fi
  local src="${REPO_DIR}/rpi5-bridge"

  # 4. Python-Umgebung (mit System-Paketen, damit python3-opencv und python3-dbus nutzbar sind)
  info "Richte Python-Umgebung in ${VENV} ein ..."
  mkdir -p "${APP_DIR}"
  if [ ! -x "${VENV}/bin/python" ]; then
    python3 -m venv --system-site-packages "${VENV}"
  fi
  "${VENV}/bin/pip" install --quiet --upgrade pip wheel
  "${VENV}/bin/pip" install --quiet -r "${src}/requirements.txt"
  # nxbt ohne seine veralteten, festgenagelten Abhängigkeiten (siehe requirements.txt)
  "${VENV}/bin/pip" install --quiet --no-deps "nxbt==0.1.4"
  "${VENV}/bin/python" -c "import nxbt, cv2, aiortc, aiohttp, websockets, yaml, dbus" \
    || fail "Python-Pakete fehlen - Ausgabe oben prüfen."
  if ! command -v hciconfig > /dev/null 2>&1; then
    warn "hciconfig fehlt - nxbt braucht es für die Bluetooth-Klasse des Controllers."
    warn "Unter Raspberry Pi OS ist es im Paket 'bluez' enthalten; ggf. 'sudo apt-get install --reinstall bluez'."
  fi

  # Konfiguration (wird bei Updates nicht überschrieben)
  mkdir -p "${CONF_DIR}" /var/lib/switch2-bridge
  if [ ! -f "${CONF_DIR}/config.yaml" ]; then
    cp "${src}/config.yaml" "${CONF_DIR}/config.yaml"
    info "Konfiguration angelegt: ${CONF_DIR}/config.yaml"
  else
    info "Konfiguration bleibt erhalten: ${CONF_DIR}/config.yaml"
  fi

  # 5. Bluetooth und automatischer Kopplungs-Agent (keine Bestätigung im Terminal nötig)
  info "Konfiguriere Bluetooth ..."
  systemctl enable --now bluetooth
  if [ -f /etc/bluetooth/main.conf ]; then
    if grep -qE '^\s*#?\s*JustWorksRepairing\s*=' /etc/bluetooth/main.conf; then
      sed -i -E 's/^\s*#?\s*JustWorksRepairing\s*=.*/JustWorksRepairing = always/' /etc/bluetooth/main.conf
    else
      sed -i -E '0,/^\[General\]/s//[General]\nJustWorksRepairing = always/' /etc/bluetooth/main.conf
    fi
  fi
  cat > "/etc/systemd/system/${AGENT_SERVICE}.service" <<UNIT
[Unit]
Description=Game Remote - Bluetooth-Kopplung ohne Bestätigung
After=bluetooth.service
Requires=bluetooth.service
PartOf=bluetooth.service

[Service]
Type=simple
ExecStart=/usr/bin/bt-agent --capability=NoInputNoOutput
Restart=always
RestartSec=2

[Install]
WantedBy=bluetooth.target
UNIT

  # 6. Dienst einrichten und (neu) starten
  info "Richte systemd-Dienst ${SERVICE} ein ..."
  cp "${src}/systemd/${SERVICE}.service" "/etc/systemd/system/${SERVICE}.service"
  systemctl daemon-reload
  systemctl enable --now "${AGENT_SERVICE}"
  systemctl enable "${SERVICE}"
  systemctl restart "${AGENT_SERVICE}" "${SERVICE}"
  sleep 3
  if ! systemctl is-active --quiet "${SERVICE}"; then
    warn "Der Dienst läuft noch nicht. Protokoll ansehen: journalctl -u ${SERVICE} -n 50"
  fi

  if command -v v4l2-ctl > /dev/null 2>&1 && ! v4l2-ctl --list-devices 2>/dev/null | grep -q "/dev/video"; then
    warn "Keine Capture-Card gefunden. USB-Capture-Card anschließen (Standard: /dev/video0)."
  fi

  # 7. Zusammenfassung
  local ip
  ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
  [ -n "${ip}" ] || ip="<IP des Raspberry Pi>"
  printf '\n\033[1;32m==========================================================\033[0m\n'
  printf '\033[1;32m  Game Remote Bridge ist installiert.\033[0m\n\n'
  printf '  In Home Assistant (Add-on "Game Remote" -> Konfiguration):\n\n'
  printf '    pi_host:        \033[1m%s\033[0m\n' "${ip}"
  printf '    pi_ws_port:     \033[1m%s\033[0m\n' "${WS_PORT}"
  printf '    pi_video_port:  \033[1m%s\033[0m\n\n' "${VIDEO_PORT}"
  printf '  Status:       sudo systemctl status %s\n' "${SERVICE}"
  printf '  Protokoll:    journalctl -u %s -f\n' "${SERVICE}"
  printf '  Einstellungen: %s/config.yaml\n' "${CONF_DIR}"
  printf '\033[1;32m==========================================================\033[0m\n'
}

main "$@"
