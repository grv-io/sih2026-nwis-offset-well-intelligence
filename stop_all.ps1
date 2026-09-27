# Stop everything run_all.ps1 / run_demo.ps1 started (API on :8000, Vite on :5173).
# Usage (repo root, fresh PowerShell):  powershell -ExecutionPolicy Bypass -File .\stop_all.ps1
$ErrorActionPreference = 'Continue'
$root = $PSScriptRoot
$pidFile = Join-Path $root '.run\pids.txt'

function Stop-Tree([int]$procId) {
    if (Get-Process -Id $procId -ErrorAction SilentlyContinue) {
        & taskkill.exe /PID $procId /T /F 2>$null | Out-Null
    }
}

if (Test-Path $pidFile) {
    Get-Content $pidFile | Where-Object { $_ -match '^\d+$' } | ForEach-Object { Stop-Tree ([int]$_) }
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
}

# Belt and braces: anything still listening on our two ports.
foreach ($port in 8000, 5173) {
    $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    foreach ($c in $conns) { Stop-Tree ([int]$c.OwningProcess) }
}
Write-Host 'NWIS: stopped API (:8000) and web dev server (:5173).'
