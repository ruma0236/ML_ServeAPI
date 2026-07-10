param(
    [string]$SourceCommit,
    [string]$OutputPath,
    [string]$PythonPath = $env:EVM_PYTHON_PATH,
    [string]$CycleUrl = "http://127.0.0.1:8000/control-panel/v1/cycles/latest",
    [string]$ResourcesUrl = "http://127.0.0.1:8000/control-panel/v1/resources",
    [switch]$RequireCloseout
)

$ErrorActionPreference = "Stop"
if (Get-Variable PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
    $PSNativeCommandUseErrorActionPreference = $false
}

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $ProjectRoot

if (-not $SourceCommit) {
    $SourceCommit = (git rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $SourceCommit) {
        throw "Unable to resolve the source commit. Pass -SourceCommit explicitly."
    }
}

$artifactsRoot = if ($env:EVM_HOST_ARTIFACTS_ROOT) {
    $env:EVM_HOST_ARTIFACTS_ROOT
} else {
    "F:\EnterpriseMLOps_Data\enterprise-vision-mlops\artifacts"
}
if (-not $OutputPath) {
    $runId = "evm-228-{0}" -f (Get-Date -Format "yyyyMMddTHHmmss")
    $OutputPath = Join-Path $artifactsRoot "w7\closeout\$runId\w7-closeout-matrix.json"
} elseif (-not [System.IO.Path]::IsPathRooted($OutputPath)) {
    $OutputPath = Join-Path $ProjectRoot $OutputPath
}

$pythonCandidates = [System.Collections.Generic.List[string]]::new()
foreach ($candidate in @(
    $PythonPath,
    $(if ($env:CONDA_PREFIX) { Join-Path $env:CONDA_PREFIX "python.exe" }),
    $(if ($env:USERPROFILE) { Join-Path $env:USERPROFILE "miniconda3\python.exe" }),
    "C:\Users\mlops\miniconda3\python.exe",
    "C:\Users\opop0\miniconda3\python.exe",
    $(if (Get-Command python -ErrorAction SilentlyContinue) {
        (Get-Command python -ErrorAction Stop).Source
    })
)) {
    if ($candidate -and -not $pythonCandidates.Contains($candidate)) {
        $pythonCandidates.Add($candidate)
    }
}

$python = $null
foreach ($candidate in $pythonCandidates) {
    if (-not (Test-Path -LiteralPath $candidate)) {
        continue
    }
    & $candidate -c "import pydantic, requests" 2>$null
    if ($LASTEXITCODE -eq 0) {
        $python = $candidate
        break
    }
}
if (-not $python) {
    throw "No Python runtime with pydantic and requests was found. Set EVM_PYTHON_PATH."
}

$outputDirectory = Split-Path -Parent $OutputPath
New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
$env:PYTHONPATH = Join-Path $ProjectRoot "src"

$arguments = @(
    "-m",
    "evm.control_panel.w7_closeout",
    "--cycle-url", $CycleUrl,
    "--resources-url", $ResourcesUrl,
    "--source-commit", $SourceCommit,
    "--output", $OutputPath
)
if ($RequireCloseout) {
    $arguments += "--require-closeout"
}

Write-Host "Python runtime: $python"
Write-Host "W7 closeout matrix: $OutputPath"
& $python @arguments
$exitCode = $LASTEXITCODE
if ($exitCode -notin @(0, 1)) {
    throw "W7 closeout evaluator failed with exit code $exitCode."
}
exit $exitCode

