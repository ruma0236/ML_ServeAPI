param(
    [int]$PollIntervalSeconds = 3,
    [string]$PythonPath = $env:EVM_TORCH_PYTHON_PATH,
    [switch]$Restart
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$DataRoot = if ($env:EVM_HOST_DATA_ROOT) { $env:EVM_HOST_DATA_ROOT } else { "F:\EnterpriseMLOps_Data\enterprise-vision-mlops" }
$ArtifactsRoot = Join-Path $DataRoot "artifacts"
$WorkloadRoot = Join-Path $ArtifactsRoot "scenario_workloads"
$PidPath = Join-Path $WorkloadRoot "_worker.pid"
$HeartbeatPath = Join-Path $WorkloadRoot "_worker.json"
$StdoutPath = Join-Path $WorkloadRoot "_worker.stdout.log"
$StderrPath = Join-Path $WorkloadRoot "_worker.stderr.log"
New-Item -ItemType Directory -Force -Path $WorkloadRoot | Out-Null

function Get-OwnedWorkerProcess {
    param([int]$ProcessId)
    $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if (-not $process) { return $null }
    $details = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
    if (-not $details -or $details.CommandLine -notlike "*evm.control_panel.scenario_workload_worker*") { return $null }
    return $process
}

$existingPid = 0
$existing = $null
if (Test-Path -LiteralPath $PidPath) {
    [void][int]::TryParse((Get-Content -LiteralPath $PidPath -Raw).Trim(), [ref]$existingPid)
    $existing = if ($existingPid -gt 0) { Get-OwnedWorkerProcess -ProcessId $existingPid } else { $null }
}
$matchingWorkers = @(
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*evm.control_panel.scenario_workload_worker*" }
)
if ($matchingWorkers.Count -gt 1) {
    throw "Scenario workload worker startup blocked because multiple matching processes exist."
}
if ($matchingWorkers.Count -eq 1 -and (-not $existing -or $matchingWorkers[0].ProcessId -ne $existingPid)) {
    throw "Scenario workload worker startup blocked because the live process is not owned by the recorded PID."
}
if ($existing -and -not $Restart) {
    Write-Host "Scenario workload worker already running with PID $existingPid"
    exit 0
}
if (Test-Path -LiteralPath $PidPath) {
    if ($existing) {
        Stop-Process -Id $existingPid -Force
        Start-Sleep -Milliseconds 500
    } elseif ($existingPid -gt 0 -and (Get-Process -Id $existingPid -ErrorAction SilentlyContinue)) {
        throw "Scenario workload worker restart blocked because PID $existingPid belongs to an unknown process."
    }
    Remove-Item -LiteralPath $PidPath -Force -ErrorAction SilentlyContinue
}

$python = if ($PythonPath) { $PythonPath } else { "F:\evm_w7_torch\python.exe" }
if (-not (Test-Path -LiteralPath $python)) { throw "Torch Python runtime is missing: $python" }
& $python -c "import torch, transformers, peft, mlflow, requests" 2>$null
if ($LASTEXITCODE -ne 0) { throw "Torch Python runtime is missing scenario workload dependencies." }

$env:PYTHONPATH = "$ProjectRoot\src;$ProjectRoot"
$env:EVM_PROJECT_ROOT = $ProjectRoot
$env:EVM_HOST_DATA_ROOT = $DataRoot
$env:EVM_DATA_MOUNT_ROOT = "/mnt/evm-data"
$env:EVM_SCENARIO_WORKLOAD_ROOT = $WorkloadRoot
$env:EVM_SCENARIO_WORKLOAD_CANONICAL_ROOT = $WorkloadRoot
$env:EVM_SCENARIO_WORKLOAD_PRESETS = Join-Path $ProjectRoot "configs\scenario_workloads\live-presets.json"
$env:EVM_SCENARIO_WORKLOAD_CI_EVIDENCE_PATH = Join-Path $WorkloadRoot "_production\local-ci-evidence.json"
$env:EVM_SCENARIO_GPU_LEASE_ROOT = Join-Path $DataRoot "runtime\gpu-lease"
$env:EVM_CONTROL_PANEL_LEDGER_ROOT = Join-Path $ArtifactsRoot "w7\operations"
$env:EVM_AIRFLOW_API_URL = "http://127.0.0.1:8080/api/v1"
$env:EVM_AIRFLOW_API_USERNAME = if ($env:AIRFLOW_ADMIN_USERNAME) { $env:AIRFLOW_ADMIN_USERNAME } else { "admin" }
$env:EVM_AIRFLOW_API_PASSWORD = if ($env:AIRFLOW_ADMIN_PASSWORD) { $env:AIRFLOW_ADMIN_PASSWORD } else { "admin" }
$env:MLFLOW_TRACKING_URI = "http://127.0.0.1:5000"
$env:EVM_PROMETHEUS_URL = "http://127.0.0.1:9090"
$env:EVM_GIT_COMMIT = (git -C $ProjectRoot rev-parse HEAD).Trim()
$env:EVM_GIT_BRANCH = (git -C $ProjectRoot branch --show-current).Trim()
if (-not $env:EVM_GIT_BRANCH) { $env:EVM_GIT_BRANCH = (git -C $ProjectRoot rev-parse --abbrev-ref HEAD).Trim() }

$previousWrite = if (Test-Path -LiteralPath $HeartbeatPath) { (Get-Item -LiteralPath $HeartbeatPath).LastWriteTimeUtc } else { [DateTime]::MinValue }
$process = Start-Process `
    -FilePath $python `
    -ArgumentList @("-m", "evm.control_panel.scenario_workload_worker", "--poll-interval", [string]$PollIntervalSeconds) `
    -WorkingDirectory $ProjectRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $StdoutPath `
    -RedirectStandardError $StderrPath `
    -PassThru
Set-Content -LiteralPath $PidPath -Value $process.Id -Encoding ascii

$deadline = [DateTimeOffset]::UtcNow.AddSeconds(45)
do {
    Start-Sleep -Milliseconds 500
    if ($process.HasExited) {
        $stderr = if (Test-Path -LiteralPath $StderrPath) { Get-Content -LiteralPath $StderrPath -Raw } else { "No stderr." }
        throw "Scenario workload worker exited during startup: $stderr"
    }
    $fresh = (Test-Path -LiteralPath $HeartbeatPath) -and (Get-Item -LiteralPath $HeartbeatPath).LastWriteTimeUtc -gt $previousWrite
    if ($fresh) {
        $heartbeat = Get-Content -LiteralPath $HeartbeatPath -Raw | ConvertFrom-Json
        $fresh = $heartbeat.pid -eq $process.Id -and $heartbeat.source_commit -eq $env:EVM_GIT_COMMIT
    }
} while (-not $fresh -and [DateTimeOffset]::UtcNow -lt $deadline)
if (-not $fresh) {
    Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
    throw "Scenario workload worker did not produce a revision-matched heartbeat within 45 seconds."
}
Write-Host "Scenario workload worker PID=$($process.Id) heartbeat=$HeartbeatPath"
