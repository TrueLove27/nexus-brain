# Create Nexus PostgreSQL database (requires PostgreSQL installed)
$ErrorActionPreference = "Stop"

$DB_NAME = "nexus_brain"
$DB_USER = "postgres"
$DB_PASS = "postgres"
$DB_HOST = "localhost"
$DB_PORT = 5432

Write-Host "Nexus PostgreSQL Setup" -ForegroundColor Cyan
Write-Host ""

# Check if psql is available
$psql = Get-Command psql -ErrorAction SilentlyContinue
if (-not $psql) {
    Write-Host "PostgreSQL CLI (psql) not found." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Install options:" -ForegroundColor White
    Write-Host "  1. Download: https://www.postgresql.org/download/windows/" -ForegroundColor Gray
    Write-Host "  2. Docker:   docker run -d --name nexus-pg -e POSTGRES_PASSWORD=postgres -p 5432:5432 postgres:16" -ForegroundColor Gray
    Write-Host ""
    Write-Host "Nexus will fall back to SQLite until PostgreSQL is available." -ForegroundColor Yellow
    Write-Host "Edit config/brain.yaml to match your connection settings." -ForegroundColor Gray
    exit 0
}

Write-Host "Creating database '$DB_NAME' if missing..." -ForegroundColor White
$env:PGPASSWORD = $DB_PASS
$exists = psql -h $DB_HOST -p $DB_PORT -U $DB_USER -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'"
if ($exists -ne "1") {
    psql -h $DB_HOST -p $DB_PORT -U $DB_USER -d postgres -c "CREATE DATABASE $DB_NAME;"
    Write-Host "Database created." -ForegroundColor Green
} else {
    Write-Host "Database already exists." -ForegroundColor Green
}

Write-Host ""
Write-Host "Initializing schema via Nexus..." -ForegroundColor White
Set-Location $PSScriptRoot\..
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& py -m pip install "psycopg[binary]>=3.1" -q --disable-pip-version-check 2>$null | Out-Null
$ErrorActionPreference = $prevEAP
py -c @"
from pathlib import Path
import sys
sys.path.insert(0, str(Path('.').resolve()))
from brain.store import create_memory
import yaml
with open('config/brain.yaml') as f:
    cfg = yaml.safe_load(f)
mem = create_memory(cfg['brain'], cfg['llm'], Path('.').resolve())
print(f'Storage ready: {mem.storage_type}')
"@

Write-Host ""
Write-Host "Done. Connection: postgresql://${DB_USER}:****@${DB_HOST}:${DB_PORT}/${DB_NAME}" -ForegroundColor Green
