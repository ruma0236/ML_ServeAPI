param(
  [string]$ConfigPath = "configs/local_visa.toml",
  [string]$EvidenceRoot = "F:\EnterpriseMLOps_Data\enterprise-vision-mlops\artifacts\w7\lifecycle_replay",
  [string]$PythonPath = "C:\Users\opop0\miniconda3\python.exe"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $PythonPath)) {
  $PythonPath = "python"
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $repoRoot

$env:PYTHONPATH = (Resolve-Path "src").Path
$env:EVM_GIT_COMMIT = (git rev-parse --short HEAD)
$env:EVM_GIT_BRANCH = (git branch --show-current)

$runId = "evm-lifecycle-replay-{0}" -f (Get-Date -Format "yyyyMMddTHHmmss")
$evidenceDir = Join-Path $EvidenceRoot $runId
New-Item -ItemType Directory -Force -Path $evidenceDir | Out-Null

$pipelines = @(
  "domain-pack-check",
  "dataset-intake-audit",
  "data-validate",
  "image-quality",
  "dataset-shards",
  "curation-workflow",
  "lakehouse-probe",
  "train",
  "register-model",
  "model-lifecycle"
)

$results = @()
foreach ($pipeline in $pipelines) {
  $startedAt = Get-Date
  $stdoutPath = Join-Path $evidenceDir "$pipeline.stdout.json"
  $stderrPath = Join-Path $evidenceDir "$pipeline.stderr.txt"
  & $PythonPath scripts\run_pipeline.py $pipeline --config $ConfigPath 1> $stdoutPath 2> $stderrPath
  $exitCode = $LASTEXITCODE
  $finishedAt = Get-Date
  $results += [ordered]@{
    pipeline = $pipeline
    exit_code = $exitCode
    started_at = $startedAt.ToString("o")
    finished_at = $finishedAt.ToString("o")
    duration_seconds = [math]::Round(($finishedAt - $startedAt).TotalSeconds, 3)
    stdout = $stdoutPath
    stderr = $stderrPath
  }
  if ($exitCode -ne 0) {
    break
  }
}

$cyclePath = Join-Path $evidenceDir "cycle_run_latest.json"
$schemaReportPath = Join-Path $evidenceDir "cycle_run_schema_validation.json"
$cycleScript = @"
from pathlib import Path
from evm.control_panel.aggregation import build_latest_cycle

cycle = build_latest_cycle()
Path(r"$cyclePath").write_text(cycle.model_dump_json(indent=2), encoding="utf-8")
"@
$cycleScript | & $PythonPath -

& $PythonPath -m evm.control_panel.validate_cycle_run `
  --openapi contracts\control-panel\control-panel.openapi.json `
  --component CycleRun `
  --input $cyclePath `
  --report $schemaReportPath

$summary = [ordered]@{
  schema_version = "evm.w7.lifecycle_replay.v1"
  evidence_dir = $evidenceDir
  config = $ConfigPath
  python = $PythonPath
  git_head = $env:EVM_GIT_COMMIT
  git_branch = $env:EVM_GIT_BRANCH
  cycle_run = $cyclePath
  cycle_schema_validation = $schemaReportPath
  results = $results
}

$summaryPath = Join-Path $evidenceDir "lifecycle_replay_summary.json"
$summary | ConvertTo-Json -Depth 6 | Set-Content -Encoding UTF8 $summaryPath
$summary | ConvertTo-Json -Depth 6

if (($results | Where-Object { $_.exit_code -ne 0 }).Count -gt 0) {
  exit 1
}
