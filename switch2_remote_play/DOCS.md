# Game Remote für Home Assistant

*Von BolliSoft.* Remote Play für die Nintendo Switch 2.

Spiele deine Nintendo Switch 2 aus der Home-Assistant-Seitenleiste – am Handy, Tablet oder PC.
Ein Raspberry Pi 5 neben der Konsole nimmt das Bild über eine USB-Capture-Card auf und gibt sich
per Bluetooth als Pro Controller aus. Home Assistant zeigt das Video und ein Touch-Gamepad an;
echte Controller (Xbox, PlayStation u. a.) am Handy oder PC funktionieren ebenfalls.

```
Switch 2 ──HDMI──▶ Capture-Card ──USB──▶ Raspberry Pi 5 ◀──Bluetooth── (Pro Controller)
                                              │
                         WebSocket 8765 / Video 8766 (Heimnetz)
                                              │
                         Home Assistant Add-on "Game Remote" (Ingress)
                                              │
                                Browser / Home-Assistant-App
```

## Was du brauchst
- Nintendo Switch 2 im Dock (HDMI-Ausgang)
- Raspberry Pi 5 mit Raspberry Pi OS (Bookworm oder neuer), im selben Netz wie Home Assistant
- USB-Capture-Card (UVC, z. B. 1080p-HDMI-Stick), angeschlossen an einen USB-3-Port des Pi
- Home Assistant mit Add-on-Unterstützung (Home Assistant OS oder Supervised)

## Schritt 1: Raspberry Pi 5 einrichten
Auf dem Pi ein Terminal öffnen (oder per SSH) und ausführen:

```bash
curl -sSL https://raw.githubusercontent.com/Devilwitha/HAaddons/main/rpi5-bridge/install.sh | sudo bash
```

Das Skript installiert alle Pakete, lädt das BolliSoft-Add-on-Repository nach `/opt/switch2-remote-play`, richtet
die Python-Umgebung in `/opt/switch2-bridge/venv` ein, konfiguriert Bluetooth samt automatischer
Kopplung und startet den Dienst `switch2-bridge`. Am Ende zeigt es die IP-Adresse des Pi und die
Ports **8765** (WebSocket) und **8766** (Video) an – die brauchst du in Schritt 2.

Denselben Befehl erneut ausführen = aktualisieren. Einstellungen (Auflösung, Bildrate, Gerät)
stehen in `/etc/switch2-bridge/config.yaml`, danach `sudo systemctl restart switch2-bridge`.

## Schritt 2: Home Assistant
1. Falls noch nicht geschehen: **Einstellungen → Apps → Add-on Store → ⋮ → Repositories** und
   `https://github.com/Devilwitha/HAaddons` hinzufügen (das BolliSoft-Repository).
2. Im Add-on Store **⋮ → Nach Updates suchen**, dann die App **Game Remote** installieren.
3. Unter **Konfiguration** die IP des Pi bei `pi_host` eintragen (Ports nur ändern, wenn du sie auf
   dem Pi geändert hast) und die App starten.
4. **Switch 2** in der Seitenleiste öffnen.

| Option | Bedeutung |
|---|---|
| `pi_host` | IP-Adresse oder Hostname des Raspberry Pi 5 |
| `pi_ws_port` | WebSocket-Port der Bridge (Standard 8765) |
| `pi_video_port` | Video-Port der Bridge (Standard 8766) |
| `stream_mode` | `webrtc` (geringste Latenz) oder `mjpeg` (funktioniert überall) |
| `default_touch_overlay` | Touch-Gamepad beim ersten Öffnen anzeigen |
| `haptic_feedback` | kurzes Vibrieren bei Tastendruck (Android) |

## Schritt 3: Erstes Koppeln
1. Auf der Switch 2 **Controller → Griffweise/Reihenfolge ändern** öffnen.
2. In Home Assistant oben auf **Switch koppeln** tippen.
3. Nach einigen Sekunden erscheint der Controller auf der Konsole, die Anzeige **Switch 2** wird grün.

Danach verbindet sich der Pi nach einem Neustart von selbst wieder, solange die Konsole an ist.

