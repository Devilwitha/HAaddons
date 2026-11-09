# Claude für Home Assistant

Chatte direkt in Home Assistant mit Claude – so wie auf claude.ai. Claude kann
Automationen anlegen, deine Konfiguration ändern, prüfen und neu laden. Jede
Änderung musst du vorher mit **Erlauben** bestätigen.

Über **Remote Control** erscheint dein Home Assistant zusätzlich in der Claude App
und auf claude.ai/code – so kannst du auch von unterwegs Änderungen machen.

## Erste Schritte
1. Add-on starten und **Claude** in der Seitenleiste öffnen.
2. Auf **Anmelden** tippen, im neuen Fenster mit deinem Claude-Konto anmelden.
3. Den angezeigten Code kopieren, einfügen, **Verbinden** – fertig.

Die Anmeldung bleibt gespeichert, auch nach Updates und Neustarts.
Voraussetzung ist ein Claude-Abo (Pro, Max, Team oder Enterprise).

## Optionen
| Option | Bedeutung |
|---|---|
| Remote Control | Home Assistant in der Claude App / auf claude.ai/code verfügbar machen |
| Session-Name | Name, unter dem es dort erscheint (Standard: „HAOS“) |
| Modell | Optional, z. B. `sonnet` oder `opus`. Leer = Standard |
| Berechtigungsmodus | `default`: jede Änderung bestätigen · `acceptEdits`: Dateiänderungen ohne Rückfrage · `plan`: Claude macht erst einen Plan |

## Tipps
- Mach vor größeren Änderungen ein Backup (Einstellungen → System → Backups).
- Beispiele: „Schalte das Flurlicht bei Bewegung nach Sonnenuntergang ein“,
  „Welche Batterien sind fast leer?“, „Prüfe meine Konfiguration“.
