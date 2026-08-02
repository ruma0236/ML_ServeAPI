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
$IdentityPath = Join-Path $LifecycleRoot "worker.identity.json"
$StdoutPath = Join-Path $LifecycleRoot "worker.stdout.log"
$StderrPath = Join-Path $LifecycleRoot "worker.stderr.log"

New-Item -ItemType Directory -Force -Path $LifecycleRoot | Out-Null
$PrometheusTargetRoot = Join-Path $ArtifactsRoot "w7\prometheus-targets"
$PrometheusTargetFile = Join-Path $PrometheusTargetRoot "lifecycle-serving.json"
New-Item -ItemType Directory -Force -Path $PrometheusTargetRoot | Out-Null
if (-not (Test-Path -LiteralPath $PrometheusTargetFile)) {
    Set-Content -LiteralPath $PrometheusTargetFile -Value "[]" -Encoding ascii
}

function Get-OwnedWorkerProcess {
    param([int]$ProcessId)

    $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if (-not $process) {
        return $null
    }
    $details = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
    if (-not $details -or $details.CommandLine -notlike "*evm.control_panel.lifecycle_worker*") {
        return $null
    }
    return $process
}

function Read-JsonFile {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        return $null
    }
    try {
        return Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
    } catch {
        return $null
    }
}

if (Test-Path -LiteralPath $PidPath) {
    $existingPid = 0
    [void][int]::TryParse((Get-Content -LiteralPath $PidPath -Raw).Trim(), [ref]$existingPid)
    $existing = if ($existingPid -gt 0) { Get-OwnedWorkerProcess -ProcessId $existingPid } else { $null }
    if ($existing -and -not $Restart) {
        Write-Host "Lifecycle worker already running with PID $existingPid"
        exit 0
    }
    if ($existing) {
        $identity = Read-JsonFile -Path $IdentityPath
        if (-not $identity -or $identity.pid -ne $existingPid -or `
            $identity.child_name -ne "lifecycle_worker") {
            throw "Lifecycle worker restart blocked because exact process identity is missing or mismatched."
        }
        Stop-Process -Id $existingPid -Force
        Start-Sleep -Milliseconds 500
    }
    Remove-Item -LiteralPath $PidPath -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $IdentityPath -Force -ErrorAction SilentlyContinue
}

$pythonCandidates = [System.Collections.Generic.List[string]]::new()
foreach ($candidate in @(
    $PythonPath,
    $(if ($env:CONDA_PREFIX) { Join-Path $env:CONDA_PREFIX "python.exe" }),
    $(if ($env:USERPROFILE) { Join-Path $env:USERPROFILE "miniconda3\python.exe" }),
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
$env:EVM_LIFECYCLE_CLAIM_ROOT = Join-Path $LifecycleRoot "_claims"
$env:EVM_LIFECYCLE_CLAIM_TTL_SECONDS = "30"
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
if (-not $env:EVM_LIFECYCLE_SINGLE_GPU_HANDOFF_ENABLED) {
    $env:EVM_LIFECYCLE_SINGLE_GPU_HANDOFF_ENABLED = "true"
}
if (-not $env:EVM_LIFECYCLE_GPU_HOLDERS) {
    $env:EVM_LIFECYCLE_GPU_HOLDERS = "evm-production/evm-b0-production"
}
$env:EVM_GIT_COMMIT = (git -C $ProjectRoot rev-parse HEAD).Trim()
$env:EVM_GIT_BRANCH = (git -C $ProjectRoot branch --show-current).Trim()
if (-not $env:EVM_GIT_BRANCH) {
    $env:EVM_GIT_BRANCH = (git -C $ProjectRoot rev-parse --abbrev-ref HEAD).Trim()
}
$env:EVM_EXPECTED_CI_COMMIT = $env:EVM_GIT_COMMIT
if (-not $env:EVM_LIFECYCLE_GUARD_REQUIRE_RUNTIME_MATCH) {
    $env:EVM_LIFECYCLE_GUARD_REQUIRE_RUNTIME_MATCH = "true"
}
if (-not $env:EVM_SUPERVISOR_LEASE_ID) {
    $env:EVM_SUPERVISOR_LEASE_ID = "standalone-$([Guid]::NewGuid().ToString('N'))"
}
if (-not $env:EVM_SUPERVISOR_FENCING_TOKEN) {
    $env:EVM_SUPERVISOR_FENCING_TOKEN = "1"
}
$env:EVM_PROCESS_INSTANCE_ID = [Guid]::NewGuid().ToString("N")

$heartbeat = Join-Path $LifecycleRoot "_worker.json"
$previousHeartbeatWrite = if (Test-Path -LiteralPath $heartbeat) {
    (Get-Item -LiteralPath $heartbeat).LastWriteTimeUtc
} else {
    [DateTime]::MinValue
}

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
$deadline = [DateTimeOffset]::UtcNow.AddSeconds(15)
do {
    Start-Sleep -Milliseconds 500
    if ($process.HasExited) {
        $stderr = if (Test-Path -LiteralPath $StderrPath) {
            Get-Content -LiteralPath $StderrPath -Raw
        } else {
            "No lifecycle worker stderr was produced."
        }
        throw "Lifecycle worker exited during startup: $stderr"
    }
    $heartbeatFresh = (Test-Path -LiteralPath $heartbeat) -and `
        (Get-Item -LiteralPath $heartbeat).LastWriteTimeUtc -gt $previousHeartbeatWrite
    if ($heartbeatFresh) {
        $payload = Get-Content -LiteralPath $heartbeat -Raw | ConvertFrom-Json
        $heartbeatFresh = $payload.pid -eq $process.Id -and `
            $payload.source_commit -eq $env:EVM_GIT_COMMIT -and `
            $payload.process_instance_id -eq $env:EVM_PROCESS_INSTANCE_ID -and `
            $payload.supervisor_lease_id -eq $env:EVM_SUPERVISOR_LEASE_ID -and `
            $payload.fencing_token -eq [int]$env:EVM_SUPERVISOR_FENCING_TOKEN -and `
            -not [string]::IsNullOrWhiteSpace([string]$payload.started_at)
    }
} while (-not $heartbeatFresh -and [DateTimeOffset]::UtcNow -lt $deadline)
if (-not $heartbeatFresh) {
    Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
    throw "Lifecycle worker did not produce a revision-matched heartbeat within 15 seconds: $heartbeat"
}
$identity = [ordered]@{
    child_name = "lifecycle_worker"
    pid = $process.Id
    process_started_at = $payload.started_at
    process_instance_id = $payload.process_instance_id
    source_commit = $payload.source_commit
    supervisor_lease_id = $payload.supervisor_lease_id
    fencing_token = $payload.fencing_token
}
$identityTemporary = "$IdentityPath.tmp"
$identity | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $identityTemporary -Encoding utf8
Move-Item -LiteralPath $identityTemporary -Destination $IdentityPath -Force
Write-Host "Lifecycle worker PID=$($process.Id) heartbeat=$heartbeat"
