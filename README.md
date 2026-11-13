# HAaddons

Home-Assistant-Add-on-Repository von **BolliSoft**.

[![Repository hinzufügen](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2FDevilwitha%2FHAaddons)

## Add-ons

### [Claude Code](claude_code/)
Chatte mit Claude direkt in der HA-Seitenleiste, im Stil von claude.ai. Claude legt
Automationen an und ändert die Konfiguration, jede Änderung bestätigst du per Klick.
Mit Remote Control geht das auch von unterwegs in der Claude App. Details: [DOCS.md](claude_code/DOCS.md)

### [Bollisoft Server Monitor](bollisoft_monitor/)
CPU, RAM, Laufwerke, Laufzeit, Netzwerk, Ollama und die Dienste eines Windows-Servers in der
HA-Seitenleiste und als Sensoren – mit Knöpfen für Neustart (mit Vorlauf, abbrechbar), Neustart
der Bollisoft-Dienste und Entladen der Ollama-Modelle. Kein Herunterfahren, kein Sleep. Die Daten
schickt `bollisoft-agent.exe` vom Server. Details: [DOCS.md](bollisoft_monitor/DOCS.md)

## Installation
Einstellungen → Add-ons → Add-on Store → ⋮ → Repositories →
`https://github.com/Devilwitha/HAaddons` hinzufügen.
