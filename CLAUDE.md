# CLAUDE.md – HAaddons (BolliSoft)

Home-Assistant-Add-on-Repository von BolliSoft (`repository.yaml`). Jedes Add-on liegt in einem
eigenen Ordner mit `config.yaml`; HA liest den Stand von `main` auf GitHub. Hilfsprogramme, die nicht
im Add-on-Container laufen, liegen ebenfalls im Hauptordner (`pc_agent/`, `retropie-mqtt/`, `rpi5-bridge/`).

## Allgemein
- Texte für Nutzer auf Deutsch. Autor in Beschreibungen/DOCS: „von BolliSoft“.
- Bei jeder Änderung an einem Add-on `version` in dessen `config.yaml` erhöhen und `CHANGELOG.md`
  ergänzen, sonst bietet HA kein Update an.
- Python 4 Leerzeichen; YAML/JSON/JS/HTML/CSS 2 Leerzeichen. Keine Platzhalter/TODOs.
- Oberflächen laufen hinter Ingress: nur relative URLs, keine externen CDNs, Zugriff nur von 172.30.32.2.

## GBA- und N64-Emulator (`gba_emulator/`, `n64_emulator/`)
- `server.py` und alles in `web/` sind in beiden Add-ons **identisch** – nur `web/static/system.js`
  (Name, Kern, Endungen, Touch-Layout, Controller-Vorlagen) und das `Dockerfile` (`EMU_SYSTEM`,
  Kerne) unterscheiden sich. Änderungen immer in beide Ordner übernehmen und mit `diff` prüfen.
- EmulatorJS wird im Dockerfile von npm geladen (SHA-256 gepinnt), zu `emulator.min.js` zusammengefügt
  und an zwei Stellen gepatcht (Steuerkreuz-Mitte, Analogstick bei 0°); die `grep`-Prüfungen müssen bleiben.

## Game Remote (`switch2_remote_play/` + `rpi5-bridge/`)
Sichtbarer Name **Game Remote**; technische Namen (Ordner `switch2_remote_play`, Slug, Dienst
`switch2-bridge`, Pfade `/opt/switch2-*`, `/etc/switch2-bridge`) bleiben bewusst so.

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
- Controller-Belegung im Browser: Vorlagen „wie Switch-Anordnung“ / „nach Beschriftung“, eigene
  Belegung tauscht bei Doppelbelegung; Xbox-Erkennung vor PlayStation prüfen („Xbox Wireless Controller“).

### Lokal testen (ohne Pi und ohne Switch)
```bash
# Bridge mit Testbild und ohne Bluetooth (config mit video.device: "test")
python rpi5-bridge/main.py --config /pfad/test.yaml --dry-run
# Add-on-Server gegen die lokale Bridge
SWITCH2_OPTIONS=/pfad/options.json SWITCH2_NO_GUARD=1 python switch2_remote_play/app/server.py
```
`SWITCH2_NO_GUARD=1` schaltet die Ingress-Sperre ab, `SWITCH2_PORT` ändert den Port. Im Testmodus
protokolliert die Bridge jede empfangene Eingabe („Testmodus - Eingabe: …“).
Benötigt lokal: `aiohttp aiortc websockets PyYAML opencv-python-headless numpy`.
