# CLAUDE.md – HAaddons (BolliSoft)

Home-Assistant-Add-on-Repository von BolliSoft (`repository.yaml`). Jedes Add-on liegt in einem
eigenen Ordner mit `config.yaml`; HA liest den Stand von `main` auf GitHub. Hilfsprogramme, die nicht
im Add-on-Container laufen, liegen ebenfalls im Hauptordner (`pc_agent/`, `retropie-mqtt/`, `rpi5-bridge/`,
`werwardas-apps/`, `esp32cam/`).

## Allgemein
- Jedes Dockerfile mit Python-/Shell-CMD braucht `ENV S6_KEEP_ENV=1` (oder `#!/usr/bin/with-contenv`):
  s6-overlay im HA-Basis-Image leert sonst die Umgebung – `SUPERVISOR_TOKEN` und eigene ENV-Werte fehlen.
- Texte für Nutzer auf Deutsch. Autor in Beschreibungen/DOCS: „von BolliSoft“.
- Bei jeder Änderung an einem Add-on `version` in dessen `config.yaml` erhöhen und `CHANGELOG.md`
  ergänzen, sonst bietet HA kein Update an.
- Python 4 Leerzeichen; YAML/JSON/JS/HTML/CSS 2 Leerzeichen. Keine Platzhalter/TODOs.
- Oberflächen laufen hinter Ingress: nur relative URLs, keine externen CDNs, Zugriff nur von 172.30.32.2.

## Claude Code (`claude_code/`)
Chat-Oberfläche (`rootfs/app/server.py` + `static/`), startet `claude` im stream-json-Modus, Erlaubnis-Abfragen
über `--permission-prompt-tool stdio`, Verlauf aus den JSONL-Dateien unter `~/.claude/projects/-config/`.
- Bilder: Upload per HTTP `/api/upload` (Ingress-WebSockets nur ~4 MB pro Nachricht), Pillow verkleinert auf
  1568 px nach `/data/uploads` (2 Tage), Original nach `/share/claude/uploads` (30 Tage). Gesendet als
  `image`-Blöcke plus Text mit `[Angehängte Bilder (Originale): …]` (wird in der Anzeige entfernt). Verlauf liefert
  Bilder über `/api/sessions/<id>/image/<zeile>/<block>` aus der JSONL.
- Lokal testen: `CC_CLAUDE` auf ein Ersatz-`claude` (antwortet auf `auth status --json`, stream-json, schreibt JSONL
  nach `CLAUDE_CONFIG_DIR`), dazu `CC_CWD`, `CC_UPLOADS`, `CC_ORIGINALS`, `CC_OPTIONS`, `CC_UI_SETTINGS`, `CC_PORT`.

## Sensoren (`ha_sensors.py`)
- Identische Datei in `gba_emulator/`, `n64_emulator/` und `switch2_remote_play/app/`: MQTT-Discovery
  (Gerät „BolliSoft“, Topics `bollisoft/<node>/…`) oder REST-API über den Supervisor, Option `use_mqtt`.
  Braucht `homeassistant_api: true` und `services: [mqtt:want]` in der `config.yaml`.
- Statistik in `/data/stats.json`; Sitzungen: Emulatoren über `/api/session/start|ping|end` aus `play.js`
  (Ablauf nach 180 s ohne Lebenszeichen), Game Remote über die offene WebSocket-Verbindung.
- Spieler kommt aus den Ingress-Kopfzeilen `X-Remote-User-Display-Name`/`X-Remote-User-Name`.
- Lokal testen: `SUPERVISOR_TOKEN=x BOLLISOFT_SUPERVISOR_URL=http://127.0.0.1:<port>` gegen einen
  kleinen Ersatz-Supervisor (`/services/mqtt`, `/core/api/states/<id>`).

