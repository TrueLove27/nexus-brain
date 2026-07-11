# Start Nexus Live — the full visual experience
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot\..

Write-Host "Starting Nexus Live..." -ForegroundColor Cyan

# Ensure Ollama is running
try {
    Invoke-WebRequest -Uri "http://localhost:11434/api/tags" -TimeoutSec 3 -UseBasicParsing | Out-Null
} catch {
    Start-Process "ollama" -ArgumentList "serve" -WindowStyle Hidden
    Start-Sleep -Seconds 3
}

# Install deps if needed
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& py -m pip install -r requirements.txt -q --disable-pip-version-check 2>$null | Out-Null
$ErrorActionPreference = $prevEAP

Write-Host ""
Write-Host "  Dashboard:  http://127.0.0.1:9477" -ForegroundColor Green
Write-Host "  Hotkey:     Win+Shift+N (type from anywhere)" -ForegroundColor Green
Write-Host "  Music:      Wallpaper auto-syncs to now playing" -ForegroundColor Green
Write-Host ""

py ui/nexus_live.py
