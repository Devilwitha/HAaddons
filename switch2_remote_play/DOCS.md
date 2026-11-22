# Game Remote für Home Assistant

*Von BolliSoft.* Fernsteuerung für deine Spielkonsole.

Spiele deine Spielkonsole aus der Home-Assistant-Seitenleiste – am Handy, Tablet oder PC.
Ein Raspberry Pi 5 neben der Konsole nimmt das Bild über eine USB-Capture-Card auf und gibt sich
per Bluetooth als Pro Controller aus. Home Assistant zeigt das Video und ein Touch-Gamepad an;
echte Controller (Xbox, PlayStation u. a.) am Handy oder PC funktionieren ebenfalls.

```
Konsole  ──HDMI──▶ Capture-Card ──USB──▶ Raspberry Pi 5 ◀──Bluetooth── (Pro Controller)
                                              │
                         WebSocket 8765 / Video 8766 (Heimnetz)
                                              │
                         Home Assistant Add-on "Game Remote" (Ingress)
                                              │
                                Browser / Home-Assistant-App
```

## Was du brauchst
- Spielkonsole mit HDMI-Ausgang (z. B. im Dock), die einen Bluetooth-Pro-Controller annimmt
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
4. **Game Remote** in der Seitenleiste öffnen.

| Option | Bedeutung |
|---|---|
| `pi_host` | IP-Adresse oder Hostname des Raspberry Pi 5 |
| `pi_ws_port` | WebSocket-Port der Bridge (Standard 8765) |
| `pi_video_port` | Video-Port der Bridge (Standard 8766) |
| `stream_mode` | `webrtc` (geringste Latenz) oder `mjpeg` (funktioniert überall) |
| `default_touch_overlay` | Touch-Gamepad beim ersten Öffnen anzeigen |
| `haptic_feedback` | kurzes Vibrieren bei Tastendruck (Android) |
| `use_mqtt` | Sensoren per MQTT als Gerät anlegen (Standard: an), sonst über die REST-API |

## Schritt 3: Erstes Koppeln
1. Auf der Konsole **Controller → Griffweise/Reihenfolge ändern** öffnen.
2. In Home Assistant oben auf **Konsole koppeln** tippen.
3. Bis zu einer Minute warten – nicht mehrfach drücken. Der Controller erscheint auf der Konsole, die
   Anzeige **Konsole** wird grün.

Nach dem Start der Bridge dauert es etwa eine Minute, bis Bluetooth bereit ist (Anzeige **Konsole**
„Controller wird gestartet“). Stürzt die Controller-Emulation ab, startet die Bridge sie von selbst neu.

Danach verbindet sich der Pi nach einem Neustart von selbst wieder, solange die Konsole an ist.

## Bedienung
- **Obere Leiste** (mit dem Pfeil einklappbar): Status von Bridge, Konsole, Capture und Ping,
  Schalter **Touch-Steuerung**, **Controller**, **Ton**, **Messwerte**, **Konsole koppeln**, **Vollbild**.
- **Ton:** kommt bei WebRTC von der Capture-Card mit (nicht bei MJPEG). Browser spielen Ton erst nach einer
  Berührung ab – einmal auf die Seite tippen. Bild und Ton werden nicht abgeglichen, damit das Bild nicht
  wartet; der Ton kann dadurch ein paar Millisekunden hinterherhängen. Abschalten am Pi:
  `audio: device: "off"` in `/etc/switch2-bridge/config.yaml`.
- **Touch-Gamepad:** zwei Analogsticks, A/B/X/Y, Steuerkreuz, L/ZL/R/ZR, −/+/HOME/Aufnahme.
  Mehrere Finger gleichzeitig (z. B. Stick und A) funktionieren.
- **Echte Controller:** per USB oder Bluetooth am Handy, Tablet oder PC verbinden und eine Taste
  drücken. Unter **Controller** stellst du die Belegung ein:
  - *Wie Konsolen-Anordnung:* die rechte Taste ist A (Xbox B bzw. PlayStation ○)
  - *Nach Beschriftung:* Xbox A = A bzw. PlayStation ✕ = A
  - *Eigene Belegung:* bei einer Funktion auf **Ändern** tippen und die gewünschte Taste drücken
  - Beschriftung Xbox, PlayStation oder Pro Controller (wird erkannt, lässt sich umstellen). Beim Pro
    Controller passt *Nach Beschriftung* (A = A) – die Vorlage ist dann schon eingestellt.
  Die Belegung gilt pro Gerät und Browser.
- **Messwerte:** oben **Bildqualität** (*720p · 60 fps* für wenig Verzögerung, *1080p · 30 fps* für ein
  schärferes Bild; der Pi merkt sich die Wahl) und **Capture-Card neu starten** (setzt den USB-Anschluss der
  Karte zurück, wie Aus- und Einstecken – hilft, wenn das Bild hängt oder die Karte kein Bild mehr liefert).
  Darunter, wo Verzögerung entsteht – am Pi (Bildrate der Capture-Card, gesendete Bilder,
  Bildalter, Kodieren), im Netz (Ping direkt und über Home Assistant) und im Browser (Datenrate, Puffer,
  Dekodieren, verworfene Bilder). Grün ist gut, gelb geht, rot bremst. Der **Reaktionstest** misst vom Tastendruck bis
  sich das Bild ändert (alles außer dem Bildschirm, inklusive Capture-Card und der Reaktion des Spiels).
  Eine Taste nehmen, die sofort etwas ändert (z. B. Menü öffnen), und mehrmals messen. Mit
  `device: "test"` am Pi zeigt das Testbild gedrückte Tasten an – so lässt sich die Strecke ohne
  Capture-Card und Konsole messen.
