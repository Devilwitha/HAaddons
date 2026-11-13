# retropie-mqtt

Verbindet einen RetroPie über MQTT mit Home Assistant. Das Gerät erscheint per
MQTT Discovery automatisch in HA, ohne YAML.

**Entitäten**

| Entität | Typ | Inhalt |
|---|---|---|
| Spielt | binary_sensor | an, solange ein Spiel läuft (Attribute: system, emulator, game, rom, since) |
| Spiel | sensor | Name aus der gamelist.xml, sonst Dateiname |
| System | sensor | z. B. `snes`, `megadrive` |
| Emulator / Letzte Änderung | sensor | Diagnose |
| Pause, Spielstand speichern/laden, Spiel zurücksetzen, Screenshot, Spiel beenden | button | RetroArch-Befehle |
| Neustart, Herunterfahren | button | optional (`ALLOW_POWER`) |

Ist der Pi aus oder das Netzwerk weg, werden alle Entitäten in HA als „nicht verfügbar“ angezeigt.

## Voraussetzungen

- Ein MQTT-Broker, z. B. das **Mosquitto broker**-Add-on in HA, mit eingerichteter MQTT-Integration.
- Ein HA-Benutzer für den RetroPie (Einstellungen → Personen → Benutzer). Das Mosquitto-Add-on akzeptiert HA-Logins.

## Installation (auf dem RetroPie)

```bash
git clone https://github.com/Devilwitha/HAaddons.git
cd HAaddons/retropie-mqtt
sudo ./install.sh
```

Der Installer:

1. installiert `mosquitto-clients`,
2. fragt nach den Broker-Zugangsdaten und schreibt sie nach `/etc/retropie-mqtt.conf`,
3. trägt Hooks in `/opt/retropie/configs/all/runcommand-onstart.sh` und `runcommand-onend.sh` ein (bestehender Inhalt bleibt erhalten),
4. setzt `network_cmd_enable = "true"` in der `retroarch.cfg` (Sicherung: `retroarch.cfg.bak-retropie-mqtt`),
5. richtet den systemd-Dienst `retropie-mqtt` ein und startet ihn.

Ein erneuter Aufruf aktualisiert das Skript und behält die Konfiguration.
Entfernen: `sudo ./install.sh --uninstall`

## MQTT-Topics

| Topic | Inhalt |
|---|---|
| `retropie/<id>/state` | `{"state":"playing","system":"snes","emulator":"lr-snes9x","game":"Super Mario World","rom":"…","since":"…"}` (retained) |
| `retropie/<id>/availability` | `online` / `offline` (retained, Last Will) |
| `retropie/<id>/cmd` | `PAUSE_TOGGLE`, `SAVE_STATE`, `LOAD_STATE`, `RESET`, `SCREENSHOT`, `MENU_TOGGLE`, `FAST_FORWARD`, `STATE_SLOT_PLUS`, `STATE_SLOT_MINUS`, `QUIT`, `REBOOT`, `SHUTDOWN` |

Andere Payloads werden ignoriert.

## Beispiel-Automation

```yaml
alias: Licht dimmen beim Zocken
triggers:
  - trigger: state
    entity_id: binary_sensor.retropie_spielt
    to: "on"
actions:
  - action: light.turn_on
    target:
      entity_id: light.wohnzimmer
    data:
      brightness_pct: 20
```

## Hinweise

- Pause, Speichern, Laden, Reset und Screenshot funktionieren nur mit RetroArch-Kernen (`lr-*`).
  „Spiel beenden“ funktioniert auch mit Standalone-Emulatoren.
- Das Passwort steht in `/etc/retropie-mqtt.conf` (lesbar für root und die Gruppe des RetroPie-Benutzers).
- Logs: `journalctl -u retropie-mqtt -f` (Dienst) und `journalctl -t retropie-mqtt` (Hooks).
- Verbindung testen: `retropie-mqtt test`
