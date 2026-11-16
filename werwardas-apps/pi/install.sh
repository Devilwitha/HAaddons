#!/usr/bin/env bash
# WerWarDas – Installer für den Raspberry Pi (Raspberry Pi OS Bookworm/Trixie, auch Lite)
#
#   curl -fsSL https://raw.githubusercontent.com/Devilwitha/werwardas/main/pi/install.sh | sudo bash
#
# Optionen:
#   --reconfigure   Einrichtungs-Assistent erneut ausführen
#   --uninstall     Dienst und Programm entfernen (Konfiguration bleibt)
#
# Erneutes Ausführen aktualisiert das Programm und behält die Konfiguration.

set -euo pipefail

REPO_URL="${WERWARDAS_REPO:-https://github.com/Devilwitha/werwardas.git}"
BRANCH="${WERWARDAS_BRANCH:-main}"
APP_DIR=/opt/werwardas
CONF_DIR=/etc/werwardas
CONF_FILE=$CONF_DIR/config.yaml
DATA_DIR=/var/lib/werwardas
SERVICE_USER=werwardas
SERVICE_FILE=/etc/systemd/system/werwardas.service

green() { printf '\033[1;32m%s\033[0m\n' "$*"; }
yellow() { printf '\033[1;33m%s\033[0m\n' "$*"; }
red() { printf '\033[1;31m%s\033[0m\n' "$*" >&2; }
step() { echo; green "==> $*"; }

# Eingaben auch dann vom Terminal lesen, wenn das Skript per "curl | bash" läuft.
ask() {
    local prompt=$1 default=${2:-} answer
    if [[ -r /dev/tty ]]; then
        read -r -p "$prompt${default:+ [$default]}: " answer </dev/tty || answer=""
    else
        answer=""
    fi
    echo "${answer:-$default}"
}

ask_secret() {
    local prompt=$1 answer
    if [[ -r /dev/tty ]]; then
        read -r -s -p "$prompt: " answer </dev/tty || answer=""
        echo >/dev/tty
    else
        answer=""
    fi
    echo "$answer"
}

yaml_quote() {
    # Wert sicher als YAML-String in doppelten Anführungszeichen ausgeben
    local s=${1//\\/\\\\}
    s=${s//\"/\\\"}
    printf '"%s"' "$s"
}

require_root() {
    if [[ $EUID -ne 0 ]]; then
        red "Bitte mit sudo ausführen:  curl -fsSL <url>/install.sh | sudo bash"
        exit 1
    fi
}

uninstall() {
    step "Entferne WerWarDas"
    systemctl disable --now werwardas.service 2>/dev/null || true
    rm -f "$SERVICE_FILE"
    systemctl daemon-reload
    rm -rf "$APP_DIR"
    rm -f /usr/local/bin/werwardas
    green "Entfernt. Konfiguration ($CONF_DIR) und Fotos ($DATA_DIR) wurden behalten."
    exit 0
}

install_packages() {
    step "Installiere Systempakete"
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    local pkgs=(git python3 python3-venv python3-pip python3-numpy python3-pil python3-yaml)
    if apt-cache show python3-picamera2 >/dev/null 2>&1; then
        pkgs+=(python3-picamera2)
    else
        yellow "python3-picamera2 nicht verfügbar – nur USB/HTTP-Kameras möglich."
    fi
    apt-get install -y -qq --no-install-recommends "${pkgs[@]}" >/dev/null
}

fetch_code() {
    step "Lade Programm nach $APP_DIR"
    local src
    src="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd)" || src=""
    if [[ -n "$src" && -f "$src/werwardas/__init__.py" ]]; then
        # Skript wurde aus einem ausgecheckten Repo gestartet -> diese Dateien nehmen
        mkdir -p "$APP_DIR"
        rm -rf "$APP_DIR/pi"
        cp -r "$src" "$APP_DIR/pi"
    elif [[ -d "$APP_DIR/.git" ]]; then
        git -C "$APP_DIR" fetch -q --depth 1 origin "$BRANCH"
        git -C "$APP_DIR" reset -q --hard "origin/$BRANCH"
    else
        rm -rf "$APP_DIR"
        git clone -q --depth 1 --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
    fi
}

setup_venv() {
    step "Richte Python-Umgebung ein"
    if [[ ! -x "$APP_DIR/venv/bin/python" ]]; then
        # system-site-packages: picamera2, numpy und Pillow kommen aus apt
        python3 -m venv --system-site-packages "$APP_DIR/venv"
    fi
    "$APP_DIR/venv/bin/pip" install -q --upgrade pip
    "$APP_DIR/venv/bin/pip" install -q -r "$APP_DIR/pi/requirements.txt"
    if ! "$APP_DIR/venv/bin/pip" install -q -r "$APP_DIR/pi/requirements-claude.txt"; then
        yellow "Claude-Paket konnte nicht installiert werden – Bewertung 'claude' ist nicht verfügbar."
    fi
    cat >/usr/local/bin/werwardas <<EOF
#!/bin/sh
cd $APP_DIR/pi && exec $APP_DIR/venv/bin/python -m werwardas "\$@"
EOF
    chmod 755 /usr/local/bin/werwardas
}

setup_user() {
    if ! id "$SERVICE_USER" >/dev/null 2>&1; then
        useradd --system --home-dir "$DATA_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
    fi
    usermod -a -G video "$SERVICE_USER"
    mkdir -p "$DATA_DIR" "$CONF_DIR"
    chown -R "$SERVICE_USER:$SERVICE_USER" "$DATA_DIR"
}

configure() {
    step "Einrichtung"
    echo "Die Werte kannst du später in $CONF_FILE ändern."
    echo
    echo "MQTT-Broker (in Home Assistant: Add-on 'Mosquitto broker' + eigener HA-Benutzer)"
    local host port user pass cam_type cam_url analyzer ollama_url ollama_model claude_key interval
    host=$(ask "  Adresse von Home Assistant / MQTT" "homeassistant.local")
    port=$(ask "  Port" "1883")
    user=$(ask "  Benutzer" "werwardas")
    pass=$(ask_secret "  Passwort")

    echo
    echo "Kamera:  1) Raspberry-Pi-Kamera   2) USB-Webcam   3) ESP32-CAM / Netzwerkkamera (HTTP)"
    case $(ask "  Auswahl" "1") in
        2) cam_type=usb ;;
        3) cam_type=http; cam_url=$(ask "  Snapshot-URL (z. B. http://192.168.1.50/capture)" "") ;;
        *) cam_type=picamera2 ;;
    esac
    if [[ $cam_type == usb ]] && ! python3 -c "import cv2" 2>/dev/null; then
        apt-get install -y -qq --no-install-recommends python3-opencv >/dev/null
    fi

    echo
    echo "Bewertung der Ordnung:"
    echo "  1) Referenzfoto-Vergleich  (offline, keine KI, sofort startklar)"
    echo "  2) Ollama                 (lokale KI, z. B. auf deinem PC – empfohlen: qwen2.5vl:7b)"
    echo "  3) Claude                 (Cloud-KI, API-Key nötig, beste Qualität)"
    case $(ask "  Auswahl" "1") in
        2)
            analyzer=ollama
            ollama_url=$(ask "  Ollama-Adresse" "http://192.168.1.20:11434")
            ollama_model=$(ask "  Modell" "qwen2.5vl:7b")
            ;;
        3)
            analyzer=claude
            claude_key=$(ask_secret "  Claude API-Key")
            ;;
        *) analyzer=reference ;;
    esac
    interval=$(ask "Foto-Intervall in Minuten" "30")

    touch "$CONF_FILE"
    chmod 600 "$CONF_FILE"
    cat >"$CONF_FILE" <<EOF
