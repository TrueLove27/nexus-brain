# Start Nexus Desktop App (no browser)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot\..

Write-Host "Starting Nexus Desktop App..." -ForegroundColor Cyan

# Ensure Ollama is running
try {
    Invoke-WebRequest -Uri "http://localhost:11434/api/tags" -TimeoutSec 3 -UseBasicParsing | Out-Null
} catch {
    Write-Host "Starting Ollama..." -ForegroundColor Yellow
    Start-Process "ollama" -ArgumentList "serve" -WindowStyle Hidden
    Start-Sleep -Seconds 3
}

# Install deps - pip writes notices to stderr; don't let that stop the script
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& py -m pip install -r requirements.txt -q --disable-pip-version-check 2>$null | Out-Null
$ErrorActionPreference = $prevEAP
if ($LASTEXITCODE -ne 0) {
    Write-Host "Warning: pip install had issues (exit $LASTEXITCODE) - continuing anyway" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "  Desktop app window will open shortly" -ForegroundColor Green
Write-Host "  Work shown in Nexus Live Workspace (external editor disabled)" -ForegroundColor Green
Write-Host ""

py ui/nexus_app.py
