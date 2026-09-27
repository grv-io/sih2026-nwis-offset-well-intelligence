# Demo mode: build the SPA once, then ONE uvicorn process serves UI + API on :8000.
# Usage (repo root, fresh PowerShell):  powershell -ExecutionPolicy Bypass -File .\run_demo.ps1 [-SkipBuild] [-NoBrowser]
# Stop with:                            powershell -ExecutionPolicy Bypass -File .\stop_all.ps1
param([switch]$SkipBuild, [switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$py = Join-Path $root '.venv\Scripts\python.exe'
$web = Join-Path $root 'web'
$run = Join-Path $root '.run'

if (-not (Test-Path $py)) { throw "Python venv not found at $py" }
& (Join-Path $root 'stop_all.ps1') | Out-Null
New-Item -ItemType Directory -Force $run | Out-Null

if (-not $SkipBuild) {
    if (-not (Get-Command node -ErrorAction SilentlyContinue)) { throw 'node not found on PATH (Node 20+ required).' }
    Push-Location $web
    try {
        if (-not (Test-Path 'node_modules')) {
            Write-Host 'NWIS: installing web dependencies (first run)...'
            & cmd.exe /c 'npm install --no-audit --no-fund'
            if ($LASTEXITCODE -ne 0) { throw 'npm install failed' }
        }
        Write-Host 'NWIS: building web UI (npm run build)...'
        & cmd.exe /c 'npm run build'
        if ($LASTEXITCODE -ne 0) { throw 'npm run build failed' }
    } finally { Pop-Location }
}
if (-not (Test-Path (Join-Path $web 'dist\index.html'))) { throw 'web\dist\index.html missing; run without -SkipBuild' }

Write-Host 'NWIS: starting single-process server on http://127.0.0.1:8000 ...'
$api = Start-Process -FilePath $py -WorkingDirectory $root -NoNewWindow -PassThru `
    -ArgumentList '-m', 'uvicorn', 'api.main:app', '--host', '127.0.0.1', '--port', '8000' `
    -RedirectStandardOutput (Join-Path $run 'api.log') -RedirectStandardError (Join-Path $run 'api.err.log')
Set-Content -Path (Join-Path $run 'pids.txt') -Value @($api.Id)

$ok = $false
for ($i = 0; $i -lt 90; $i++) {
    try {
        $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 8 -Uri 'http://127.0.0.1:8000/health'
        if ($r.StatusCode -eq 200) { $ok = $true; break }
    } catch { Start-Sleep -Seconds 1 }
}
if (-not $ok) { throw "Server did not answer /health; see $run\api.err.log" }

Write-Host ''
Write-Host 'NWIS demo is up (one process, UI + API):'
Write-Host '  Office  http://127.0.0.1:8000/office'
Write-Host '  Rig     http://127.0.0.1:8000/rig'
Write-Host "  Logs    $run"
if (-not $NoBrowser) { Start-Process 'http://127.0.0.1:8000/office' }