## GBA- und N64-Emulator (`gba_emulator/`, `n64_emulator/`)
- `server.py` und alles in `web/` sind in beiden Add-ons **identisch** – nur `web/static/system.js`
  (Name, Kern, Endungen, Touch-Layout, Controller-Vorlagen) und das `Dockerfile` (`EMU_SYSTEM`,
  Kerne) unterscheiden sich. Änderungen immer in beide Ordner übernehmen und mit `diff` prüfen.
- HA-Ingress nimmt höchstens **16 MB pro Anfrage** an: Uploads (ROMs, Speicherstände) laufen deshalb über
  `/api/blob/start|<id>|<id>/finish` in Teilen zu 8 MB (`web/static/upload.js`).
- Große Antworten über Ingress bleiben auf manchen Geräten hängen: `play.js` lädt das Spiel über
  `/api/rom/<name>/info` und `/part` in 4-MB-Teilen, cacht es (Cache API) und startet EmulatorJS erst
  danach mit einer `blob:`-Adresse (`loader.js` wird dynamisch nachgeladen, `EJS_gameName` = Dateiname).
- Die HA-App hält statische Dateien sonst stundenlang im Cache: `page()` hängt `?v=<ASSET_TAG>` an alle
  `static/`- und `ejs/`-Skripte/Stylesheets im HTML, `/static/` und `/ejs/` gehen mit `Cache-Control: no-cache` raus.
  Neue Skripte nur über `<script src="static/….js">` im HTML einbinden (sonst greift das nicht).
- EmulatorJS wird im Dockerfile von npm geladen (SHA-256 gepinnt), zu `emulator.min.js` zusammengefügt
  und an drei Stellen gepatcht (Steuerkreuz-Mitte, Analogstick bei 0°, Controller-Abfrage 4 statt 10 ms);
  die `grep`-Prüfungen müssen bleiben.
- Verzögerung: `play.js` fordert für WebGL `desynchronized: true` an (Low-Latency-Canvas). Run-Ahead,
  Preemptive Frames und `audio_latency` in `retroarch.cfg` wirken im Browser-Build von EmulatorJS 4.2.3 nicht
  (gemessen, 11/2026) – nicht erneut versuchen.

## Game Remote (`switch2_remote_play/` + `rpi5-bridge/`)
Sichtbarer Name **Game Remote**; technische Namen (Ordner `switch2_remote_play`, Slug, Dienst
`switch2-bridge`, Pfade `/opt/switch2-*`, `/etc/switch2-bridge`) bleiben bewusst so.
In sichtbaren Namen, Beschreibungen und Texten keine Marken-/Konsolennamen – nur „Spielkonsole“/„Konsole“.

- `switch2_remote_play/` – Add-on: Ingress-Server (aiohttp) + Oberfläche (Video, Touch-Gamepad,
  Gamepad API). Leitet `/ws` und `/video/*` an den Pi weiter, damit der Browser nur mit HA spricht.
- `rpi5-bridge/` – Raspberry Pi 5: Pro-Controller-Emulation (nxbt/BlueZ) und Video von der
  USB-Capture-Card (OpenCV, MJPEG + WebRTC über aiortc). `install.sh` klont dieses Repository flach nach
  `/opt/switch2-remote-play` und richtet den systemd-Dienst `switch2-bridge` ein.

### Protokoll Browser ↔ Bridge (WebSocket, JSON)
- Browser → Bridge: `{"type":"input","buttons":[...],"ls":[x,y],"rs":[x,y]}` (x/y −100…100, y oben
  positiv), `{"type":"heartbeat","t":ms}`, `{"type":"system","action":"pair"|"capture_restart"}`,
  `{"type":"system","action":"video_mode","mode":"720p60"|"1080p30"}`
- Bridge → Browser: `{"type":"status","switch":{state,detail},"capture":{...},"stream":{webrtc:{fps,age,convert,encode}|null,jpeg:{fps,age,encode}|null,jpeg_clients,mjpeg_fps,webrtc_clients,audio:{state,detail,device,listeners}},"clients":n}`,
  `{"type":"pong","t":ms}`, `{"type":"info"|"error","message":...}`
