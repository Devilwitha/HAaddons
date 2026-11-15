#!/usr/bin/env bash
# Game Remote - Raspberry Pi 5 Bridge entfernen.
#
#   sudo bash /opt/switch2-remote-play/rpi5-bridge/uninstall.sh            Dienst und Programm entfernen
#   sudo bash /opt/switch2-remote-play/rpi5-bridge/uninstall.sh --purge    zusätzlich Konfiguration und
#                                                                          gespeicherte Switch-Adresse löschen
# Systempakete (bluez, ffmpeg, OpenCV ...) bleiben installiert.

set -euo pipefail

APP_DIR="/opt/switch2-bridge"
REPO_DIR="/opt/switch2-remote-play"
CONF_DIR="/etc/switch2-bridge"
STATE_DIR="/var/lib/switch2-bridge"
SERVICE="switch2-bridge"
AGENT_SERVICE="switch2-bt-agent"
NXBT_OVERRIDE="/run/systemd/system/bluetooth.service.d/nxbt.conf"

info() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }

main() {
  if [ "${EUID}" -ne 0 ]; then
    echo "Bitte mit sudo ausführen: sudo bash $0" >&2
    exit 1
  fi
  local purge=0
  [ "${1:-}" = "--purge" ] && purge=1

  info "Stoppe und deaktiviere Dienste ..."
  for unit in "${SERVICE}" "${AGENT_SERVICE}"; do
    systemctl stop "${unit}" 2>/dev/null || true
    systemctl disable "${unit}" 2>/dev/null || true
    rm -f "/etc/systemd/system/${unit}.service"
  done
  systemctl daemon-reload

  # nxbt startet Bluetooth mit abgeschalteten Plugins neu - wieder normal starten
  if [ -f "${NXBT_OVERRIDE}" ]; then
    info "Setze Bluetooth-Dienst zurück ..."
    rm -f "${NXBT_OVERRIDE}"
    systemctl daemon-reload
    systemctl restart bluetooth || true
  fi

  info "Entferne ${APP_DIR} und ${REPO_DIR} ..."
  rm -rf "${APP_DIR}" "${REPO_DIR}"

  if [ "${purge}" -eq 1 ]; then
    info "Entferne Konfiguration und gespeicherte Switch-Adresse ..."
    rm -rf "${CONF_DIR}" "${STATE_DIR}"
  else
    info "Konfiguration bleibt in ${CONF_DIR} (mit --purge ebenfalls löschen)."
  fi

  info "Game Remote Bridge wurde entfernt."
}

main "$@"
