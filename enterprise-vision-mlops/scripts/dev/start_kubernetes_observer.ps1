param(
    [int]$IntervalSeconds = 5,
    [string]$PythonPath = $env:EVM_PYTHON_PATH,
    [string]$Namespaces = $env:EVM_KUBERNETES_NAMESPACES,
    [switch]$Restart
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$ArtifactsRoot = if ($env:EVM_HOST_ARTIFACTS_ROOT) {
    $env:EVM_HOST_ARTIFACTS_ROOT
} else {
    "F:\EnterpriseMLOps_Data\enterprise-vision-mlops\artifacts"
}
$ObserverRoot = Join-Path $ArtifactsRoot "w7\kubernetes_observer"
$OutputPath = Join-Path $ObserverRoot "latest.json"
$HistoryRoot = Join-Path $ObserverRoot "history"
$PidPath = Join-Path $ObserverRoot "observer.pid"
$StdoutPath = Join-Path $ObserverRoot "observer.stdout.log"
$StderrPath = Join-Path $ObserverRoot "observer.stderr.log"

New-Item -ItemType Directory -Force -Path $ObserverRoot | Out-Null

if (Test-Path -LiteralPath $PidPath) {
    $existingPid = [int](Get-Content -LiteralPath $PidPath -Raw).Trim()
    $existing = Get-Process -Id $existingPid -ErrorAction SilentlyContinue
    if ($existing -and -not $Restart) {
        Write-Host "Kubernetes observer already running with PID $existingPid"
        exit 0
    }
    if ($existing) {
        Stop-Process -Id $existingPid -Force
        Start-Sleep -Milliseconds 500
    }
    Remove-Item -LiteralPath $PidPath -Force -ErrorAction SilentlyContinue
}

$pythonCandidates = [System.Collections.Generic.List[string]]::new()
foreach ($candidate in @(
    $PythonPath,
    $(if ($env:CONDA_PREFIX) { Join-Path $env:CONDA_PREFIX "python.exe" }),
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
    & $candidate -c "import pydantic" 2>$null
    if ($LASTEXITCODE -eq 0) {
        $python = $candidate
        break
    }
}
if (-not $python) {
    throw "No Python runtime with the project dependencies was found. Set EVM_PYTHON_PATH."
}
$env:PYTHONPATH = Join-Path $ProjectRoot "src"
$observerNamespaces = if ($Namespaces) {
    $Namespaces
} else {
    "evm-training,evm-staging,evm-production"
}
$arguments = @(
    "-m",
    "evm.control_panel.kubernetes_observer",
    "--output", $OutputPath,
    "--history-root", $HistoryRoot,
    "--interval-seconds", [string]$IntervalSeconds,
    "--cluster-context", "docker-desktop",
    "--namespaces", $observerNamespaces
)

$startArguments = @{
    FilePath = $python
    ArgumentList = $arguments
    WorkingDirectory = $ProjectRoot
    WindowStyle = "Hidden"
    RedirectStandardOutput = $StdoutPath
    RedirectStandardError = $StderrPath
    PassThru = $true
}
$process = Start-Process @startArguments

Set-Content -LiteralPath $PidPath -Value $process.Id -Encoding ascii
Start-Sleep -Seconds 2
if ($process.HasExited) {
    $stderr = if (Test-Path -LiteralPath $StderrPath) {
        Get-Content -LiteralPath $StderrPath -Raw
    } else {
        "No observer stderr was produced."
    }
    throw "Kubernetes observer exited during startup: $stderr"
}
if (-not (Test-Path -LiteralPath $OutputPath)) {
    throw "Kubernetes observer started but did not produce $OutputPath"
}

Write-Host "Kubernetes observer PID=$($process.Id) output=$OutputPath"