- Der Ingress-Server schickt zusätzlich `{"type":"proxy","status":"connected"|"unreachable"|"bridge_closed"}`.
- Schnellster Weg für Eingaben: WebRTC-Datenkanal `input` (ungeordnet, `maxRetransmits: 0`) direkt zum Pi,
  Nachrichten wie `input` plus `seq` (ältere verworfen). Der Browser wiederholt den Zustand alle 50 ms; nach
  0,5 s ohne Paket gilt der Kanal als neutral. Ohne offenen Kanal gehen Eingaben über den WebSocket; beim
  Wechsel schickt der Browser auf dem bisherigen Weg einmal „alles los“.
  Über den Kanal auch `heartbeat` → `pong` mit `"via":"dc"` (Ping direkt in der Messanzeige).
- Tastennamen: A B X Y L R ZL ZR MINUS PLUS HOME CAPTURE DPAD_UP/DOWN/LEFT/RIGHT LS RS
  (müssen in `virtual-gamepad.js`, `app.js` und `controller_emulator.py` übereinstimmen).

### Wichtige Entscheidungen
- **nxbt 0.1.4** (2021, ungepflegt) pinnt Pakete, die unter Python ≥ 3.11 nicht bauen. Für die
  Emulation braucht es nur `dbus-python` → `pip install --no-deps nxbt==0.1.4`, `python3-dbus` per apt.
  nxbt nicht in `requirements.txt` aufnehmen.
- **venv mit `--system-site-packages`**: `python3-opencv`, `python3-numpy`, `python3-dbus` kommen aus apt.
  Keine Abhängigkeit hinzufügen, die NumPy 2 per pip nachzieht (bricht das apt-OpenCV).
- **WebRTC** nur im LAN (Medien gehen direkt Pi → Browser). Das Frontend fällt nach 8 s automatisch auf
  Einzelbilder zurück (läuft komplett über Ingress). aiortc ohne STUN-Server starten
  (`RTCConfiguration(iceServers=[])`), sonst wartet der Aufbau Sekunden auf Google-STUN.
- **Einzelbilder** (Übertragung „jpeg“, wählbar unter Messwerte, `localStorage` `sw2-transport`, Vorgabe
  `stream_mode`): WebSocket `/frames` der Bridge, vom Add-on unter `video/frames` durchgereicht. Nachricht = 4 Byte
  Länge + JSON-Kopf `{id,age,enc,pt}` + JPEG; der Browser dekodiert per `createImageBitmap` und schickt
  `{"type":"ack"}`, höchstens `JPEG_WINDOW` = 2 Bilder unbestätigt. MJPEG (multipart) über Ingress kam nicht an
  (HA komprimiert/puffert die Antwort) – `stream_mode: mjpeg` gilt als `jpeg`. Liefert die Karte MJPG, liest die
  Bridge mit `CAP_PROP_CONVERT_RGB = 0` die Roh-JPEGs (`jpeg_passthrough`), dekodiert nur für WebRTC-Zuschauer;
  sind die Rohdaten kein JPEG, öffnet sie die Karte ohne Durchreichen neu. `CAP_PROP_BUFFERSIZE` nicht auf 1
  (`capture_buffers`, Vorgabe 3): mit einem Puffer gingen bei 60 fps Bilder verloren (gemessen ~44 fps). Die Cam Link 4K liefert kein MJPG, nur YUYV/NV12 und nur in der Größe
  des HDMI-Signals (1080p60): die Bridge liest NV12 roh (`nv12`, sonst Rückfall wie bei JPEG), baut daraus das I420
  für aiortc (`nv12_to_i420`) und verkleinert auf die Größe der Bildqualität (`_scale_size`). Umrechnungen pro Bild
  einmal für alle Zuschauer (`_cached`: bgr, i420, jpeg).
