param(
    [string]$TrainingImage = "enterprise-vision-mlops-efficientnet-training:local",
    [string]$ProfileId = "standard-b0-manual-tuning",
    [int]$ProfileVersion = 9,
    [string]$PythonPath = $env:EVM_PYTHON_PATH,
    [string]$DataRoot = "F:\EnterpriseMLOps_Data\enterprise-vision-mlops",
    [string]$OutputRoot = "F:\EnterpriseMLOps_Data\enterprise-vision-mlops\artifacts\operations\lifecycle_guard_c"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$env:PYTHONPATH = Join-Path $ProjectRoot "src"

function Resolve-ProjectPython {
    $candidates = @(
        $PythonPath,
        $(if ($env:CONDA_PREFIX) { Join-Path $env:CONDA_PREFIX "python.exe" }),
        $(if ($env:USERPROFILE) { Join-Path $env:USERPROFILE "miniconda3\python.exe" }),
        "C:\Users\opop0\miniconda3\python.exe"
    ) | Where-Object { $_ } | Select-Object -Unique
    foreach ($candidate in $candidates) {
        if (-not (Test-Path -LiteralPath $candidate)) {
            continue
        }
        & $candidate -c "import evm, pydantic" 2>$null
        if ($LASTEXITCODE -eq 0) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    throw "No Python runtime with the project package is available. Set EVM_PYTHON_PATH."
}

$ResolvedPython = Resolve-ProjectPython
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

& $ResolvedPython -m evm.operations.lifecycle_guard_c_runner `
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
