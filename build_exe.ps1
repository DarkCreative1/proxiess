$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    py -3 -m venv .venv
}
& ".venv\Scripts\python.exe" -m pip install -r requirements-dev.txt
& ".venv\Scripts\python.exe" -m PyInstaller `
    --noconfirm --clean --windowed --onedir `
    --name ProxyPulse `
    --collect-all aiohttp_socks `
    app.py

Write-Host "EXE hazır: $PSScriptRoot\dist\ProxyPulse\ProxyPulse.exe"

