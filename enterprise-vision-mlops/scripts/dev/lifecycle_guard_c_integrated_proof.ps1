param(
    [string]$TrainingImage = "enterprise-vision-mlops-efficientnet-training:local",
    [string]$ProfileId = "standard-b0-manual-tuning",
    [int]$ProfileVersion = 9,
    [string]$DataRoot = "F:\EnterpriseMLOps_Data\enterprise-vision-mlops",
    [string]$OutputRoot = "F:\EnterpriseMLOps_Data\enterprise-vision-mlops\artifacts\operations\lifecycle_guard_c"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$sourceCommit = (git -C $ProjectRoot rev-parse HEAD).Trim()
$sourceBranch = (git -C $ProjectRoot branch --show-current).Trim()
$dirty = [bool](git -C $ProjectRoot status --porcelain -- .)
if ($dirty) {
    throw "Integrated Scenario C requires a clean repository worktree."
}

& (Join-Path $PSScriptRoot "scenario_c_quality_proof.ps1") `
    -TrainingImage $TrainingImage `
    -DataRoot $DataRoot
if ($LASTEXITCODE -ne 0) {
    throw "The source-bound Scenario C CUDA proof failed."
}

$latestPath = Join-Path $DataRoot "artifacts\operations\scenario-c\latest-scenario-c.json"
$latest = Get-Content $latestPath -Raw | ConvertFrom-Json
if (-not $latest.run_id) {
    throw "Scenario C latest evidence pointer is invalid."
}
$scenarioRoot = Join-Path $DataRoot "artifacts\operations\scenario-c\$($latest.run_id)"

python -m evm.operations.lifecycle_guard_c_runner `
    --project-root $ProjectRoot `
    --scenario-root $scenarioRoot `
    --output-root $OutputRoot `
    --profile-id $ProfileId `
    --profile-version $ProfileVersion `
    --source-commit $sourceCommit `
    --source-branch $sourceBranch
if ($LASTEXITCODE -ne 0) {
    throw "Integrated Scenario C lifecycle proof failed."
}
