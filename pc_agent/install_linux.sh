#!/usr/bin/env bash
# HA PC Agent – Einrichtung unter Linux (Steam Deck / SteamOS, Bazzite, …).
#
# Nutzung (im Ordner pc_agent):
#   bash install_linux.sh            # venv + Abhängigkeiten
#   bash install_linux.sh --service  # zusätzlich als Autostart-Dienst (systemd --user)
#
# Zum Einrichten im Desktop-Modus ausführen. Das Einstellungsfenster öffnet sich
# beim ersten Start (python3-tk nötig); danach läuft der Agent im Hintergrund.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

echo ">> Virtuelle Umgebung anlegen …"
python3 -m venv venv
# Zugriff auf System-Python-Pakete (z. B. gobject/gtk für das Tray-Symbol), falls vorhanden
if [ ! -f venv/pyvenv.cfg ] || ! grep -q "include-system-site-packages = true" venv/pyvenv.cfg; then
    python3 -m venv --system-site-packages venv
fi
./venv/bin/python -m pip install --quiet --upgrade pip
echo ">> Abhängigkeiten installieren …"
./venv/bin/python -m pip install --quiet -r requirements.txt

PY="$HERE/venv/bin/python"
echo ""
echo ">> Fertig. Agent starten mit:"
echo "   \"$PY\" \"$HERE/agent.py\""
echo ""

# Hinweise zu optionalen Systemwerkzeugen
missing=""
need_screenshot=0
for t in spectacle grim gnome-screenshot scrot; do command -v "$t" >/dev/null 2>&1 && need_screenshot=1; done
[ "$need_screenshot" = "0" ] && missing="$missing Screenshot(spectacle/grim)"
command -v wpctl >/dev/null 2>&1 || command -v pactl >/dev/null 2>&1 || missing="$missing Audio(wpctl/pactl)"
command -v playerctl >/dev/null 2>&1 || missing="$missing Medientasten(playerctl)"
if [ -n "$missing" ]; then
    echo ">> Optional nachinstallieren für volle Funktion:$missing"
    echo "   (SteamOS hat wpctl & spectacle bereits; playerctl ggf. nachrüsten.)"
    echo ""
fi

if [ "$1" = "--service" ]; then
    echo ">> Richte Autostart-Dienst ein (systemd --user) …"
    mkdir -p "$HOME/.config/systemd/user"
    cat > "$HOME/.config/systemd/user/ha-pc-agent.service" <<UNIT
[Unit]
Description=HA PC Agent
After=graphical-session.target network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=$PY $HERE/agent.py --service
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
UNIT
    systemctl --user daemon-reload
    systemctl --user enable --now ha-pc-agent.service || true
    echo "   Dienst aktiv. Status: systemctl --user status ha-pc-agent"
    echo "   Hinweis: Einmal die Einstellungen im Desktop-Modus setzen"
    echo "   (\"$PY\" \"$HERE/agent.py\"), bevor der Dienst sinnvoll läuft."
fi
