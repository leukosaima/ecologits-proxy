#Requires -Version 7
$ErrorActionPreference = "Stop"
$repo = $PSScriptRoot

# Clean previous test log.
$testLast = "$env:USERPROFILE\.local\share\opencode\ecologits-last.json"
if (Test-Path $testLast) { Remove-Item $testLast }

$python = Join-Path $repo ".venv\Scripts\python.exe"
$mockOut = "$repo\mock-upstream.log"
$mockErr = "$repo\mock-upstream.err"
$proxyOut = "$repo\proxy.log"
$proxyErr = "$repo\proxy.err"

Write-Host "Starting mock upstream on :9999 ..." -ForegroundColor Cyan
$mock = Start-Process -FilePath $python -ArgumentList "-m","uvicorn","mock_upstream:app","--host","127.0.0.1","--port","9999" -WorkingDirectory $repo -PassThru -WindowStyle Hidden -RedirectStandardOutput $mockOut -RedirectStandardError $mockErr

Write-Host "Starting EcoLogits proxy on :8787 ..." -ForegroundColor Cyan
$proxyEnv = @{
    UPSTREAM_BASE_URL = "http://127.0.0.1:9999"
    ECOLOGITS_ZONE = "WOR"
    ECOLOGITS_FALLBACK_PROVIDER = "openai"
    ECOLOGITS_FALLBACK_MODEL = "gpt-4o-mini"
}
$proxy = Start-Process -FilePath $python -ArgumentList "-m","uvicorn","proxy:app","--host","127.0.0.1","--port","8787" -WorkingDirectory $repo -PassThru -WindowStyle Hidden -Environment $proxyEnv -RedirectStandardOutput $proxyOut -RedirectStandardError $proxyErr

try {
    Write-Host "Waiting for servers to start..." -ForegroundColor Cyan
    Start-Sleep -Seconds 4

    Write-Host "`n>>> Testing /v1/models" -ForegroundColor Green
    try {
        Invoke-RestMethod -Uri http://127.0.0.1:8787/v1/models -Method GET | ConvertTo-Json -Depth 3
    } catch {
        Write-Host "models request failed: $_" -ForegroundColor Red
    }

    Write-Host "`n>>> Testing non-streaming chat completion" -ForegroundColor Green
    try {
        Invoke-RestMethod -Uri http://127.0.0.1:8787/v1/chat/completions -Method POST -ContentType "application/json" -Body '{"model":"glm-5.3-flash","messages":[{"role":"user","content":"hello"}]}' | ConvertTo-Json -Depth 3
    } catch {
        Write-Host "non-streaming request failed: $_" -ForegroundColor Red
    }

    Start-Sleep -Seconds 2

    Write-Host "`n>>> Testing streaming chat completion" -ForegroundColor Green
    try {
        curl.exe -s -X POST http://127.0.0.1:8787/v1/chat/completions -H "Content-Type: application/json" -d '{"model":"openai/gpt-4o-mini","messages":[{"role":"user","content":"hello"}],"stream":true}'
    } catch {
        Write-Host "streaming request failed: $_" -ForegroundColor Red
    }

    Start-Sleep -Seconds 2

    Write-Host "`n>>> Testing streaming chat completion without upstream finish_reason" -ForegroundColor Green
    try {
        curl.exe -s -X POST http://127.0.0.1:8787/v1/chat/completions -H "Content-Type: application/json" -H "x-test-no-finish: 1" -d '{"model":"openai/gpt-4o-mini","messages":[{"role":"user","content":"hello"}],"stream":true}'
    } catch {
        Write-Host "no-finish streaming request failed: $_" -ForegroundColor Red
    }

    Start-Sleep -Seconds 2

    Write-Host "`n>>> Last EcoLogits estimate" -ForegroundColor Green
    if (Test-Path $testLast) {
        Get-Content $testLast | ConvertFrom-Json | Select-Object time, model, output_tokens, latency_seconds, impacts | ConvertTo-Json -Depth 4
    } else {
        Write-Host "No estimate file written yet." -ForegroundColor Yellow
    }
} finally {
    Write-Host "`nStopping test servers..." -ForegroundColor Cyan
    Stop-Process -Id $mock.Id -Force -ErrorAction SilentlyContinue
    Stop-Process -Id $proxy.Id -Force -ErrorAction SilentlyContinue

    Write-Host "`nMock stdout:" -ForegroundColor Cyan
    if (Test-Path $mockOut) { Get-Content $mockOut -Tail 20 }
    Write-Host "`nMock stderr:" -ForegroundColor Cyan
    if (Test-Path $mockErr) { Get-Content $mockErr -Tail 20 }
    Write-Host "`nProxy stdout:" -ForegroundColor Cyan
    if (Test-Path $proxyOut) { Get-Content $proxyOut -Tail 40 }
    Write-Host "`nProxy stderr:" -ForegroundColor Cyan
    if (Test-Path $proxyErr) { Get-Content $proxyErr -Tail 40 }
}