# WerWarDas – Konfiguration. Alle Optionen: $APP_DIR/pi/config.example.yaml
# Nach Änderungen:  sudo systemctl restart werwardas

mqtt:
  host: $(yaml_quote "$host")
  port: ${port}
  username: $(yaml_quote "$user")
  password: $(yaml_quote "$pass")

camera:
  type: ${cam_type}
  url: $(yaml_quote "${cam_url:-}")
  rotation: 0

interval_minutes: ${interval}
active_hours: "08:00-22:00"
threshold: 60
analyzer: ${analyzer}
prompt_hint: ""

ollama:
  url: $(yaml_quote "${ollama_url:-http://localhost:11434}")
  model: $(yaml_quote "${ollama_model:-qwen2.5vl:7b}")

claude:
  api_key: $(yaml_quote "${claude_key:-}")
  model: claude-opus-5-5

storage:
  dir: $DATA_DIR
  keep_days: 7
EOF
    chown root:"$SERVICE_USER" "$CONF_FILE"
    chmod 640 "$CONF_FILE"
}

install_service() {
    step "Richte Systemdienst ein"
    cat >"$SERVICE_FILE" <<EOF
[Unit]
Description=WerWarDas Aufräum-Wächter
After=network-online.target
Wants=network-online.target

[Service]
User=$SERVICE_USER
Group=$SERVICE_USER
SupplementaryGroups=video
WorkingDirectory=$APP_DIR/pi
ExecStart=$APP_DIR/venv/bin/python -m werwardas -c $CONF_FILE
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    systemctl enable -q werwardas.service
    systemctl restart werwardas.service
}

test_camera() {
    step "Teste die Kamera"
    systemctl stop werwardas.service
    if runuser -u "$SERVICE_USER" -- /usr/local/bin/werwardas -c "$CONF_FILE" --test-camera "$DATA_DIR/test.jpg"; then
        green "Kamera funktioniert – Testfoto: $DATA_DIR/test.jpg"
    else
        yellow "Kamera-Test fehlgeschlagen. Prüfe Anschluss und camera.type in $CONF_FILE."
    fi
    systemctl start werwardas.service
}

main() {
    require_root
    case "${1:-}" in
        --uninstall) uninstall ;;
    esac

    green "WerWarDas – Aufräum-Wächter – Installation"
    install_packages
    fetch_code
    setup_venv
    setup_user
    if [[ ! -f "$CONF_FILE" || "${1:-}" == "--reconfigure" ]]; then
        configure
    else
        step "Vorhandene Konfiguration $CONF_FILE wird behalten (neu einrichten: --reconfigure)"
    fi
    install_service
    test_camera

    echo
    green "Fertig!"
    cat <<EOF

  Status:        systemctl status werwardas
  Log ansehen:   journalctl -u werwardas -f
  Konfiguration: sudo nano $CONF_FILE   (danach: sudo systemctl restart werwardas)
  Update:        dieses Skript einfach erneut ausführen

  In Home Assistant erscheint jetzt automatisch das Gerät "WerWarDas Kamera"
  (Einstellungen -> Geräte & Dienste -> MQTT).
  Bei Bewertung "Referenzfoto": Zimmer aufräumen und dort "Als aufgeräumt speichern" drücken.
EOF
}

# WERWARDAS_NO_MAIN=1 erlaubt es, die Funktionen in Tests zu laden.
if [[ -z "${WERWARDAS_NO_MAIN:-}" ]]; then
    main "$@"
fi