- **Vollbild:** Im eingebetteten HA-Fenster erlauben manche Browser kein Vollbild – dann die Seite
  in einem eigenen Browser-Tab öffnen.

## Sensoren in Home Assistant
Das Add-on legt ein Gerät **Game Remote** an (mit eingerichtetem MQTT-Broker, z. B. Mosquitto,
unter *Einstellungen → Geräte & Dienste → MQTT*; sonst als einzelne Entitäten):

| Sensor | Inhalt |
|---|---|
| Status | „Aktiv“, solange eine Steuerungsseite mit der Bridge verbunden ist, sonst „Ruhend“ |
| Spieler | Home-Assistant-Benutzer, der gerade spielt |
| Sitzung | Dauer der laufenden Sitzung |
| Gesamtzeit | gesamte Spielzeit in Stunden |
| Sitzungen | Anzahl der Sitzungen |
| Zuletzt gespielt | Zeitpunkt der letzten Sitzung (Attribut: Spieler) |
| Aktiv | an, solange jemand spielt – praktisch für Automationen |
| Konsole | Verbunden, Wartet auf Kopplung, Nicht gekoppelt … |
| Capture-Card | Bild, Kein Signal oder Nicht verfügbar (Attribute: Auflösung, fps) |
| Bridge | erreichbar ja/nein (Diagnose) |

Die Statistik liegt in `/data/stats.json` und ist im Backup des Add-ons enthalten.

## Gut zu wissen
- **WebRTC und unterwegs:** Über Home Assistant läuft bei WebRTC nur der Verbindungsaufbau; das
  Video selbst geht direkt vom Pi zum Browser. Das klappt im Heimnetz. Von unterwegs (z. B. über
  Nabu Casa) ist der Pi nicht direkt erreichbar – dann schaltet die Seite nach einigen Sekunden
  automatisch auf **MJPEG** um, das vollständig über Home Assistant läuft (mehr Bandbreite, etwas
  mehr Verzögerung).
- **Geringe Verzögerung:** Bei WebRTC gehen auch die Eingaben über einen Datenkanal direkt vom Browser
  zum Pi (ohne Umweg über Home Assistant). Am wenigsten Verzögerung gibt es mit Kabel-LAN für Pi und
  PC/Konsole, einem Controller per USB und `webrtc_fps: 60` in `/etc/switch2-bridge/config.yaml`.
  Schafft der Pi die 60 Bilder nicht, sinkt die Rate von selbst, ohne dass sich Bilder stauen.
- **Sicherheit:** Kommt 2 Sekunden lang kein Lebenszeichen vom Browser (Tab zu, Verbindung weg),
  lässt der Pi alle Tasten los und stellt die Sticks auf Mitte (beim Datenkanal schon nach 0,5 s).
- **nxbt:** Die Pro-Controller-Emulation nutzt [nxbt](https://github.com/Brikwerk/nxbt) 0.1.4. Es
  wird ohne seine veralteten Abhängigkeiten installiert, weil nur der Bluetooth-Teil gebraucht wird.
  Ob die Konsole den emulierten Controller annimmt, hängt von ihrer Firmware ab.
- **Testbild:** In `/etc/switch2-bridge/config.yaml` `device: "test"` setzen, um Video ohne
  Capture-Card zu prüfen.

## Fehlersuche
| Anzeige | Ursache / Lösung |
|---|---|
| **Bridge** rot | Pi nicht erreichbar: IP in der App-Konfiguration prüfen, `sudo systemctl status switch2-bridge` |
| **Konsole** gelb | Warte auf Kopplung: auf der Konsole *Griffweise/Reihenfolge ändern* öffnen |
| **Konsole** rot | nxbt läuft nicht: `journalctl -u switch2-bridge -n 50` |
| **Capture** gelb | Karte gefunden, aber kein Bild: Konsole im Standby oder HDMI-Kabel prüfen |
| **Capture** rot | Keine Capture-Card: `v4l2-ctl --list-devices`, ggf. `device` in der config.yaml anpassen |
| Ruckelt | Messwerte → Bildqualität *720p · 60 fps*; sonst `webrtc_fps`/`mjpeg_fps` in der config.yaml senken |
| Bild unscharf | Messwerte → Bildqualität *1080p · 30 fps*; **Datenrate** sollte bei Bewegung über 2,5 Mbit/s liegen (`webrtc_min_bitrate`/`webrtc_max_bitrate` in der config.yaml) |
| Bild hängt / Capture rot | Messwerte → **Capture-Card neu starten** |
| Kein Ton | Messwerte → **Ton** zeigt den Grund. Konsole auf Ton über TV/HDMI stellen; `arecord -l` am Pi muss die Capture-Card zeigen |
| Koppeln klappt nicht | Auf der Konsole *Griffweise/Reihenfolge ändern* offen lassen, einmal drücken, bis zu einer Minute warten; Protokoll: `journalctl -u switch2-bridge -n 80` |

## Entfernen
```bash
sudo bash /opt/switch2-remote-play/rpi5-bridge/uninstall.sh          # Dienst und Programm
sudo bash /opt/switch2-remote-play/rpi5-bridge/uninstall.sh --purge  # zusätzlich Einstellungen
```

## Lizenzen
Dieses Projekt ist mit keinem Konsolenhersteller verbunden. Verwendet werden nxbt (MIT), aiortc (BSD), aiohttp (Apache 2.0), websockets (BSD) und OpenCV
(Apache 2.0).
