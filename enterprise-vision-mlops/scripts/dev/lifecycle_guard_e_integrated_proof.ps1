param(
    [string]$BaseProfileId = "standard-b0-manual-tuning",
    [int]$BaseProfileVersion = 9,
    [string]$PythonPath = $env:EVM_PYTHON_PATH,
    [string]$OutputRoot = "F:\EnterpriseMLOps_Data\enterprise-vision-mlops\artifacts\operations\lifecycle_guard_e_integrated"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$env:PYTHONPATH = Join-Path $ProjectRoot "src"

function Resolve-ProjectPython {
    $candidates = @(
        $PythonPath,
        "F:\evm_w7_torch\python.exe",
        $(if ($env:CONDA_PREFIX) { Join-Path $env:CONDA_PREFIX "python.exe" }),
        $(if ($env:USERPROFILE) { Join-Path $env:USERPROFILE "miniconda3\python.exe" }),
        "C:\Users\opop0\miniconda3\python.exe"
    ) | Where-Object { $_ } | Select-Object -Unique
    foreach ($candidate in $candidates) {
        if (-not (Test-Path -LiteralPath $candidate)) {
            continue
        }
        $previousPreference = $ErrorActionPreference
        $probeExitCode = 1
        try {
            $ErrorActionPreference = "Continue"
            & $candidate -c "import evm, pydantic, torch" 2>$null
            $probeExitCode = $LASTEXITCODE
        }
        finally {
            $ErrorActionPreference = $previousPreference
        }
        if ($probeExitCode -eq 0) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    throw "No Python runtime with evm, pydantic, and torch is available."
}

$ResolvedPython = Resolve-ProjectPython
$sourceCommit = (git -C $ProjectRoot rev-parse HEAD).Trim()
$sourceBranch = (git -C $ProjectRoot branch --show-current).Trim()
$dirty = [bool](git -C $ProjectRoot status --porcelain -- .)
if ($dirty) {
    throw "Integrated Scenario E requires a clean repository worktree."
}

& $ResolvedPython -m evm.operations.lifecycle_guard_e_integrated_runner `
    --project-root $ProjectRoot `
    --output-root $OutputRoot `
    --base-profile-id $BaseProfileId `
    --base-profile-version $BaseProfileVersion `
    --source-commit $sourceCommit `
    --source-branch $sourceBranch
if ($LASTEXITCODE -ne 0) {
    throw "Integrated Scenario E lifecycle proof failed."
}