- **Watchdog** 2,0 s ohne Heartbeat/Eingabe → alle Tasten los, Sticks (0, 0).
- **Bildqualität** (`MODES` in `video_streamer.py`): 720p60 oder 1080p30 (Software-VP8 auf dem Pi 5 schafft 1080p
  nur mit ~30 fps), gemerkt in `/var/lib/switch2-bridge/video_mode.json`. aiortc startet sonst mit 500 kbit/s und
  folgt der REMB-Schätzung des Browsers → `MIN_BITRATE`/`MAX_BITRATE` von `aiortc.codecs.vpx`/`h264` werden gesetzt
  (`webrtc_min_bitrate` 3 Mbit/s). aiortc legt den Encoder bei > 10 % Bitraten-Änderung neu an (Schlüsselbild,
  Hänger) → `webrtc_fixed_bitrate` (MIN = MAX); `vpx.number_of_threads` wird auf 4 Kerne gepatcht (`use_all_cores`).
  `system_tune.py`: CPU-Regler performance, WLAN-Stromsparen aus, Temperatur/Last/`vcgencmd get_throttled` im Status. **Gerät** `device: "auto"`: `/dev/v4l/by-id/usb-*-video-index0`, sonst erstes
  USB-Videogerät mit `index` 0 in `/sys/class/video4linux` (der Pi 5 hat eigene Videogeräte, die Nummer der Karte
  wechselt). Ein eingetragenes `/dev/videoN` ohne USB-Karte dahinter wird ebenfalls durch die Suche ersetzt.
  **Capture-Card neu starten**: Karte schließen, `USBDEVFS_RESET` auf
  `/dev/bus/usb/<bus>/<dev>` (aus `/sys/class/video4linux/videoN/device`), neu öffnen – der Dienst läuft als root.
- **Ton** nur über WebRTC: Bridge liest die Capture-Card per `arecord` (alsa-utils, 48 kHz Stereo, 20-ms-Pakete,
  höchstens 60 ms Puffer), nur solange jemand zuhört. Die Tonspur bekommt eine **eigene Stream-ID**
  (`sender._stream_id`), sonst gleicht der Browser Bild und Ton ab und verzögert das Bild. `audio.device`:
  `auto` (erste USB-Soundkarte mit Eingang), `off`, ALSA-Name; beim Testbild ein Piepton bei gedrückter Taste.
- **nxbt-Abstürze:** Ein abgestürzter Controller-Prozess hält den Adapter belegt („No adapters available“).
  Die Bridge beendet dann die nxbt-Prozesse selbst (`controllers.terminate()`, `resource_manager.shutdown()`,
  ohne `_on_exit`, das BlueZ neu starten würde) und legt `nxbt.Nxbt()` mit mehreren Versuchen neu an.
  Koppeln während eines laufenden Versuchs (`connecting`, < 90 s) wird abgelehnt.
  Der nxbt-Start läuft im Hintergrund (`controller_main`): WebSocket und Video sind sofort da, Koppeln wartet.
- **Konfiguration der Bridge** liegt nach der Installation in `/etc/switch2-bridge/config.yaml`
  (bei Updates nicht überschrieben); `rpi5-bridge/config.yaml` ist nur die Vorlage.
- Eingaben mehrerer Clients: Tasten ODER-verknüpft, Sticks nach größtem Ausschlag.
- Controller-Belegung im Browser: Vorlagen „wie Konsolen-Anordnung“ / „nach Beschriftung“, eigene
  Belegung tauscht bei Doppelbelegung; Erkennung in der Reihenfolge Xbox → Pro Controller (`057e`) →
  PlayStation („Xbox Wireless Controller“ enthält „wireless controller“). Gilt auch für `controller.js`
  der Emulatoren (dort `proPreset` in `system.js`).

