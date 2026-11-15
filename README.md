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

### [GBA Emulator](gba_emulator/)
Game-Boy-Advance-, Game-Boy- und Game-Boy-Color-Spiele in der HA-Seitenleiste spielen, am PC,
Tablet oder Handy (mit Touch-Gamepad). ROMs per Upload oder Samba nach `/share/gba`. Spielstände
und Speicherstände liegen auf dem Server, so geht es auf jedem Gerät am selben Stand weiter.
Details: [DOCS.md](gba_emulator/DOCS.md)

### [N64 Emulator](n64_emulator/)
Nintendo-64-Spiele in der HA-Seitenleiste spielen, am PC, Tablet oder Handy (Touch-Gamepad mit
Analogstick und C-Tasten). ROMs per Upload oder Samba nach `/share/n64`. Spielstände und
Speicherstände liegen auf dem Server. Details: [DOCS.md](n64_emulator/DOCS.md)

### [Game Remote](switch2_remote_play/)
Eine Spielkonsole aus der HA-Seitenleiste spielen: Video über WebRTC (automatisch MJPEG von
unterwegs), Touch-Gamepad mit zwei Sticks und echte Controller (Xbox, PlayStation) mit einstellbarer
Belegung. Braucht einen Raspberry Pi 5 mit USB-Capture-Card neben der Konsole, der sich per
Bluetooth als Pro Controller ausgibt – eingerichtet mit einem Befehl, siehe
[rpi5-bridge](rpi5-bridge/). Details: [DOCS.md](switch2_remote_play/DOCS.md)

## Installation
Einstellungen → Add-ons → Add-on Store → ⋮ → Repositories →
`https://github.com/Devilwitha/HAaddons` hinzufügen.
