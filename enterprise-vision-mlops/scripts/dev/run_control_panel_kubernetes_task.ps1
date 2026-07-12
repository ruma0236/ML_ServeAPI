param(
    [Parameter(Mandatory = $true)][string]$TaskId,
    [string]$PythonExe = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path

if (-not $PythonExe) {
    $CondaPython = "C:\Users\opop0\miniconda3\python.exe"
    $PythonExe = if (Test-Path $CondaPython) { $CondaPython } else { "python" }
}

$env:PYTHONPATH = "$ProjectRoot\src;$ProjectRoot"
$env:EVM_PROJECT_ROOT = $ProjectRoot
$env:EVM_CONTROL_PANEL_LEDGER_ROOT = "F:\EnterpriseMLOps_Data\enterprise-vision-mlops\artifacts\w7\operations"

Push-Location $ProjectRoot
try {
    & $PythonExe -m evm.control_panel.kubernetes_task_executor --task-id $TaskId
    if ($LASTEXITCODE -ne 0) {
        throw "Kubernetes task executor failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}
