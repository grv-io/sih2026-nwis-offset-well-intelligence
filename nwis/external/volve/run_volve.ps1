<#
.SYNOPSIS
    Phase 8 end-to-end: download Volve data -> build wells/surveys -> prepare DDR docs
    -> ingest via the (unmodified) nwis.ingest pipeline -> eval against weak labels.

.DESCRIPTION
    Sequence (research/07 "Volve DDR access recipe" Path A; docs/IMPLEMENTATION_PLAN.md
    Phase 8):
        1. python -m nwis.external.volve.download
        2. python -m nwis.external.volve.wells        (writes data/volve.sqlite)
        3. python -m nwis.external.volve.prepare_docs  (writes data/external/volve/docs/)
        4. python -m nwis.external.volve.labels        (writes weak_truth.json)
        5. python -m nwis.ingest.run data/external/volve/docs --limit 40
               with $env:NWIS_DB = "data/volve.sqlite" so nwis.config points the
               (unmodified) ingestion pipeline at the SEPARATE Volve db, never at
               data/nwis.sqlite.
        6. python -m nwis.ingest.eval --truth data/external/volve/weak_truth.json
               --out models/volve_metrics.json

    Ollama was busy running the synthetic-corpus ingestion (~1h) when this was written
    (26 Sep 2026), so step 5 is capped at --limit 40 docs and a wall-clock budget
    (-MaxIngestMinutes, default 40): if ingestion is still running past that budget,
    this script stops it, prints how many documents had completed, and still runs
    step 6 (eval) against whatever events made it into data/volve.sqlite before the
    stop -- an honest partial result beats no result.

.PARAMETER Force
    Re-download even if data/external/volve/ddr.parquet + sodir/*.csv already exist.

.PARAMETER IngestLimit
    Max documents for the ingest step (default 40 -- see Ollama-busy note above).

.PARAMETER MaxIngestMinutes
    Wall-clock budget for the ingest step before it is stopped early (default 40).
#>
param(
    [switch]$Force,
    [int]$IngestLimit = 40,
    [int]$MaxIngestMinutes = 40
)

$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..\..\..")
Set-Location $root

$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    throw "venv python not found at $py -- bare 'python' is broken on this machine, see IMPLEMENTATION_PLAN.md ground rules."
}

function Step($name) {
    Write-Host ""
    Write-Host "=== $name ===" -ForegroundColor Cyan
}

# 1. Download -----------------------------------------------------------------
Step "1/6 Download (HuggingFace DDR parquet + Sodir FactPages CSVs)"
$downloadArgs = @("-m", "nwis.external.volve.download")
if ($Force) { $downloadArgs += "--force" }
& $py @downloadArgs
if ($LASTEXITCODE -ne 0) { throw "download.py failed (exit $LASTEXITCODE) -- see BLOCKED note it printed." }

# 2. Wells + surveys -> data/volve.sqlite --------------------------------------
Step "2/6 Wells + surveys -> data/volve.sqlite"
& $py -m nwis.external.volve.wells
if ($LASTEXITCODE -ne 0) { throw "wells.py failed (exit $LASTEXITCODE)" }

# 3. Prepare DDR .txt docs ------------------------------------------------------
Step "3/6 Prepare DDR .txt docs"
& $py -m nwis.external.volve.prepare_docs
if ($LASTEXITCODE -ne 0) { throw "prepare_docs.py failed (exit $LASTEXITCODE)" }

# 4. Weak labels ----------------------------------------------------------------
Step "4/6 Weak (regex) labels -> weak_truth.json"
& $py -m nwis.external.volve.labels
if ($LASTEXITCODE -ne 0) { throw "labels.py failed (exit $LASTEXITCODE)" }

# 5. Ingest via the unmodified nwis.ingest pipeline, capped at $IngestLimit docs
#    and $MaxIngestMinutes wall clock (Ollama was busy on the synthetic corpus).
Step "5/6 Ingest (--limit $IngestLimit, budget ${MaxIngestMinutes}min, NWIS_DB=data/volve.sqlite)"
$env:NWIS_DB = "data/volve.sqlite"

$ingestLog = "data/external/volve/ingest_run.log"
$psi = Start-Process -FilePath $py `
    -ArgumentList @("-m", "nwis.ingest.run", "data/external/volve/docs", "--limit", "$IngestLimit") `
    -NoNewWindow -PassThru `
    -RedirectStandardOutput $ingestLog -RedirectStandardError "$ingestLog.err"

$deadline = (Get-Date).AddMinutes($MaxIngestMinutes)
while (-not $psi.HasExited -and (Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 15
}

if (-not $psi.HasExited) {
    Write-Host "Ingest exceeded ${MaxIngestMinutes}min budget -- stopping it now (Ollama was busy; see run header)." -ForegroundColor Yellow
    Stop-Process -Id $psi.Id -Force
    Start-Sleep -Seconds 2
}

if (Test-Path $ingestLog) {
    Write-Host "--- ingest output (tail) ---"
    Get-Content $ingestLog -Tail 20
    $nDone = (Select-String -Path $ingestLog -Pattern "^\[\d+/\d+\]" -AllMatches).Matches.Count
    Write-Host "Documents that reached a per-file log line before stop/finish: $nDone"
}

# 6. Eval against weak labels ---------------------------------------------------
Step "6/6 Eval against weak_truth.json -> models/volve_metrics.json"
& $py -m nwis.ingest.eval --truth data/external/volve/weak_truth.json --out models/volve_metrics.json
if ($LASTEXITCODE -ne 0) {
    Write-Host "eval.py failed (exit $LASTEXITCODE) -- check data/volve.sqlite has events (ingest may have produced 0)." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "Done. Metrics: models/volve_metrics.json" -ForegroundColor Green
Write-Host "Remember: models/volve_metrics.json is evaluated against WEAK/REGEX labels (see nwis/external/volve/labels.py docstring for limitations), not human-adjudicated ground truth like data/synthetic/events_truth.json."