### Lokal testen (ohne Pi und ohne Konsole)
```bash
# Bridge mit Testbild und ohne Bluetooth (config mit video.device: "test")
python rpi5-bridge/main.py --config /pfad/test.yaml --dry-run
# Add-on-Server gegen die lokale Bridge
SWITCH2_OPTIONS=/pfad/options.json SWITCH2_NO_GUARD=1 python switch2_remote_play/app/server.py
```
`SWITCH2_NO_GUARD=1` schaltet die Ingress-Sperre ab, `SWITCH2_PORT` ändert den Port. Im Testmodus
protokolliert die Bridge jede empfangene Eingabe („Testmodus - Eingabe: …“).
Benötigt lokal: `aiohttp aiortc websockets PyYAML opencv-python-headless numpy`.

## Vorrat (`vorrat/`)
Vorräte mit Kameras + Ollama zählen. Code in `vorrat/app/`: `server.py` (Optionen, Warteschlange `Counter`,
API, Ingress), `vision.py` (Fotos: `http…` oder `camera.…` über `/core/api/camera_proxy`; Ollama mit Server-Ersatz
wie WerWarDas, JSON-Schema `{"items":[{name,count}]}`), `inventory.py` (Stand in `/data/inventory.json`),
`ha.py` (eigenes MQTT-Discovery mit dynamischen Entitäten, REST-Ersatz, To-do-Sync).
- Prinzip **Bestand zählen statt Bewegungen verfolgen**: jeder Durchlauf ersetzt den Stand des Ortes, Verlauf
  entsteht aus den Unterschieden. Kameras eines Ortes werden addiert. Schlägt eine Kamera fehl, bleibt der Stand.
- Namen bleiben stabil über bekannte Namen im Prompt, Vergleich ohne Groß-/Kleinschreibung und Aliase beim
  Umbenennen/Zusammenführen. Nur von der Kamera erkannte Vorräte verschwinden nach 3 Tagen auf 0.
- MQTT: Gerät „Vorrat“, Topics `bollisoft/vorrat/…`, Knöpfe `bollisoft/vorrat/<ort>/scan` und `scan_all`;
  gelöschte Entitäten bekommen eine leere Discovery-Nachricht (Liste in `/data/mqtt_entities.json`).
- Einkaufsliste: nur selbst angelegte Einträge werden wieder entfernt (`/data/todo.json`).
- AI HAT+ (Hailo) geht unter HA OS nicht im Add-on (kein Treiber), daher nur Ollama.
- Lokal testen: `SUPERVISOR_TOKEN=x VORRAT_SUPERVISOR_URL=… VORRAT_OPTIONS=… VORRAT_DATA=… VORRAT_NO_GUARD=1
  VORRAT_PORT=… python vorrat/app/server.py` gegen einen Ersatz für Kamera, Ollama (`/api/tags`, `/api/chat`)
  und Supervisor.

## BolliSoft Themes (`bollisoft_themes/`)
Designs, Theme-Editor und Dashboard-Karten. Bis 11/2026 lag nur PicoDesk im privaten Repository `Devilwitha/ThemeHA`
(übernommen ist nur `themes/picodesk.yaml`, unverändert in `app/themes/`; `deploy.ps1` enthält einen Token und
bleibt draußen).
- `themegen.py` macht aus wenigen Parametern (Farben, Rundung, Schatten …) alle HA-Variablen je Modus; Designs
  in `presets.py` (mit `file` wird die Datei unverändert installiert, die Parameter dienen nur als Vorlage).
  Eigene Themes in `/data/themes.json`, Typ `editor` (Parameter) oder `yaml` (importiert).
- Schreibt nur nach `/config/themes/bollisoft/<id>.yaml` (erste Zeile `# BolliSoft Themes`, nur solche Dateien
  werden gelöscht), danach `frontend.reload_themes`. Ordner kommt aus `frontend: themes: !include_dir_merge_named …`
  der configuration.yaml; fehlt die Zeile, trägt `hass.add_themes_line` sie mit Sicherung ein.
