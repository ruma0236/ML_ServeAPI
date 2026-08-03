param(
    [string]$PythonPath = $env:EVM_PYTHON_PATH,
    [switch]$AllowBlocked
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$env:PYTHONPATH = Join-Path $ProjectRoot "src"

$candidates = @(
    $PythonPath,
    "C:\Users\opop0\miniconda3\python.exe",
    $(if ($env:CONDA_PREFIX) { Join-Path $env:CONDA_PREFIX "python.exe" })
) | Where-Object { $_ } | Select-Object -Unique
$ResolvedPython = $null
foreach ($candidate in $candidates) {
    if (Test-Path -LiteralPath $candidate) {
        & $candidate -c "import evm, requests" 2>$null
        if ($LASTEXITCODE -eq 0) {
            $ResolvedPython = (Resolve-Path -LiteralPath $candidate).Path
            break
        }
    }
}
if (-not $ResolvedPython) {
    throw "No Python runtime with the project package is available. Set EVM_PYTHON_PATH."
}

$arguments = @(
    "-m", "evm.operations.lifecycle_guard_closure_validator",
    "--config", (Join-Path $ProjectRoot "configs\operations\lifecycle_guard_closure.toml"),
    "--project-root", $ProjectRoot
)
if ($AllowBlocked) {
    $arguments += "--allow-blocked"
}
& $ResolvedPython @arguments
if ($LASTEXITCODE -ne 0) {
    throw "Full lifecycle guard closure is blocked. Read the emitted result evidence."
}

