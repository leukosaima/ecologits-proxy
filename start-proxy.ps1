#Requires -Version 7
$ErrorActionPreference = "Stop"
$repo = $PSScriptRoot
$venv = Join-Path $repo ".venv"

# Load local, untracked configuration if present.
$config = Join-Path $repo "config.local.ps1"
if (Test-Path $config) {
    . $config
}

if (-not $env:UPSTREAM_BASE_URL) {
    throw "UPSTREAM_BASE_URL is not set. Copy config.example.ps1 to config.local.ps1 and configure it."
}

if (-not (Test-Path $venv)) {
    Write-Host "Creating Python virtual environment..." -ForegroundColor Cyan
    python -m venv $venv
}

Write-Host "Installing / upgrading dependencies..." -ForegroundColor Cyan
& (Join-Path $venv "Scripts\python.exe") -m pip install -q -r (Join-Path $repo "requirements.txt")

Write-Host "Starting EcoLogits proxy on http://127.0.0.1:8787 (upstream: $env:UPSTREAM_BASE_URL)" -ForegroundColor Green
& (Join-Path $venv "Scripts\python.exe") -m uvicorn proxy:app --app-dir $repo --host 127.0.0.1 --port 8787
