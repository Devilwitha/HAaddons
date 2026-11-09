# HA PC Agent – entfernt Autostart und beendet den laufenden Agenten.
$ErrorActionPreference = "SilentlyContinue"
$lnk = Join-Path ([Environment]::GetFolderPath('Startup')) "HA PC Agent.lnk"
if (Test-Path $lnk) { Remove-Item $lnk -Force }
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" |
    Where-Object { $_.CommandLine -like "*agent.py*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Write-Host "Autostart entfernt und Agent beendet." -ForegroundColor Green
Write-Host "Hinweis: Das Geraet bleibt in Home Assistant sichtbar (als 'nicht verfuegbar')."
Write-Host "Zum vollstaendigen Entfernen in HA: Geraet -> ... -> Loeschen."
