# HA PC Agent (Windows & Linux)

Meldet deinen PC an dein eigenes Home Assistant – vergleichbar mit HASS.Agent.
Läuft unter **Windows** und **Linux** (inkl. **Steam Deck / SteamOS** und
**Bazzite**) als Fenster-App mit Infobereich-Symbol (Tray), verbindet sich per
MQTT und legt in HA automatisch ein Gerät an (MQTT-Discovery). Es wird nur an das
in den Einstellungen eingetragene Home Assistant gesendet, sonst nirgendwohin.

## Was in Home Assistant erscheint
- **Kamera „Bildschirm“** – Screenshot des Desktops im eingestellten Takt
- **Sensoren** – CPU, RAM, Temperatur, aktives Fenster, Betriebszeit (Akku nur bei
  Laptops; Temperatur unter Linux immer, unter Windows nur mit Sensorquelle wie
  LibreHardwareMonitor)
- **Knöpfe** – Herunterfahren, Neustart, Sperren, Energie sparen, Herunterfahren abbrechen
- **Medien & Ton** – Lautstärke-Regler, Ton stumm, Mikrofon stumm, Wiedergabe/Pause,
  nächster/vorheriger Titel
- **Programme, Spiele & Links** – eigene Knöpfe, die ein Programm, eine Datei/einen
  Ordner, ein **Steam-Spiel** oder einen **Link** (z. B. Epic-, Ubisoft-, GOG-Spiel,
  Webseite) auf dem PC öffnen
- **Schalter** – Screenshots senden an/aus

## Bedienung
- **Doppelklick auf die .exe** öffnet das **Einstellungsfenster**.
- **Fenster schließen** beendet das Programm **nicht** – es läuft im Infobereich
  (unten rechts neben der Uhr) weiter.
- **Rechtsklick aufs Tray-Symbol:** Einstellungen öffnen, Screenshots pausieren, Beenden.
- Im Fenster: MQTT-Zugang, Gerätename, Screenshot-Takt/Größe, Sensoren-Takt,
  Abschaltbefehle erlauben, **„Automatisch mit Windows starten“**, sowie
  **„Verbindung testen“** und **„Speichern & Anwenden“**.
- **Programme, Spiele & Links:** Unter „Programm/Datei …“ eine `.exe`, `.lnk` oder
  `.url` wählen, „Steam-Spiel …“ für eine Liste deiner installierten Steam-Spiele, oder
  „Link …“ für einen beliebigen Link mit eigenem Namen, z. B.
  `com.epicgames.launcher://apps/<ID>?action=launch&silent=true`, `uplay://launch/<ID>/0`
  oder `https://…`. Jeder Eintrag wird nach „Speichern & Anwenden“ zu einem Knopf in HA,
  der ihn öffnet. Spiele-Links bekommen ein Gamepad-Symbol, Webseiten ein Globus-Symbol.
  - **Tipp für Epic/Ubisoft/GOG:** Im Launcher eine Desktop-Verknüpfung zum Spiel anlegen
    und diese `.url`-Datei über „Programm/Datei …“ wählen – der Link wird daraus übernommen.
  - Der jeweilige Launcher (Steam, Epic …) muss installiert und angemeldet sein.

## Variante A – fertige .exe bauen (empfohlen)
Einmalig, danach brauchst du kein Python mehr auf dem Ziel-PC.
1. Ordner `pc_agent` auf einen PC mit Python 3 kopieren.
2. PowerShell im Ordner öffnen:
   ```powershell
   powershell -ExecutionPolicy Bypass -File build.ps1
   ```
3. Ergebnis: `dist\HA PC Agent.exe`. Diese Datei irgendwohin kopieren und
   doppelklicken. Beim ersten Start öffnet sich das Einstellungsfenster.

