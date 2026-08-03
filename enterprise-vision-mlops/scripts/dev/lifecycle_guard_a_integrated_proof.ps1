param(
    [string]$PythonPath = $env:EVM_PYTHON_PATH,
    [string]$Approver = "local-maintenance-owner",
    [switch]$MaintenanceApproved
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$env:PYTHONPATH = Join-Path $ProjectRoot "src"

$candidates = @(
    $PythonPath,
    "F:\evm_w7_torch\python.exe",
    $(if ($env:USERPROFILE) { Join-Path $env:USERPROFILE "miniconda3\python.exe" })
) | Where-Object { $_ } | Select-Object -Unique

$resolved = $null
foreach ($candidate in $candidates) {
    if (-not (Test-Path -LiteralPath $candidate)) { continue }
    $previous = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        & $candidate -c "import evm, pydantic" 2>$null
        if ($LASTEXITCODE -eq 0) { $resolved = (Resolve-Path -LiteralPath $candidate).Path; break }
    }
    finally { $ErrorActionPreference = $previous }
}
if (-not $resolved) { throw "No project Python runtime is available." }
if (-not $MaintenanceApproved) { throw "Scenario A requires -MaintenanceApproved." }

& $resolved -m evm.operations.lifecycle_guard_a_runner `
    --config (Join-Path $ProjectRoot "configs\operations\lifecycle_guard_a_integration.toml") `
    --project-root $ProjectRoot `
    --approver $Approver `
    --maintenance-approved
if ($LASTEXITCODE -ne 0) { throw "Integrated Scenario A failed." }
