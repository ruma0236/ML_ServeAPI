param(
    [int]$PollIntervalSeconds = 3,
    [string]$PythonPath = $env:EVM_PYTHON_PATH,
    [switch]$Restart
)

$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$DataRoot = if ($env:EVM_HOST_DATA_ROOT) {
    $env:EVM_HOST_DATA_ROOT
} else {
    "F:\EnterpriseMLOps_Data\enterprise-vision-mlops"
}
$ArtifactsRoot = Join-Path $DataRoot "artifacts"
$LifecycleRoot = Join-Path $ArtifactsRoot "w7\lifecycle_runs"
$PidPath = Join-Path $LifecycleRoot "worker.pid"
$StdoutPath = Join-Path $LifecycleRoot "worker.stdout.log"
$StderrPath = Join-Path $LifecycleRoot "worker.stderr.log"

New-Item -ItemType Directory -Force -Path $LifecycleRoot | Out-Null
$PrometheusTargetRoot = Join-Path $ArtifactsRoot "w7\prometheus-targets"
$PrometheusTargetFile = Join-Path $PrometheusTargetRoot "lifecycle-serving.json"
New-Item -ItemType Directory -Force -Path $PrometheusTargetRoot | Out-Null
if (-not (Test-Path -LiteralPath $PrometheusTargetFile)) {
    Set-Content -LiteralPath $PrometheusTargetFile -Value "[]" -Encoding ascii
}

if (Test-Path -LiteralPath $PidPath) {
    $existingPid = [int](Get-Content -LiteralPath $PidPath -Raw).Trim()
    $existing = Get-Process -Id $existingPid -ErrorAction SilentlyContinue
    if ($existing -and -not $Restart) {
        Write-Host "Lifecycle worker already running with PID $existingPid"
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
    & $candidate -c "import pydantic, requests" 2>$null
    if ($LASTEXITCODE -eq 0) {
        $python = $candidate
        break
    }
}
if (-not $python) {
    throw "No Python runtime with the project dependencies was found. Set EVM_PYTHON_PATH."
}

$env:PYTHONPATH = "$ProjectRoot\src;$ProjectRoot"
$env:EVM_PROJECT_ROOT = $ProjectRoot
$env:EVM_HOST_DATA_ROOT = $DataRoot
$env:EVM_DATA_MOUNT_ROOT = "/mnt/evm-data"
$env:EVM_PIPELINE_PROFILE_ROOT = Join-Path $ArtifactsRoot "w7\pipeline_profiles"
$env:EVM_PIPELINE_PROFILE_RUNTIME_ROOT = "/mnt/evm-data/artifacts/w7/pipeline_profiles"
$env:EVM_LIFECYCLE_RUN_ROOT = $LifecycleRoot
$env:EVM_LIFECYCLE_RUNTIME_ROOT = "/mnt/evm-data/artifacts/w7/lifecycle_runs"
$env:EVM_KUBERNETES_GENERATED_MANIFEST_ROOT = $LifecycleRoot
$env:EVM_CONTROL_PANEL_LEDGER_ROOT = Join-Path $ArtifactsRoot "w7\operations"
$env:EVM_DEPLOYMENT_INTENT_ROOT = Join-Path $ArtifactsRoot "w7\deployment_intents"
$env:EVM_CI_EVIDENCE_PATH = Join-Path $ArtifactsRoot "w7\ci\latest_ci_evidence.json"
$env:EVM_CI_VALIDATION_REPORT_PATH = Join-Path $ArtifactsRoot "w7\ci\latest_ci_validation.json"
$env:EVM_AIRFLOW_API_URL = "http://127.0.0.1:8080/api/v1"
$env:EVM_AIRFLOW_API_USERNAME = if ($env:AIRFLOW_ADMIN_USERNAME) { $env:AIRFLOW_ADMIN_USERNAME } else { "admin" }
$env:EVM_AIRFLOW_API_PASSWORD = if ($env:AIRFLOW_ADMIN_PASSWORD) { $env:AIRFLOW_ADMIN_PASSWORD } else { "admin" }
$env:MLFLOW_TRACKING_URI = "http://127.0.0.1:5000"
$env:EVM_PROMETHEUS_URL = "http://127.0.0.1:9090"
$env:EVM_PROMETHEUS_FILE_SD_PATH = Join-Path $ArtifactsRoot "w7\prometheus-targets\lifecycle-serving.json"
$env:EVM_GIT_COMMIT = (git -C $ProjectRoot rev-parse HEAD).Trim()
$env:EVM_GIT_BRANCH = (git -C $ProjectRoot branch --show-current).Trim()

$arguments = @(
    "-m", "evm.control_panel.lifecycle_worker",
    "--poll-interval", [string]$PollIntervalSeconds,
    "--worker-id", "windows-docker-desktop-lifecycle-worker"
)
$process = Start-Process `
    -FilePath $python `
    -ArgumentList $arguments `
    -WorkingDirectory $ProjectRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $StdoutPath `
    -RedirectStandardError $StderrPath `
    -PassThru

Set-Content -LiteralPath $PidPath -Value $process.Id -Encoding ascii
Start-Sleep -Seconds 2
if ($process.HasExited) {
    $stderr = if (Test-Path -LiteralPath $StderrPath) {
        Get-Content -LiteralPath $StderrPath -Raw
    } else {
        "No lifecycle worker stderr was produced."
    }
    throw "Lifecycle worker exited during startup: $stderr"
}

$heartbeat = Join-Path $LifecycleRoot "_worker.json"
if (-not (Test-Path -LiteralPath $heartbeat)) {
    throw "Lifecycle worker started but did not produce $heartbeat"
}
Write-Host "Lifecycle worker PID=$($process.Id) heartbeat=$heartbeat"
