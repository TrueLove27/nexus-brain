# Start Nexus Brain in interactive mode
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot\..

# Ensure Ollama is reachable
try {
    $resp = Invoke-WebRequest -Uri "http://localhost:11434/api/tags" -TimeoutSec 3 -UseBasicParsing
} catch {
    Write-Host "Starting Ollama..." -ForegroundColor Yellow
    Start-Process "ollama" -ArgumentList "serve" -WindowStyle Hidden
    Start-Sleep -Seconds 3
}

py main.py
