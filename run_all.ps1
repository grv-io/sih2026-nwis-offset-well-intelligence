# Dev mode: FastAPI (uvicorn --reload) on :8000 + Vite dev server on :5173 (proxies /api -> :8000).
# Usage (repo root, fresh PowerShell):  powershell -ExecutionPolicy Bypass -File .\run_all.ps1
# Stop with:                            powershell -ExecutionPolicy Bypass -File .\stop_all.ps1
param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$py = Join-Path $root '.venv\Scripts\python.exe'
$web = Join-Path $root 'web'
$run = Join-Path $root '.run'

if (-not (Test-Path $py)) { throw "Python venv not found at $py (create it: py -3.13 -m venv .venv; .venv\Scripts\pip install -r requirements.txt)" }
if (-not (Get-Command node -ErrorAction SilentlyContinue)) { throw 'node not found on PATH (Node 20+ required).' }

& (Join-Path $root 'stop_all.ps1') | Out-Null
New-Item -ItemType Directory -Force $run | Out-Null

if (-not (Test-Path (Join-Path $web 'node_modules'))) {
    Write-Host 'NWIS: installing web dependencies (first run)...'
    Push-Location $web
    & cmd.exe /c 'npm install --no-audit --no-fund'
    $code = $LASTEXITCODE
    Pop-Location
    if ($code -ne 0) { throw 'npm install failed' }
}

Write-Host 'NWIS: starting API on http://127.0.0.1:8000 ...'
$api = Start-Process -FilePath $py -WorkingDirectory $root -NoNewWindow -PassThru `
    -ArgumentList '-m', 'uvicorn', 'api.main:app', '--host', '127.0.0.1', '--port', '8000', '--reload', '--reload-dir', 'api', '--reload-dir', 'nwis' `
    -RedirectStandardOutput (Join-Path $run 'api.log') -RedirectStandardError (Join-Path $run 'api.err.log')

Write-Host 'NWIS: starting web dev server on http://localhost:5173 ...'
$vite = Start-Process -FilePath 'cmd.exe' -WorkingDirectory $web -NoNewWindow -PassThru `
    -ArgumentList '/c', 'npm run dev' `
    -RedirectStandardOutput (Join-Path $run 'web.log') -RedirectStandardError (Join-Path $run 'web.err.log')

Set-Content -Path (Join-Path $run 'pids.txt') -Value @($api.Id, $vite.Id)

# Wait for the API (first import of pandas/plotly/sqlmodel takes a few seconds).
$ok = $false
for ($i = 0; $i -lt 90; $i++) {
    try {
        $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 8 -Uri 'http://127.0.0.1:8000/health'
        if ($r.StatusCode -eq 200) { $ok = $true; break }
    } catch { Start-Sleep -Seconds 1 }
}
if (-not $ok) { Write-Warning "API did not answer /health in time; see $run\api.err.log" }

for ($i = 0; $i -lt 60; $i++) {
    try {
        $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 -Uri 'http://localhost:5173/'
        if ($r.StatusCode -eq 200) { break }
    } catch { Start-Sleep -Seconds 1 }
}

Write-Host ''
Write-Host 'NWIS dev is up:'
Write-Host '  Office  http://localhost:5173/office'
Write-Host '  Rig     http://localhost:5173/rig'
Write-Host '  API     http://127.0.0.1:8000/docs'
Write-Host "  Logs    $run"
if (-not $NoBrowser) { Start-Process 'http://localhost:5173/office' }