## Bedienung
- **Obere Leiste** (mit dem Pfeil einklappbar): Status von Bridge, Switch 2, Capture und Ping,
  Schalter **Touch-Steuerung**, **Controller**, **Switch koppeln**, **Vollbild**.
- **Touch-Gamepad:** zwei Analogsticks, A/B/X/Y, Steuerkreuz, L/ZL/R/ZR, −/+/HOME/Aufnahme.
  Mehrere Finger gleichzeitig (z. B. Stick und A) funktionieren.
- **Echte Controller:** per USB oder Bluetooth am Handy, Tablet oder PC verbinden und eine Taste
  drücken. Unter **Controller** stellst du die Belegung ein:
  - *Wie Switch-Anordnung:* die rechte Taste ist A (Xbox B bzw. PlayStation ○)
  - *Nach Beschriftung:* Xbox A = Switch A bzw. PlayStation ✕ = A
  - *Eigene Belegung:* bei einer Funktion auf **Ändern** tippen und die gewünschte Taste drücken
  - Beschriftung Xbox oder PlayStation (wird erkannt, lässt sich umstellen)
  Die Belegung gilt pro Gerät und Browser.
- **Vollbild:** Im eingebetteten HA-Fenster erlauben manche Browser kein Vollbild – dann die Seite
  in einem eigenen Browser-Tab öffnen.

## Gut zu wissen
- **WebRTC und unterwegs:** Über Home Assistant läuft bei WebRTC nur der Verbindungsaufbau; das
  Video selbst geht direkt vom Pi zum Browser. Das klappt im Heimnetz. Von unterwegs (z. B. über
  Nabu Casa) ist der Pi nicht direkt erreichbar – dann schaltet die Seite nach einigen Sekunden
  automatisch auf **MJPEG** um, das vollständig über Home Assistant läuft (mehr Bandbreite, etwas
  mehr Verzögerung).
- **Sicherheit:** Kommt 2 Sekunden lang kein Lebenszeichen vom Browser (Tab zu, Verbindung weg),
  lässt der Pi alle Tasten los und stellt die Sticks auf Mitte.
- **nxbt:** Die Pro-Controller-Emulation nutzt [nxbt](https://github.com/Brikwerk/nxbt) 0.1.4. Es
  wird ohne seine veralteten Abhängigkeiten installiert, weil nur der Bluetooth-Teil gebraucht wird.
  nxbt wurde für die erste Switch entwickelt; ob jede Firmware der Switch 2 den emulierten Controller
  annimmt, hängt von Nintendo ab.
- **Testbild:** In `/etc/switch2-bridge/config.yaml` `device: "test"` setzen, um Video ohne
  Capture-Card zu prüfen.

## Fehlersuche
| Anzeige | Ursache / Lösung |
|---|---|
| **Bridge** rot | Pi nicht erreichbar: IP in der App-Konfiguration prüfen, `sudo systemctl status switch2-bridge` |
| **Switch 2** gelb | Warte auf Kopplung: auf der Konsole *Griffweise/Reihenfolge ändern* öffnen |
| **Switch 2** rot | nxbt läuft nicht: `journalctl -u switch2-bridge -n 50` |
| **Capture** gelb | Karte gefunden, aber kein Bild: Switch 2 im Standby oder HDMI-Kabel prüfen |
| **Capture** rot | Keine Capture-Card: `v4l2-ctl --list-devices`, ggf. `device` in der config.yaml anpassen |
| Ruckelt | `webrtc_fps`/`mjpeg_fps` oder Auflösung in der config.yaml senken |

## Entfernen
```bash
sudo bash /opt/switch2-remote-play/rpi5-bridge/uninstall.sh          # Dienst und Programm
sudo bash /opt/switch2-remote-play/rpi5-bridge/uninstall.sh --purge  # zusätzlich Einstellungen
```

## Lizenzen und Marken
Nintendo Switch ist eine Marke von Nintendo. Dieses Projekt ist nicht mit Nintendo verbunden.
Verwendet werden nxbt (MIT), aiortc (BSD), aiohttp (Apache 2.0), websockets (BSD) und OpenCV
(Apache 2.0).
