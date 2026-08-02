param(
    [string]$BaseProfileId = "standard-b0-manual-tuning",
    [int]$BaseProfileVersion = 9,
    [string]$PythonPath = $env:EVM_PYTHON_PATH,
    [string]$OutputRoot = "F:\EnterpriseMLOps_Data\enterprise-vision-mlops\artifacts\operations\lifecycle_guard_b"
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
        & $candidate -c "import evm, pydantic, torch" 2>$null
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
    throw "Integrated Scenario B requires a clean repository worktree."
}

& $ResolvedPython -m evm.operations.lifecycle_guard_b_runner `
    --project-root $ProjectRoot `
    --output-root $OutputRoot `
    --base-profile-id $BaseProfileId `
    --base-profile-version $BaseProfileVersion `
    --quality-config (Join-Path $ProjectRoot "configs\operations\lifecycle_guard_b_quality.toml") `
    --runtime-config (Join-Path $ProjectRoot "configs\operations\lifecycle_guard_b_runtime.toml") `
    --source-commit $sourceCommit `
    --source-branch $sourceBranch
if ($LASTEXITCODE -ne 0) {
    throw "Integrated Scenario B lifecycle proof failed."
}
