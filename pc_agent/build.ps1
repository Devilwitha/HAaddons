# HA PC Agent – baut eine eigenständige Windows-.exe (dist\HA PC Agent.exe).
# Ausführen in PowerShell im Ordner pc_agent:
#   powershell -ExecutionPolicy Bypass -File build.ps1
#   powershell -ExecutionPolicy Bypass -File build.ps1 -Release   # + GitHub-Release
#
# -Release lädt die fertige .exe als GitHub-Release hoch (Tag "pcagent-v<Version>"
# aus version.py). Dafür muss die GitHub CLI installiert und angemeldet sein
# (einmalig: gh auth login). Danach aktualisieren sich bestehende .exe von selbst.
param([switch]$Release)
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
    --name "HA PC Agent" --icon "$here\icon.ico" --add-data "$here\icon.ico;." `
    --distpath "$here\dist" --workpath "$here\build" --specpath "$here\build" `
    "$here\agent.py"

$exe = "$here\dist\HA PC Agent.exe"
Write-Host ""
Write-Host "Fertig: $exe" -ForegroundColor Green
Write-Host "Diese .exe kannst du irgendwohin kopieren und doppelklicken."
Write-Host "Beim ersten Start oeffnet sich das Einstellungsfenster."

if ($Release) {
    if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
        Write-Warning "GitHub CLI (gh) nicht gefunden - Release uebersprungen. Installieren: winget install GitHub.cli"
        return
    }
    $m = Select-String -Path "$here\version.py" -Pattern '__version__\s*=\s*"([^"]+)"'
    $ver = $m.Matches[0].Groups[1].Value
    $tag = "pcagent-v$ver"
    Write-Host ""
    Write-Host "Veroeffentliche GitHub-Release $tag ..." -ForegroundColor Cyan
    & gh release view $tag *> $null
    if ($LASTEXITCODE -ne 0) {
        & gh release create $tag "$exe" --title "HA PC Agent $ver" `
            --notes "Automatischer Build der Windows-.exe. Selbst-Update ist aktiv."
    } else {
        Write-Host "Release existiert bereits - ersetze die .exe ..."
        & gh release upload $tag "$exe" --clobber
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Release fehlgeschlagen. Angemeldet? Einmalig: gh auth login"
    } else {
        Write-Host "Release veroeffentlicht: $tag" -ForegroundColor Green
        Write-Host "Bestehende Installationen aktualisieren sich automatisch."
    }
}
