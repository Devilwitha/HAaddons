# Claude Code für Home Assistant OS

Läuft Claude Code direkt auf deinem HAOS-Rechner (z. B. Raspberry Pi 4/5) mit
Schreibzugriff auf `/config` und Zugriff auf die Home-Assistant-API. Über
**Remote Control** steuerst du die Session vom Handy (Claude App) oder Browser
(claude.ai/code) aus – Claude kann so Automationen anlegen, YAML ändern, die
Konfiguration prüfen und neu laden.

## Voraussetzungen
- Claude Pro/Max (oder Team/Enterprise) – Remote Control funktioniert nur mit
  claude.ai-Login, nicht mit einem API-Key.
- 64-bit HAOS (aarch64 oder amd64).

## Einrichtung
1. Add-on starten, dann **Claude Code** in der Seitenleiste öffnen.
2. Im Terminal `claude` eingeben und `/login` ausführen. Den angezeigten Link
   im Browser öffnen, anmelden, den Code zurück ins Terminal kopieren.
3. Claude mit `/exit` beenden. Das Fenster **remote** (`Ctrl-b 1`) verbindet sich
   innerhalb von 30 Sekunden und zeigt einen Link/QR-Code.
4. In der Claude App bzw. auf claude.ai/code erscheint die Session unter dem
   eingestellten Namen (Standard: „HAOS“).

Login und Einstellungen liegen in `/data/home` und überleben Updates und Neustarts.

## Optionen
| Option | Bedeutung |
|---|---|
| `remote_control` | Remote Control dauerhaft laufen lassen |
| `session_name` | Name der Session in App/Web |
| `model` | Optionales Modell, z. B. `sonnet` oder `opus` |
| `permission_mode` | `default` (fragt nach), `acceptEdits` (Dateiänderungen ohne Rückfrage), `plan` |

## Helfer im Terminal
- `ha-api GET /states` – Entitäten abfragen
- `ha-api POST /services/automation/reload` – Automationen neu laden
- `ha-check` – Konfiguration prüfen
- `ha-logs 100` – Core-Log

Claude kennt diese Befehle automatisch (über `~/.claude/haos-addon.md`).

## Sicherheit
Das Add-on kann deine gesamte HA-Konfiguration ändern und Dienste aufrufen.
Lass `permission_mode` auf `default`, damit jede Änderung im Handy/Browser
bestätigt werden muss, und erstelle vorher ein Backup – oder mach `/config` zu
einem Git-Repository (`cd /config && git init && git add -A && git commit -m init`;
`secrets.yaml` und `.storage` vorher in `.gitignore` eintragen).
