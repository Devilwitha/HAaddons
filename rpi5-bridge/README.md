# Game Remote – Raspberry Pi 5 Bridge

Gegenstück zum Home-Assistant-Add-on [Game Remote](../switch2_remote_play/). Läuft auf einem
Raspberry Pi 5 neben der Spielkonsole: gibt sich per Bluetooth als Pro Controller aus (nxbt)
und streamt das Bild der USB-Capture-Card (WebRTC und MJPEG) an Home Assistant.

## Installieren / aktualisieren
```bash
curl -sSL https://raw.githubusercontent.com/Devilwitha/HAaddons/main/rpi5-bridge/install.sh | sudo bash
```
Am Ende zeigt das Skript die IP des Pi und die Ports 8765/8766 für die Add-on-Konfiguration.

## Einstellungen
`/etc/switch2-bridge/config.yaml` (Gerät, Auflösung, Bildraten, Watchdog), danach
`sudo systemctl restart switch2-bridge`. Mit `device: "test"` gibt es ein Testbild ohne Capture-Card. `device: "auto"` (Standard) sucht die
Capture-Card selbst – die Nummer `/dev/videoN` ändert sich nach Umstecken oder Neustart.

## Protokoll und Status
```bash
sudo systemctl status switch2-bridge
journalctl -u switch2-bridge -f
```

## Entfernen
```bash
sudo bash /opt/switch2-remote-play/rpi5-bridge/uninstall.sh           # Dienst und Programm
sudo bash /opt/switch2-remote-play/rpi5-bridge/uninstall.sh --purge   # zusätzlich Einstellungen
```

Ausführliche Anleitung (Koppeln, Controller-Belegung, Fehlersuche): [DOCS.md des Add-ons](../switch2_remote_play/DOCS.md).
