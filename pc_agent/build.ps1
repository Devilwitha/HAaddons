# HA PC Agent – baut eine eigenständige Windows-.exe (dist\HA PC Agent.exe).
# Ausführen in PowerShell im Ordner pc_agent:
#   powershell -ExecutionPolicy Bypass -File build.ps1
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

if (-not (Test-Path "$here\venv")) {
    Write-Host "Erstelle virtuelle Umgebung ..."
    python -m venv "$here\venv"
}
$py = "$here\venv\Scripts\python.exe"
& $py -m pip install --quiet --upgrade pip
Write-Host "Installiere Abhaengigkeiten + PyInstaller ..."
& $py -m pip install --quiet -r "$here\requirements.txt" pyinstaller

Write-Host "Baue .exe ..."
& "$here\venv\Scripts\pyinstaller.exe" --noconsole --onefile `
    --name "HA PC Agent" --icon "icon.ico" --add-data "icon.ico;." `
    --distpath "$here\dist" --workpath "$here\build" --specpath "$here\build" `
    "$here\agent.py"

Write-Host ""
Write-Host "Fertig: $here\dist\HA PC Agent.exe" -ForegroundColor Green
Write-Host "Diese .exe kannst du irgendwohin kopieren und doppelklicken."
Write-Host "Beim ersten Start oeffnet sich das Einstellungsfenster."