- Ein Theme nur mit `modes: dark` erzwingt im Frontend den Dunkelmodus, ohne `dark` gilt Hell (themes-mixin.ts).
- Geladene Themes und Standard über WebSocket `frontend/get_themes` (Supervisor-Proxy `ws://supervisor/core/websocket`).
- Karten `app/cards/bollisoft-cards.js` (glow, room, person + Editoren mit `ha-form`, keine Abhängigkeiten): beim Start
  nach `/config/www/bollisoft-themes/` kopiert und per `lovelace/resources/create|update` mit `?v=<sha1>` eingetragen.
  Die Vorschau im Add-on lädt dieselbe Datei mit Ersatz-Elementen (`static/stubs.js`) und Beispieldaten (`static/demo.js`).
- Lokal testen: `SUPERVISOR_TOKEN=x THEMES_SUPERVISOR_URL=… THEMES_CONFIG_DIR=… THEMES_DATA=… THEMES_NO_GUARD=1
  THEMES_PORT=… python bollisoft_themes/app/server.py` gegen einen Ersatz-Supervisor (Dienste, `check_config`,
  WebSocket mit `frontend/get_themes` und `lovelace/resources*`).

## BolliSoft Browser (`bollisoft_browser/`)
Chromium (headless, Alpine-Paket) im Add-on, bedient über Ingress. `app/browser.py` (Prozess, DevTools-Protokoll über
WebSocket auf 127.0.0.1 mit `--remote-debugging-port=0` + `DevToolsActivePort`, Tabs, Bild, Eingaben, Dialoge,
Downloads), `server.py` (Ingress, `/ws`, Startseite, Lesezeichen, Upload, Downloads), `hass.py` (Geräte mit
`configuration_url` über `config/device_registry/list`).
- Bild: `Page.startScreencast` (JPEG) des aktiven Tabs → binär an den Browser (4 Byte Länge + JSON-Kopf + JPEG).
  Der Browser schickt nach dem Zeichnen `ack`; erst dann bekommt er das nächste Bild, und erst dann bestätigt der
  Server Chromium das Bild (spätestens nach 1 s) – so viele Bilder, wie der schnellste Zuschauer schafft.
- Größe: `Emulation.setDeviceMetricsOverride` aus Fenstergröße, Zoom und Pixeldichte (höchstens 2560 px); Ansicht
  „Handy“ = `mobile` + Android-User-Agent. Bestimmt vom zuletzt geöffneten Fenster bzw. von dem, das zuletzt etwas
  eingegeben hat. „HeadlessChrome“ wird aus dem User-Agent entfernt.
- `<select>`-Popups fehlen im Screencast: vor jedem Linksklick prüft `PICK_JS` per `Runtime.evaluate` das Element
  (auch Iframes gleicher Herkunft, Shadow DOM), die Oberfläche zeigt die Liste, `CHOOSE_JS` setzt den Wert.
- Basic Auth über `Fetch.enable` mit `handleAuthRequests`, nur für `resourceType: Document` (sonst würde jede Anfrage
  angehalten). Gemerkte Anmeldungen in `/data/logins.json` (0600). Dateiauswahl über
  `Page.setInterceptFileChooserDialog` + Upload in 8-MB-Teilen + `DOM.setFileInputFiles`. Downloads per
  `Browser.setDownloadBehavior` (`allowAndName`) nach `/share/browser/downloads/.laden`, danach umbenannt.
- Chromium läuft als Benutzer `browser` (ohne Root, `--no-sandbox` nötig im Container) mit eigener, leerer Umgebung
  ohne `SUPERVISOR_TOKEN`. `file:`, `chrome:` usw. sind in der Adresszeile gesperrt.
- Ohne Zuschauer nach `idle_minutes` beendet; Tabs in `/data/session.json`, beim Start wiederhergestellt.
- Lokal testen: `BROWSER_CHROMIUM=<msedge.exe oder chromium> BROWSER_DATA=… BROWSER_OPTIONS=… BROWSER_DOWNLOADS=…
  BROWSER_NO_GUARD=1 BROWSER_PORT=… SUPERVISOR_TOKEN=x BROWSER_SUPERVISOR_URL=… python bollisoft_browser/app/server.py`
  (Ersatz-Supervisor mit WebSocket `config/device_registry/list` und `config/area_registry/list`). Ein zweiter
  DevTools-Client (z. B. Playwright `connect_over_cdp`) schließt Dialoge selbst und leitet Downloads um – im Test
  `page.on("dialog", …)` setzen und Downloads vorher prüfen.

