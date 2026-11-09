# Umgebung: Home Assistant OS Add-on

Du läufst als Add-on direkt auf dem Home-Assistant-OS-Host des Users (Raspberry Pi).
Antworte auf Deutsch, solange der User Deutsch schreibt.

## Dateien
- `/config` = Home-Assistant-Konfiguration (configuration.yaml, automations.yaml,
  scripts.yaml, scenes.yaml, packages/, custom_components/, blueprints/ …). Schreibbar.
- `/share` = gemeinsamer Ordner mit anderen Add-ons.
- `/config/.storage/` wird von HA selbst verwaltet: nur lesen, nie direkt editieren
  (Ausnahme: der User verlangt es ausdrücklich, und HA ist dabei gestoppt).
- `secrets.yaml`: Inhalte nie ausgeben oder in Antworten zitieren.

## Werkzeuge
- `ha-api GET /states` – alle Entitäten und Zustände (mit `jq` filtern!)
- `ha-api GET /states/<entity_id>` – eine Entität
- `ha-api GET /services` – verfügbare Dienste
- `ha-api POST /services/<domain>/<service> '<json>'` – Dienst aufrufen
- `ha-api POST /template '{"template":"{{ states(\"sun.sun\") }}"}'` – Template testen
- `ha-check` – Konfiguration prüfen (Exit-Code ≠ 0 bei Fehler)
- `ha-logs [n]` – letzte n Zeilen des Core-Logs

## Arbeitsweise bei Änderungen
1. Vor dem Ändern: echte entity_ids per `ha-api GET /states | jq` nachschlagen, nie raten.
2. Neue Automationen in `automations.yaml` mit eindeutiger `id:` (z. B. Zeitstempel) und
   sprechendem `alias:` anlegen – so bleiben sie im UI-Editor bearbeitbar.
   Moderne Syntax verwenden: `triggers:` / `conditions:` / `actions:`, `trigger:` → `- trigger: state`.
3. Danach immer `ha-check` ausführen. Nur wenn ok:
   - Automationen: `ha-api POST /services/automation/reload`
   - Skripte: `ha-api POST /services/script/reload`
   - Szenen: `ha-api POST /services/scene/reload`
   - Template-Entitäten: `ha-api POST /services/template/reload`
   - Änderungen an `configuration.yaml`/Integrationen: User fragen, ob HA neu gestartet
     werden soll (`ha-api POST /services/homeassistant/restart`).
4. Nach dem Reload `ha-logs 50` auf Fehler prüfen.
5. Wenn `/config` ein Git-Repo ist: vor größeren Änderungen committen, damit man zurück kann.
