# Install Nexus Brain dependencies
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot\..

Write-Host "Installing Python dependencies..." -ForegroundColor Cyan
py -m pip install -r requirements.txt

Write-Host "Checking Ollama..." -ForegroundColor Cyan
$ollama = Get-Command ollama -ErrorAction SilentlyContinue
if (-not $ollama) {
    Write-Host "WARNING: Ollama not found in PATH. Install from https://ollama.com" -ForegroundColor Yellow
} else {
    ollama list
}

# Create data directories
@("data\inbox", "data\logs", "data\workflows", "data\spawned_agents", "data\inbox\processed") | ForEach-Object {
    New-Item -ItemType Directory -Force -Path $_ | Out-Null
}

Write-Host "`nNexus Brain installed. Run: .\scripts\start.ps1" -ForegroundColor Green
