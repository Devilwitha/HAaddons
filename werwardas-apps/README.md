# 🧹 WerWarDas – der Aufräum-Wächter

Eine Kamera fotografiert in regelmäßigen Abständen dein Zimmer und bewertet, wie aufgeräumt es ist.
Ist es zu unordentlich, zeigt dein Windows-PC einen Hinweis an. Bleibt es unordentlich, wird der PC
**gesperrt**, auf Wunsch auch **heruntergefahren**. Wieder frei wird er erst, wenn aufgeräumt ist.
Alles ist in **Home Assistant** eingebunden.

```
┌───────────────────────┐   MQTT   ┌──────────────────────┐   MQTT   ┌────────────────────────┐
│ Raspberry Pi + Kamera │ ───────► │ Home Assistant       │ ◄──────► │ Windows-App            │
│ Foto + Bewertung      │ ◄─────── │ Mosquitto-Broker,    │          │ Hinweis / Sperre /     │
│ (pi/)                 │ "prüfen" │ Dashboard, Automation│          │ Herunterfahren         │
└───────────────────────┘          └──────────────────────┘          └────────────────────────┘
```

## Inhalt

| Ordner | Was |
|---|---|
| [`pi/`](pi) | Kamera-Software für den Raspberry Pi (Python) inklusive Installationsskript |
| [`windows/`](windows) | Windows-App (C# / .NET 8). Die `.exe` baut GitHub Actions automatisch |
| [`android/`](android) | Android-Apps für das Kinderhandy und die Eltern (Kotlin), auch unterwegs über Nabu Casa – siehe [android/README.md](android/README.md) |
| [`homeassistant/`](homeassistant) | Add-ons (Kameradienst, Ollama), Blueprint und Beispiel-Karte fürs Dashboard |
| [`esp32cam/`](esp32cam) | Firmware für eine ESP32-CAM als günstige WLAN-Kamera (PlatformIO) |

---

## 1. Was du brauchst

- **Home Assistant** (z. B. HA OS auf einem Pi 4/5) mit dem Add-on **Mosquitto broker**
- **Ein zweiter Raspberry Pi für die Kamera** (Pi 3, 4 oder 5) mit **Raspberry Pi OS Lite (64-bit)**.
  Unter HA OS lassen sich keine eigenen Programme installieren. Die Kamera braucht deshalb ihren eigenen Pi.
  Ein Pi 3 reicht völlig aus.
- **Kamera**: am besten das *Raspberry Pi Camera Module 3 **Wide*** (Weitwinkel, damit das ganze Zimmer drauf ist).
  Eine USB-Webcam oder eine ESP32-CAM (über ihre `/capture`-URL) gehen auch.
- Einen **Windows-PC** (Windows 10/11, 64-bit)

## 2. Home Assistant vorbereiten (einmalig)

1. *Einstellungen → Add-ons → Add-on-Store →* **Mosquitto broker** installieren und starten.
2. *Einstellungen → Personen → Benutzer →* einen Benutzer `werwardas` mit Passwort anlegen.
   Mit diesen Zugangsdaten melden sich Pi und PC am Broker an.
3. *Einstellungen → Geräte & Dienste:* Die **MQTT**-Integration wird meist automatisch vorgeschlagen. Einfach bestätigen.

## 3. Raspberry Pi mit Kamera installieren

Raspberry Pi OS Lite mit dem *Raspberry Pi Imager* auf die SD-Karte schreiben (dort WLAN und SSH gleich mit einrichten),
die Kamera anschließen, per SSH verbinden und **einen** Befehl ausführen:

```bash
curl -fsSL https://raw.githubusercontent.com/Devilwitha/werwardas/main/pi/install.sh | sudo bash
```

Das Skript installiert alles Nötige und fragt dabei nach den MQTT-Zugangsdaten, der Kamera und der Bewertungsart.
Danach macht es ein Testfoto und startet den Dienst. **Update:** denselben Befehl einfach erneut ausführen.

Kurz danach erscheint in Home Assistant automatisch das Gerät **„WerWarDas Kamera“** mit:

| Entität | Bedeutung |
|---|---|
| Zimmer (Kamera) | letztes Foto |
| Ordnung | Score 0–100 % (100 = perfekt aufgeräumt) |
| Unordentlich | `an`, wenn der Score unter dem Grenzwert liegt |
| Begründung | z. B. „Kleidung auf dem Boden vor dem Bett“ |
| Streak / Rekord | Tage in Folge aufgeräumt, siehe Abschnitt 8 |
| Jetzt prüfen | sofort ein Foto machen und bewerten |
| Als aufgeräumt speichern | aktuelles Foto als Referenz für den Offline-Vergleich |
| Überwachung | Schalter zum Pausieren (z. B. im Urlaub) |
| Grenzwert / Intervall / Prüfzeit / Bewertung | Einstellungen, direkt in HA änderbar (Prüfzeit z. B. `08:00-22:00`, leer = immer) |

Nützliche Befehle auf dem Pi:

```bash
journalctl -u werwardas -f              # Log live ansehen
sudo nano /etc/werwardas/config.yaml    # Konfiguration (alle Optionen: pi/config.example.yaml)
sudo systemctl restart werwardas        # nach Änderungen neu starten
werwardas --once                        # einmal Foto + Bewertung ausgeben (Dienst vorher stoppen)
curl -fsSL .../install.sh | sudo bash -s -- --reconfigure   # Einrichtung neu
curl -fsSL .../install.sh | sudo bash -s -- --uninstall     # entfernen
```

## 3b. Ohne Pi: ESP32-CAM + Kameradienst auf dem Windows-PC

Statt Pi + Kamera geht auch eine **ESP32-CAM (AI-Thinker)** als WLAN-Kamera. Die Bewertung läuft dann auf dem Windows-PC.

**Firmware flashen** (ESP32-CAM-MB per USB, [PlatformIO](https://platformio.org) nötig: `pip install platformio`):

```powershell
cd esp32cam
copy include\secrets.example.h include\secrets.h   # WLAN-Name/Passwort eintragen (nur 2,4 GHz)
pio run -t upload --upload-port COM41               # COM-Port im Geräte-Manager nachsehen
pio run -e ota -t upload --upload-port werwardas-cam.local   # spätere Updates per WLAN
```

Danach liefert `http://werwardas-cam.local/capture` ein Foto (Statusseite: `http://werwardas-cam.local/`).

**Kameradienst installieren** (Python 3.10+ nötig, keine Admin-Rechte):

```powershell
.\pi\install-windows.ps1 -MqttHost <IP-von-HA> -MqttUser werwardas -MqttPassword <passwort> `
    -CameraUrl http://werwardas-cam.local/capture -Analyzer ollama
```

Das Skript installiert nach `%LOCALAPPDATA%\WerWarDas\kamera`, macht ein Testfoto und richtet die geplante Aufgabe
„WerWarDas Kamera“ ein (startet bei der Anmeldung). Danach erscheint in HA dasselbe Gerät **„WerWarDas Kamera“** wie beim Pi.
Liegt das Bild schief: `camera.rotation` in der `config.yaml` setzen. **Update:** Skript ohne Parameter erneut ausführen,
**Entfernen:** `-Uninstall`.

> Läuft der Dienst auf dem überwachten PC selbst, kann man ihn beenden. Die Windows-App gibt den PC dann nach
> 2 Minuten frei. Wer das nicht will, betreibt denselben Dienst später auf einem Pi (Abschnitt 3) und trägt dort
> `camera.url` der ESP32-CAM und `ollama.url: http://<IP-des-PCs>:11434` ein.

## 3c. Alles auf dem Home-Assistant-Rechner (Add-ons, ohne Pi und ohne laufenden PC)

Läuft Home Assistant OS auf einem Pi 4/5 mit 8 GB (oder einem Mini-PC), kann der Kameradienst samt KI direkt dort laufen.
Zusammen mit der ESP32-CAM (Abschnitt 3b) braucht es dann keinen eigenen Kamera-Pi und keinen eingeschalteten PC.

1. *Einstellungen → Add-ons → Add-on-Store → ⋮ → Repositories* → `https://github.com/Devilwitha/werwardas` hinzufügen.
2. **Ollama (WerWarDas)** installieren und starten. Beim ersten Start lädt es `qwen2.5vl:3b` (ca. 3 GB, dauert eine Weile).
3. **WerWarDas Kamera** installieren, im Reiter *Konfiguration* `camera_url` (z. B. `http://192.168.1.165/capture`) prüfen
   und starten. Die MQTT-Zugangsdaten holt sich das Add-on selbst vom Mosquitto-Add-on.
4. Den Kameradienst auf dem PC entfernen, falls installiert: `.\pi\install-windows.ps1 -Uninstall`.

**PC als Turbo:** In `ollama_servers` stehen mehrere Ollama-Server, die der Reihe nach versucht werden. Standard:
erst der PC (`qwen2.5vl:7b`, wenige Sekunden), ist der aus, das Ollama-Add-on auf dem Pi (`qwen2.5vl:3b`; gemessen
auf einem Pi 4 mit 8 GB: rund 12 Minuten pro Foto, ein Pi 5 ist etwa zwei- bis dreimal so schnell). Das reicht, denn
der Pi muss nur bewerten, wenn der PC aus ist, und dann gibt es nichts zu sperren. Das kleine Modell urteilt allerdings
weniger zuverlässig als das große. Soll nur mit dem PC bewertet werden, in HA beim Gerät *WerWarDas Kamera* den Schalter
**„Pi-KI als Ersatz“** ausschalten (das Ollama-Add-on kann dann auch ganz gestoppt werden). Damit der Pi den PC erreicht, muss Ollama dort im LAN lauschen:

```powershell
setx OLLAMA_HOST 0.0.0.0     # danach Ollama beenden und neu starten
# einmalig als Administrator: Firewall für das Heimnetz öffnen
New-NetFirewallRule -DisplayName "Ollama (WerWarDas)" -Direction Inbound -Protocol TCP -LocalPort 11434 -Profile Private -Action Allow
```

Der ESP32-CAM und dem PC am besten im Router eine feste IP geben, die Add-on-Konfiguration nutzt IP-Adressen.
**Update:** Sobald im Store eine neue Version erscheint, auf *Aktualisieren* klicken.

## 4. Bewertung: drei Möglichkeiten

| Bewertung | Wie | Vorteile | Nachteile |
|---|---|---|---|
| `reference` | Vergleich mit Fotos vom aufgeräumten Zimmer | offline, kostenlos, läuft auf jedem Pi | erkennt nur „anders als sonst“, reagiert auf verschobene Möbel |
| `ollama` | lokale KI (z. B. `qwen2.5vl:7b`, `gemma3`, `llava`) | versteht das Bild, gibt eine Begründung, alles bleibt zu Hause | braucht einen Rechner mit genug Leistung |
| `claude` | Claude API (Cloud) | beste Erkennung und Begründung | API-Key nötig, kostet ca. 1–2 Cent pro Foto (bei 30-Minuten-Intervall rund 0,30–0,50 € pro Tag), Foto geht in die Cloud |

Die Bewertung lässt sich jederzeit in Home Assistant über die Auswahl **„Bewertung“** umschalten.

**Referenzfoto (`reference`):** Zimmer einmal ordentlich aufräumen und in HA auf **„Als aufgeräumt speichern“** drücken.
Am besten mehrmals zu verschiedenen Tageszeiten (Tageslicht und Lampe), das System merkt sich die letzten 5 Fotos.
Bereiche, die sich ständig ändern (Fenster, Monitor), lassen sich mit `reference.ignore_areas` ausblenden.

**Lokale KI (`ollama`):** [Ollama](https://ollama.com) z. B. **auf deinem Windows-PC** installieren. Der ist ja
genau dann an, wenn geprüft werden muss. Dann:

```powershell
ollama pull qwen2.5vl:7b
setx OLLAMA_HOST 0.0.0.0     # damit der Pi zugreifen darf, danach Ollama neu starten
```

In der Pi-Konfiguration `ollama.url: http://<IP-des-PCs>:11434` eintragen. Mit einer Grafikkarte dauert eine Bewertung
ein paar Sekunden. Direkt auf einem Pi 5 (8 GB) läuft ein kleines Modell wie `gemma3:4b` auch, braucht aber
1–3 Minuten pro Foto. Ist Ollama nicht erreichbar, bleibt der PC frei.

**Claude (`claude`):** API-Key auf [console.anthropic.com](https://console.anthropic.com) erstellen und bei der
Installation eingeben (oder in `config.yaml` unter `claude.api_key` eintragen).

Mit `prompt_hint` in der `config.yaml` kannst du der KI Hinweise geben, z. B.
`"Der Wäschekorb in der Ecke und die Gitarre am Bett sind erlaubt."`

## 5. Windows-App

1. **`WerWarDas.exe` herunterladen:** unter [Releases](https://github.com/Devilwitha/werwardas/releases)
   oder unter *Actions → „Windows-App bauen“ → letzter Lauf → Artifacts*.
2. Die exe an einen festen Ort legen (z. B. `C:\Programme\WerWarDas\`) und starten. Es ist keine Installation nötig.
   Windows SmartScreen warnt eventuell, weil die exe nicht signiert ist: *Weitere Informationen → Trotzdem ausführen*.
3. Beim ersten Start öffnen sich die Einstellungen: Adresse von Home Assistant, Benutzer `werwardas` und Passwort.

In den Einstellungen der App lassen sich unter *Kamera-Prüfung* auch das **Prüfintervall**, der **Grenzwert** (ab wie viel % Ordnung das Zimmer als aufgeräumt gilt) und die **Prüfzeit** des Kameradienstes ändern. Das sind dieselben Werte wie in HA.

Die App läuft danach im Infobereich (🟢 alles ok, 🟡 Hinweis, 🔴 gesperrt, ⚪ keine Verbindung) und startet mit Windows.

**Ablauf im Modus „Automatisch“:**

1. Kamera meldet *unordentlich* → **Hinweisfenster** mit Foto, Begründung und Countdown.
2. Nach der **Schonfrist** (Standard 15 Minuten) ist es immer noch unordentlich → **Sperrbildschirm** über allen Monitoren.
3. Optional: nach *X* Minuten gesperrt → **Herunterfahren** (mit 60 Sekunden Vorwarnung).
4. Aufräumen → **„Ich habe aufgeräumt – jetzt prüfen“** klicken → der Pi macht sofort ein Foto → ist es ordentlich, ist der PC frei. 🎉

**Sicherheitsnetz:** Ist der Broker oder die Kamera länger als 2 Minuten nicht erreichbar, oder schlägt die Bewertung fehl,
wird der PC **freigegeben**. Mit der optionalen **Notfall-PIN** lässt sich die Sperre aufheben – wie lange, steht in den Einstellungen unter „Notfall-Entsperrung“ (Standard 60 Minuten).
Die PIN schützt auch die Einstellungen und das Beenden der App.

> Hinweis: Das ist ein Motivations-Werkzeug, kein Hochsicherheits-Schloss. Windows-Taste, Alt+Tab, Alt+F4 usw. sind
> während der Sperre blockiert. Strg+Alt+Entf und der Task-Manager lassen sich von einer normalen App aber nicht verhindern.

**Modus „Nur Home Assistant“:** Die App macht nur, was Home Assistant sagt. Das Gerät **„WerWarDas PC …“** bringt
dafür den Schalter *Sperre* und die Buttons *Hinweis anzeigen* und *Herunterfahren* mit. Den fertigen Ablauf gibt es als Blueprint:

[![Blueprint importieren](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2FDevilwitha%2Fwerwardas%2Fblob%2Fmain%2Fhomeassistant%2Fblueprints%2Fautomation%2Fwerwardas%2Faufraeum_waechter.yaml)

Damit lassen sich auch eigene Regeln bauen, z. B. „nur an Schultagen“ oder „Handy-Benachrichtigung“.

## 6. MQTT-Topics (für eigene Automationen)

| Topic | Richtung | Inhalt |
|---|---|---|
| `werwardas/cam/state` | Pi → | `{"score": 43, "tidy": false, "threshold": 60, "reason": "…", "analyzer": "ollama", "trigger": "interval", "error": false, "paused": false, "streak": 4, "record": 12, "timestamp": "…"}` |
| `werwardas/cam/image` | Pi → | letztes Foto (JPEG) |
| `werwardas/cam/availability` | Pi → | `online` / `offline` |
| `werwardas/cam/cmd/check` | → Pi | sofort prüfen |
| `werwardas/cam/cmd/reference` | → Pi | Foto als „aufgeräumt“ speichern |
| `werwardas/cam/set/{threshold,interval,analyzer,active}` | → Pi | Einstellungen ändern |
| `werwardas/pc/<name>/cmd` | → PC | `lock`, `unlock`, `warn`, `shutdown`, `cancel_shutdown` |
| `werwardas/pc/<name>/state` | PC → | `frei`, `hinweis`, `gesperrt`, `notfall` |
| `werwardas/pc/<name>/event` | PC → | `locked`, `unlocked`, `emergency_unlock`, `shutdown` |

## 7. Datenschutz

- Fotos bleiben auf dem Pi (`/var/lib/werwardas/photos`) es bleiben nur das aktuelle und die 3 vorherigen (`storage.max_photos: 4`).
  Nur bei der Bewertung `claude` wird das Foto an die Claude API geschickt.
- Die Kamera ist nur für die Sekunden der Aufnahme an, es gibt keinen Dauer-Stream.
- Tipp: Die Kamera so ausrichten, dass vor allem Boden, Schreibtisch und Bett zu sehen sind.

## 8. Verlauf & Streak

Beides läuft automatisch mit, ohne weitere Einrichtung.

**Verlauf:** Der Sensor *Ordnung* hat dank `state_class: measurement` automatisch eine Home-Assistant-Historie.
Die aktualisierte [Beispiel-Karte](homeassistant/dashboard-karte.yaml) enthält einen `history-graph`
(letzte 48 Stunden) und einen `statistics-graph` (Tagesmittel über 30 Tage).

**Streak:** Der Kameradienst zählt selbst die Tage in Folge, an denen das Zimmer nie unordentlich war, und merkt
sich den Rekord (Datei `streak.json` im Datenverzeichnis, überlebt also auch einen Neustart). Beim Gerät
**„WerWarDas Kamera“** erscheinen dafür automatisch zwei neue Sensoren: **Streak** und **Rekord**. Ein Tag ohne
jede Prüfung (Dienst aus, Überwachung pausiert) zählt weder für noch gegen den Streak; ein Tag, an dem das
Zimmer mindestens einmal als unordentlich erkannt wurde, setzt ihn auf 0 zurück.

## 9. Entwicklung

```bash
# Pi-Software lokal testen
cd pi && pip install -r requirements.txt numpy pillow pytest && python -m pytest

# Windows-App bauen (unter Windows; unter Linux geht es mit EnableWindowsTargeting nur ohne Starten)
dotnet build windows/WerWarDas/WerWarDas.csproj
```

Jeder Push auf `main`, der etwas unter `windows/` ändert, baut per GitHub Actions automatisch ein neues
Release `v1.0.<Laufnummer>` mit angehängter `WerWarDas.exe`.

**Automatische Updates:** Die App sucht alle *n* Stunden nach dem neuesten Release (Einstellungen → „Nach Updates
suchen“, Standard 1 Stunde, 0 = nie; von Hand über das Tray-Menü). Eine neuere Exe wird heruntergeladen, auf
Größe, SHA-256-Prüfsumme und Versionsnummer geprüft und eingespielt, sobald der PC **frei** ist (nicht gesperrt,
kein Hinweis, keine Notfall-Entsperrung). Danach startet die App von selbst neu.
