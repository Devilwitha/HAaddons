#!/usr/bin/env bash
# HA PC Agent – Ein-Zeilen-Installer für Raspberry Pi / Debian-basierte Linux.
#
# Direkt aus GitHub ausführen:
#   curl -fsSL https://raw.githubusercontent.com/Devilwitha/HAaddons/main/pc_agent/install_pi.sh | bash
#
# Holt den Agent aus GitHub, installiert Systempakete + Python-Abhängigkeiten,
# fragt die Home-Assistant-Zugangsdaten ab und richtet ihn als Autostart-Dienst
# ein. Erneutes Ausführen aktualisiert eine vorhandene Installation.
set -euo pipefail

REPO_URL="https://github.com/Devilwitha/HAaddons.git"
BRANCH="main"
DEST="$HOME/.local/share/ha-pc-agent"
APPDIR="$DEST/pc_agent"
CFG="$HOME/.config/ha-pc-agent/config.json"
TTY=/dev/tty

say()  { printf '\033[1;36m>> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m!! %s\033[0m\n' "$*"; }

# --------------------------------------------------------------- Systempakete
if command -v apt-get >/dev/null 2>&1; then
    say "Systempakete installieren (sudo kann nach dem Passwort fragen) …"
    sudo apt-get update -qq || true
    # Pflicht: python + git ; Kür: Screenshot/Audio/Medientasten (Fehler ignorieren)
    sudo apt-get install -y -qq git python3 python3-venv python3-tk python3-pip || true
    sudo apt-get install -y -qq grim scrot playerctl wireplumber pulseaudio-utils 2>/dev/null || true
else
    warn "Kein apt gefunden – bitte git, python3, python3-venv selbst installieren."
fi

# --------------------------------------------------------------- Code aus GitHub
if [ -d "$DEST/.git" ]; then
    say "Vorhandene Installation aktualisieren …"
    git -C "$DEST" fetch --depth 1 origin "$BRANCH" -q
    git -C "$DEST" reset --hard "origin/$BRANCH" -q
else
    say "Agent aus GitHub laden …"
    rm -rf "$DEST"
    mkdir -p "$(dirname "$DEST")"
    git clone --depth 1 --branch "$BRANCH" -q "$REPO_URL" "$DEST"
fi

# --------------------------------------------------------------- venv + Abhängigkeiten
say "Python-Umgebung einrichten …"
cd "$APPDIR"
python3 -m venv --system-site-packages venv
PY="$APPDIR/venv/bin/python"
"$PY" -m pip install --quiet --upgrade pip
"$PY" -m pip install --quiet -r requirements.txt

# --------------------------------------------------------------- Konfiguration abfragen
ask() {  # ask "Frage" "Vorgabe" -> Antwort auf stdout
    local prompt="$1" def="$2" ans=""
    if [ -r "$TTY" ]; then
        if [ -n "$def" ]; then printf '%s [%s]: ' "$prompt" "$def" >"$TTY"
        else printf '%s: ' "$prompt" >"$TTY"; fi
        IFS= read -r ans <"$TTY" || true
    fi
    printf '%s' "${ans:-$def}"
}

if [ ! -f "$CFG" ] && [ -r "$TTY" ]; then
    say "Home Assistant einrichten (Enter = Vorgabe übernehmen):"
    MQ_HOST=$(ask "MQTT Host/IP des Home Assistant" "")
    MQ_PORT=$(ask "MQTT Port" "1883")
    MQ_USER=$(ask "MQTT Benutzer (dein HA-Login)" "")
    printf 'MQTT Passwort: ' >"$TTY"; IFS= read -rs MQ_PASS <"$TTY" || true; printf '\n' >"$TTY"
    MQ_NAME=$(ask "Gerätename in HA" "$(hostname)")
    mkdir -p "$(dirname "$CFG")"
    MQ_HOST="$MQ_HOST" MQ_PORT="$MQ_PORT" MQ_USER="$MQ_USER" MQ_PASS="${MQ_PASS:-}" MQ_NAME="$MQ_NAME" "$PY" - "$CFG" <<'PY'
import json, os, sys
cfg = {
    "mqtt": {"host": os.environ["MQ_HOST"], "port": int(os.environ["MQ_PORT"] or 1883),
             "username": os.environ["MQ_USER"], "password": os.environ["MQ_PASS"]},
    "device_name": os.environ["MQ_NAME"],
    "screenshot": {"enabled": True, "interval_seconds": 10, "max_width": 1280,
                   "jpeg_quality": 55, "all_monitors": True},
    "sensors_interval_seconds": 15,
    "allow_power_commands": True, "media_controls": True, "launchers": [],
}
json.dump(cfg, open(sys.argv[1], "w"), indent=2, ensure_ascii=False)
print("gespeichert:", sys.argv[1])
PY
else
    [ -f "$CFG" ] && say "Konfiguration vorhanden – unverändert ($CFG)." || \
        warn "Keine Eingabe möglich – Einstellungen später im Fenster oder in $CFG setzen."
fi

# --------------------------------------------------------------- Autostart-Dienst
say "Autostart-Dienst einrichten (systemd --user) …"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
mkdir -p "$HOME/.config/systemd/user"
cat > "$HOME/.config/systemd/user/ha-pc-agent.service" <<UNIT
[Unit]
Description=HA PC Agent
After=network-online.target graphical-session.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=$PY $APPDIR/agent.py --service
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
UNIT

systemctl --user daemon-reload
# Auch ohne aktive Anmeldung beim Booten starten (headless)
sudo loginctl enable-linger "$(id -un)" 2>/dev/null || loginctl enable-linger "$(id -un)" 2>/dev/null || true
systemctl --user enable --now ha-pc-agent.service || true

echo ""
say "Fertig."
echo "   Status:        systemctl --user status ha-pc-agent"
echo "   Neu starten:   systemctl --user restart ha-pc-agent"
echo "   Einstellungen: $CFG   (oder im Desktop-Modus: $PY $APPDIR/agent.py)"
echo "   Aktualisieren: curl -fsSL https://raw.githubusercontent.com/Devilwitha/HAaddons/main/pc_agent/install_pi.sh | bash"
if ! command -v grim >/dev/null 2>&1 && [ -z "${WAYLAND_DISPLAY:-}" ] && [ -z "${DISPLAY:-}" ]; then
    warn "Headless erkannt: Sensoren & Energie funktionieren; Screenshots brauchen eine aktive Desktop-Sitzung."
fi
