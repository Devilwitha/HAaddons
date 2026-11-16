# CLAUDE.md – HAaddons (BolliSoft)

Home-Assistant-Add-on-Repository von BolliSoft (`repository.yaml`). Jedes Add-on liegt in einem
eigenen Ordner mit `config.yaml`; HA liest den Stand von `main` auf GitHub. Hilfsprogramme, die nicht
im Add-on-Container laufen, liegen ebenfalls im Hauptordner (`pc_agent/`, `retropie-mqtt/`, `rpi5-bridge/`,
`werwardas-apps/`).

## Allgemein
- Jedes Dockerfile mit Python-/Shell-CMD braucht `ENV S6_KEEP_ENV=1` (oder `#!/usr/bin/with-contenv`):
  s6-overlay im HA-Basis-Image leert sonst die Umgebung – `SUPERVISOR_TOKEN` und eigene ENV-Werte fehlen.
- Texte für Nutzer auf Deutsch. Autor in Beschreibungen/DOCS: „von BolliSoft“.
- Bei jeder Änderung an einem Add-on `version` in dessen `config.yaml` erhöhen und `CHANGELOG.md`
  ergänzen, sonst bietet HA kein Update an.
- Python 4 Leerzeichen; YAML/JSON/JS/HTML/CSS 2 Leerzeichen. Keine Platzhalter/TODOs.
- Oberflächen laufen hinter Ingress: nur relative URLs, keine externen CDNs, Zugriff nur von 172.30.32.2.

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
  und an zwei Stellen gepatcht (Steuerkreuz-Mitte, Analogstick bei 0°); die `grep`-Prüfungen müssen bleiben.

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
  positiv), `{"type":"heartbeat","t":ms}`, `{"type":"system","action":"pair"}`
- Bridge → Browser: `{"type":"status","switch":{state,detail},"capture":{...},"clients":n}`,
  `{"type":"pong","t":ms}`, `{"type":"info"|"error","message":...}`
- Der Ingress-Server schickt zusätzlich `{"type":"proxy","status":"connected"|"unreachable"|"bridge_closed"}`.
- Schnellster Weg für Eingaben: WebRTC-Datenkanal `input` (ungeordnet, `maxRetransmits: 0`) direkt zum Pi,
  Nachrichten wie `input` plus `seq` (ältere verworfen). Der Browser wiederholt den Zustand alle 50 ms; nach
  0,5 s ohne Paket gilt der Kanal als neutral. Ohne offenen Kanal gehen Eingaben über den WebSocket; beim
  Wechsel schickt der Browser auf dem bisherigen Weg einmal „alles los“.
- Tastennamen: A B X Y L R ZL ZR MINUS PLUS HOME CAPTURE DPAD_UP/DOWN/LEFT/RIGHT LS RS
  (müssen in `virtual-gamepad.js`, `app.js` und `controller_emulator.py` übereinstimmen).

### Wichtige Entscheidungen
- **nxbt 0.1.4** (2021, ungepflegt) pinnt Pakete, die unter Python ≥ 3.11 nicht bauen. Für die
  Emulation braucht es nur `dbus-python` → `pip install --no-deps nxbt==0.1.4`, `python3-dbus` per apt.
  nxbt nicht in `requirements.txt` aufnehmen.
- **venv mit `--system-site-packages`**: `python3-opencv`, `python3-numpy`, `python3-dbus` kommen aus apt.
  Keine Abhängigkeit hinzufügen, die NumPy 2 per pip nachzieht (bricht das apt-OpenCV).
- **WebRTC** nur im LAN (Medien gehen direkt Pi → Browser). Das Frontend fällt nach 8 s automatisch auf
  MJPEG zurück (läuft komplett über Ingress). aiortc ohne STUN-Server starten
  (`RTCConfiguration(iceServers=[])`), sonst wartet der Aufbau Sekunden auf Google-STUN.
- **Watchdog** 2,0 s ohne Heartbeat/Eingabe → alle Tasten los, Sticks (0, 0).
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

## WerWarDas (`werwardas/`, `ollama/`, `werwardas-apps/`)
Aufräum-Wächter, bis 11/2026 eigenes Repository `Devilwitha/werwardas` (Verlauf mit übernommen).
- `werwardas/` und `ollama/` sind die Add-ons. Das Kamera-Add-on lädt beim Bauen das Archiv von HAaddons `main`
  und kopiert `werwardas-apps/pi/werwardas` – der Python-Code existiert nur einmal.
- Interne Add-on-Adresse von Ollama: `http://ac854b7e-ollama:11434` (`ac854b7e` = sha1 der Repo-URL in
  Kleinbuchstaben, erste 8 Zeichen).
- `werwardas-apps/`: `pi/` (Kameradienst, `install.sh` klont HAaddons nach `/opt/werwardas`, Code in
  `werwardas-apps/pi`), `windows/` (C#/.NET 8), `android/` (Kotlin, Apps Kind + Eltern), `esp32cam/`, `homeassistant/`
  (Blueprint, Beispiel-Karte).
- Releases liegen neben denen des PC Agent; jedes Programm filtert nach seinem Tag-Präfix:
  `pcagent-v…`, `werwardas-v1.0.N` (Windows), `werwardas-android-v1.0.N` (N = versionCode). Die Updater dürfen nie
  `releases/latest` benutzen. Workflows `werwardas-*.yml`: Laufnummer + 100 (liegt über den Versionen aus dem alten
  Repository). Android-Releases nur mit den Secrets `WERWARDAS_KEYSTORE_BASE64`/`WERWARDAS_KEYSTORE_PASSWORD`.
- `HaAuth.CLIENT_ID` bleibt `https://github.com/Devilwitha/werwardas`: HA bindet die Refresh-Token an die Client-ID.