### Automatische Updates (Windows-.exe)
Die .exe prüft kurz nach dem Start und dann **jede Stunde**, ob auf GitHub ein neueres
**Release** liegt, lädt es und tauscht sich selbst aus (Programm startet kurz neu).
Abschaltbar im Fenster („Automatisch über GitHub aktualisieren"); dort auch „Update suchen".

**Neue Version veröffentlichen:** Nummer in `version.py` erhöhen und nach `main` pushen.
Der GitHub-Workflow `.github/workflows/pc-agent-release.yml` baut dann die .exe und
legt das Release `pcagent-v<Version>` an. Spätestens eine Stunde später haben sich alle
installierten .exe aktualisiert. Unter *Actions → PC Agent Release → Run workflow*
lässt sich der Build auch von Hand starten.

Alternativ lokal bauen und hochladen (GitHub CLI nötig, einmalig `gh auth login`):
```powershell
powershell -ExecutionPolicy Bypass -File build.ps1 -Release
```

> Hinweis: Eine bereits laufende alte .exe (ohne Update-Funktion) muss **einmalig**
> von Hand durch die neue ersetzt werden; ab dann aktualisiert sie sich selbst.

## Variante B – aus dem Quelltext starten (Windows)
1. `powershell -ExecutionPolicy Bypass -File install.ps1`
2. Starten: `& ".\venv\Scripts\pythonw.exe" ".\agent.py"`

## Raspberry Pi / Debian – ein Befehl (direkt aus GitHub)
Auf dem Pi ein Terminal öffnen und einfügen:
```bash
curl -fsSL https://raw.githubusercontent.com/Devilwitha/HAaddons/main/pc_agent/install_pi.sh | bash
```
Das installiert alles Nötige, fragt die Home-Assistant-Zugangsdaten ab (MQTT
Host/Benutzer/Passwort, Gerätename) und richtet den Agent als Autostart-Dienst
ein (`systemctl --user … ha-pc-agent`, läuft auch headless beim Booten).
Denselben Befehl erneut ausführen = aktualisieren. Auf einem Pi ohne Bildschirm
funktionieren Sensoren und Energie-Befehle; Screenshots brauchen eine laufende
Desktop-Sitzung.

## Linux (Steam Deck / SteamOS, Bazzite, …)
Am besten im **Desktop-Modus** einrichten (dort gibt es Fenster und Tray).
1. Ordner `pc_agent` auf das Gerät kopieren, Terminal im Ordner öffnen.
2. `bash install_linux.sh` (legt eine venv an und installiert alles).
   - Für dauerhaften Autostart als Hintergrunddienst: `bash install_linux.sh --service`.
3. Starten / Einstellungen öffnen: `./venv/bin/python agent.py`

Was wofür genutzt wird (fehlt ein Werkzeug, wird die Funktion einfach ausgeblendet):
- **Screenshots:** KDE `spectacle` (Wayland+X11), sonst `grim` (Wayland),
  `gnome-screenshot`/`scrot`/`maim` (X11). *SteamOS bringt spectacle mit.*
- **Lautstärke/Mikrofon:** `wpctl` (PipeWire) oder `pactl`. *Auf SteamOS/Bazzite vorhanden.*
- **Medientasten:** `playerctl` (ggf. nachinstallieren).
- **Energie:** `systemctl` / `loginctl` (Standard).
- **Aktives Fenster:** nur unter X11 bzw. mit `xdotool`/`kdotool` (unter reinem Wayland nicht ermittelbar).
- **Steam-Spiele:** erkennt native und Flatpak-Steam automatisch.

Autostart unter Linux ist ein Eintrag in `~/.config/autostart` (Desktop-Modus)
bzw. der `--service`-Dienst (`systemctl --user … ha-pc-agent`), der auch im
Spielemodus weiterläuft.

> SteamOS ist schreibgeschützt – die venv im Benutzerordner ist davon nicht
> betroffen. Fehlende Zusatzwerkzeuge per `pacman`/Flatpak/Homebrew (Bazzite)
> nachrüsten, falls du die jeweilige Funktion brauchst.

## Voraussetzungen
- Windows **oder** Linux. Für den Windows-Build (Variante A) Python 3 (python.org,
  „Add to PATH“). Unter Linux genügt `python3` + `python3-venv` (für das Fenster
  zusätzlich `python3-tk`).
- Ein MQTT-Broker, den HA nutzt. Das **Mosquitto-Add-on** reicht; als MQTT-Benutzer
  und -Passwort funktioniert dein normaler Home-Assistant-Login.

In Home Assistant erscheint das Gerät automatisch unter
**Einstellungen → Geräte & Dienste → MQTT**. Eine fertige Dashboard-Karte liegt in
[home_assistant/dashboard_card.yaml](home_assistant/dashboard_card.yaml).

## Wo liegen die Einstellungen?
- Windows: `%APPDATA%\HA-PC-Agent\config.json`
- Linux: `~/.config/ha-pc-agent/config.json`

Dort liegt auch `agent.log`. Die Werte änderst du am besten im Fenster, nicht von Hand.

## Sicherheit
- Der Agent kann deinen PC herunterfahren/neustarten und überträgt Bildschirmfotos
  in dein HA. Gib HA-Zugriff nur Personen, denen du das erlauben willst.
- Screenshots sind nicht „retained“, bleiben also nicht im Broker liegen.
- Screenshots jederzeit pausierbar – im Fenster, im Tray-Menü oder aus HA.
- Ohne „Herunterfahren … erlauben“ werden keine Abschalt-Knöpfe angelegt.
- Entfernen: Autostart abwählen (Windows: oder `uninstall.ps1`; Linux-Dienst:
  `systemctl --user disable --now ha-pc-agent`), Programm beenden, in HA das Gerät löschen.