## BolliSoft Kamera (`esp32cam/`)
ESP32-CAM-Firmware (PlatformIO, Arduino-Core 2.x über espressif32@6.9.0) für WerWarDas und Vorrat.
- `/capture` (`?led=0..255`, `?flash=1`, `?settle=`), Livestream auf **eigenem** `esp_http_server` Port 81
  (`/stream`, blockiert so Port 80 nicht), `/settings` (Passwort = OTA-Passwort).
- WLAN, Name, Passwort liegen im NVS (WiFiManager-Hotspot „BolliSoft-Kamera-XXXX“). Eine optionale `secrets.h`
  wird beim ersten Start übernommen – Release-Firmware darf keine Zugangsdaten enthalten.
- GitHub-Updater: Releases-Liste (`?per_page=20`, ArduinoJson mit Filter, `useHTTP10` gegen chunked), Tag-Präfix
  `esp32cam-v`, Datei `bollisoft-cam.bin`, `httpUpdate` mit Weiterleitung zu release-assets.githubusercontent.com.
  TLS mit `src/github_ca.h` (USERTrust ECC für die API, Let's Encrypt Root YR für Downloads, dazu Reserve-Roots);
  vorher NTP, sonst schlägt die Zertifikatsprüfung fehl. Vor dem Flashen Kamera und Livestream abschalten (DMA).
- Selbst gebaut = Version `0.0.0` → wird vom nächsten Release ersetzt. Workflow `esp32cam.yml` setzt
  `FW_VERSION` über `PLATFORMIO_BUILD_FLAGS`, prüft per `grep`, dass sie in der Firmware steckt, und legt
  zusätzlich `bollisoft-cam-komplett.bin` (merge_bin, Adresse 0x0) fürs erste Flashen ab.

## WerWarDas (`werwardas/`, `ollama/`, `werwardas-apps/`)
Aufräum-Wächter, bis 11/2026 eigenes Repository `Devilwitha/werwardas` (Verlauf mit übernommen).
- `werwardas/` und `ollama/` sind die Add-ons. Das Kamera-Add-on lädt beim Bauen das Archiv von HAaddons `main`
  und kopiert `werwardas-apps/pi/werwardas` – der Python-Code existiert nur einmal.
- Interne Add-on-Adresse von Ollama: `http://ac854b7e-ollama:11434` (`ac854b7e` = sha1 der Repo-URL in
  Kleinbuchstaben, erste 8 Zeichen).
- `werwardas-apps/`: `pi/` (Kameradienst, `install.sh` klont HAaddons nach `/opt/werwardas`, Code in
  `werwardas-apps/pi`), `windows/` (C#/.NET 8), `android/` (Kotlin, Apps Kind + Eltern), `homeassistant/`
  (Blueprint, Beispiel-Karte). Die ESP32-CAM-Firmware liegt seit 11/2026 im Hauptordner `esp32cam/`.
- Releases liegen neben denen des PC Agent; jedes Programm filtert nach seinem Tag-Präfix:
  `pcagent-v…`, `werwardas-v1.0.N` (Windows), `werwardas-android-v1.0.N` (N = versionCode), `esp32cam-v1.0.N`. Die Updater dürfen nie
  `releases/latest` benutzen. Workflows `werwardas-*.yml`: Laufnummer + 100 (liegt über den Versionen aus dem alten
  Repository). Android-Releases nur mit den Secrets `WERWARDAS_KEYSTORE_BASE64`/`WERWARDAS_KEYSTORE_PASSWORD`.
- `HaAuth.CLIENT_ID` bleibt `https://github.com/Devilwitha/werwardas`: HA bindet die Refresh-Token an die Client-ID.
