param(
    [int]$CheckIntervalSeconds = 5,
    [int]$HeartbeatStaleSeconds = 20,
    [string]$PythonPath = $env:EVM_PYTHON_PATH,
    [switch]$Restart,
    [switch]$NoKubernetesObserver,
    [switch]$NoLifecycleWorker,
    [switch]$Run,
    [switch]$RestartChildren,
    [switch]$Once
)

$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$ArtifactsRoot = if ($env:EVM_HOST_ARTIFACTS_ROOT) {
    $env:EVM_HOST_ARTIFACTS_ROOT
} else {
    "F:\EnterpriseMLOps_Data\enterprise-vision-mlops\artifacts"
}
$RuntimeRoot = Join-Path $ArtifactsRoot "w7\host_runtime"
$SupervisorPidPath = Join-Path $RuntimeRoot "supervisor.pid"
$HeartbeatPath = Join-Path $RuntimeRoot "supervisor.json"
$StdoutPath = Join-Path $RuntimeRoot "supervisor.stdout.log"
$StderrPath = Join-Path $RuntimeRoot "supervisor.stderr.log"
$ObserverRoot = Join-Path $ArtifactsRoot "w7\kubernetes_observer"
$LifecycleRoot = Join-Path $ArtifactsRoot "w7\lifecycle_runs"
$PowerShellPath = (Get-Process -Id $PID).Path

New-Item -ItemType Directory -Force -Path $RuntimeRoot | Out-Null

function Get-OwnedProcess {
    param(
        [int]$ProcessId,
        [string]$CommandMarker
    )

    $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if (-not $process) {
        return $null
    }
    $details = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
    if (-not $details -or $details.CommandLine -notlike "*$CommandMarker*") {
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

function Get-HeartbeatAgeSeconds {
    param(
        [object]$Payload,
        [string]$PropertyName
    )

    if (-not $Payload -or -not $Payload.$PropertyName) {
        return [double]::PositiveInfinity
    }
    try {
        $timestamp = [DateTimeOffset]::Parse([string]$Payload.$PropertyName)
        return [Math]::Max(0, ([DateTimeOffset]::UtcNow - $timestamp).TotalSeconds)
    } catch {
        return [double]::PositiveInfinity
    }
}

function Get-ChildState {
    param(
        [string]$Name,
        [string]$PidPath,
        [string]$HeartbeatFile,
        [string]$HeartbeatProperty,
        [string]$CommandMarker,
        [string]$ExpectedCommit
    )

    $childPid = $null
    $process = $null
    if (Test-Path -LiteralPath $PidPath) {
        $parsedPid = 0
        if ([int]::TryParse((Get-Content -LiteralPath $PidPath -Raw).Trim(), [ref]$parsedPid)) {
            $childPid = $parsedPid
            $process = Get-OwnedProcess -ProcessId $parsedPid -CommandMarker $CommandMarker
        }
    }
    $payload = Read-JsonFile -Path $HeartbeatFile
    $age = Get-HeartbeatAgeSeconds -Payload $payload -PropertyName $HeartbeatProperty
    $revisionMatches = $true
    if ($Name -eq "lifecycle_worker" -and $ExpectedCommit) {
        $revisionMatches = $payload -and $payload.source_commit -eq $ExpectedCommit
    }
    $live = $process -and $age -le $HeartbeatStaleSeconds -and $revisionMatches
    return [ordered]@{
        name = $Name
        status = $(if ($live) { "live" } else { "unhealthy" })
        pid = $childPid
        heartbeat_age_seconds = $(if ([double]::IsPositiveInfinity($age)) { $null } else { [Math]::Round($age, 3) })
        revision_matches = $revisionMatches
    }
}

function Start-ChildRuntime {
    param(
        [string]$ScriptName,
        [string]$Name
    )

    $arguments = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", ('"{0}"' -f (Join-Path $PSScriptRoot $ScriptName)),
        "-Restart"
    )
    if ($PythonPath) {
        $arguments += @("-PythonPath", ('"{0}"' -f $PythonPath))
    }
    $launcher = Start-Process `
        -FilePath $PowerShellPath `
        -ArgumentList $arguments `
        -WorkingDirectory $ProjectRoot `
        -WindowStyle Hidden `
        -PassThru
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds(20)
    do {
        Start-Sleep -Milliseconds 250
        $launcher.Refresh()
    } while (-not $launcher.HasExited -and [DateTimeOffset]::UtcNow -lt $deadline)
    if (-not $launcher.HasExited) {
        Stop-Process -Id $launcher.Id -Force -ErrorAction SilentlyContinue
        throw "$Name launcher did not exit within 20 seconds."
    }
    if ($launcher.ExitCode -ne 0) {
        throw "$Name launcher exited with code $($launcher.ExitCode)."
    }
}

function Write-SupervisorHeartbeat {
    param([System.Collections.IDictionary]$Payload)

    $temporary = "$HeartbeatPath.tmp"
    $Payload | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $temporary -Encoding utf8
    Move-Item -LiteralPath $temporary -Destination $HeartbeatPath -Force
}

$commit = (git -C $ProjectRoot rev-parse HEAD).Trim()
$branch = (git -C $ProjectRoot branch --show-current).Trim()
if (-not $branch) {
    $branch = (git -C $ProjectRoot rev-parse --abbrev-ref HEAD).Trim()
}
$env:EVM_GIT_COMMIT = $commit
$env:EVM_GIT_BRANCH = $branch
$env:EVM_EXPECTED_CI_COMMIT = $commit

if (-not $Run) {
    if (Test-Path -LiteralPath $SupervisorPidPath) {
        $existingPid = 0
        [void][int]::TryParse((Get-Content -LiteralPath $SupervisorPidPath -Raw).Trim(), [ref]$existingPid)
        $existing = if ($existingPid -gt 0) {
            Get-OwnedProcess -ProcessId $existingPid -CommandMarker "start_host_runtime_supervisor.ps1"
        } else {
            $null
        }
        $existingHeartbeat = Read-JsonFile -Path $HeartbeatPath
        $heartbeatAge = Get-HeartbeatAgeSeconds -Payload $existingHeartbeat -PropertyName "last_seen_at"
        $current = $existing -and $heartbeatAge -le $HeartbeatStaleSeconds -and `
            $existingHeartbeat.source_commit -eq $commit
        if ($current -and -not $Restart) {
            Write-Host "Host runtime supervisor already healthy with PID $existingPid at commit $commit."
            exit 0
        }
        if ($existing) {
            Stop-Process -Id $existingPid -Force
            Start-Sleep -Milliseconds 500
        }
        Remove-Item -LiteralPath $SupervisorPidPath -Force -ErrorAction SilentlyContinue
    }

    $arguments = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", ('"{0}"' -f $PSCommandPath),
        "-Run",
        "-CheckIntervalSeconds", [string]$CheckIntervalSeconds,
        "-HeartbeatStaleSeconds", [string]$HeartbeatStaleSeconds
    )
    if ($PythonPath) {
        $arguments += @("-PythonPath", ('"{0}"' -f $PythonPath))
    }
    if ($NoKubernetesObserver) {
        $arguments += "-NoKubernetesObserver"
    }
    if ($NoLifecycleWorker) {
        $arguments += "-NoLifecycleWorker"
    }
    if ($Restart) {
        $arguments += "-RestartChildren"
    }

    $process = Start-Process `
        -FilePath $PowerShellPath `
        -ArgumentList $arguments `
        -WorkingDirectory $ProjectRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $StdoutPath `
        -RedirectStandardError $StderrPath `
        -PassThru
    Set-Content -LiteralPath $SupervisorPidPath -Value $process.Id -Encoding ascii

    $deadline = [DateTimeOffset]::UtcNow.AddSeconds(30)
    do {
        Start-Sleep -Seconds 1
        $heartbeat = Read-JsonFile -Path $HeartbeatPath
        if ($heartbeat -and $heartbeat.supervisor_pid -eq $process.Id -and $heartbeat.status -eq "healthy") {
            Write-Host "Host runtime supervisor PID=$($process.Id) heartbeat=$HeartbeatPath commit=$commit"
            exit 0
        }
        if ($process.HasExited) {
            $stderr = if (Test-Path -LiteralPath $StderrPath) {
                Get-Content -LiteralPath $StderrPath -Raw
            } else {
                "No supervisor stderr was produced."
            }
            throw "Host runtime supervisor exited during startup: $stderr"
        }
    } while ([DateTimeOffset]::UtcNow -lt $deadline)
    throw "Host runtime supervisor did not report healthy within 30 seconds. Inspect $StderrPath."
}

$restartCounts = [ordered]@{
    kubernetes_observer = 0
    lifecycle_worker = 0
}
$forceRestart = $RestartChildren
while ($true) {
    $errors = [System.Collections.Generic.List[string]]::new()

    if (-not $NoKubernetesObserver) {
        $observerState = Get-ChildState `
            -Name "kubernetes_observer" `
            -PidPath (Join-Path $ObserverRoot "observer.pid") `
            -HeartbeatFile (Join-Path $ObserverRoot "latest.json") `
            -HeartbeatProperty "observed_at" `
            -CommandMarker "evm.control_panel.kubernetes_observer" `
            -ExpectedCommit $commit
        if ($forceRestart -or $observerState.status -ne "live") {
            try {
                Start-ChildRuntime -ScriptName "start_kubernetes_observer.ps1" -Name "Kubernetes observer"
                $restartCounts.kubernetes_observer++
            } catch {
                $errors.Add("kubernetes_observer:$($_.Exception.Message)")
            }
            $observerState = Get-ChildState `
                -Name "kubernetes_observer" `
                -PidPath (Join-Path $ObserverRoot "observer.pid") `
                -HeartbeatFile (Join-Path $ObserverRoot "latest.json") `
                -HeartbeatProperty "observed_at" `
                -CommandMarker "evm.control_panel.kubernetes_observer" `
                -ExpectedCommit $commit
        }
    } else {
        $observerState = [ordered]@{ name = "kubernetes_observer"; status = "disabled"; pid = $null }
    }

    if (-not $NoLifecycleWorker) {
        $workerState = Get-ChildState `
            -Name "lifecycle_worker" `
            -PidPath (Join-Path $LifecycleRoot "worker.pid") `
            -HeartbeatFile (Join-Path $LifecycleRoot "_worker.json") `
            -HeartbeatProperty "last_seen_at" `
            -CommandMarker "evm.control_panel.lifecycle_worker" `
            -ExpectedCommit $commit
        if ($forceRestart -or $workerState.status -ne "live") {
            try {
                Start-ChildRuntime -ScriptName "start_lifecycle_worker.ps1" -Name "Lifecycle worker"
                $restartCounts.lifecycle_worker++
            } catch {
                $errors.Add("lifecycle_worker:$($_.Exception.Message)")
            }
            $workerState = Get-ChildState `
                -Name "lifecycle_worker" `
                -PidPath (Join-Path $LifecycleRoot "worker.pid") `
                -HeartbeatFile (Join-Path $LifecycleRoot "_worker.json") `
                -HeartbeatProperty "last_seen_at" `
                -CommandMarker "evm.control_panel.lifecycle_worker" `
                -ExpectedCommit $commit
        }
    } else {
        $workerState = [ordered]@{ name = "lifecycle_worker"; status = "disabled"; pid = $null }
    }

    $forceRestart = $false
    $healthy = $errors.Count -eq 0 -and `
        $observerState.status -in @("live", "disabled") -and `
        $workerState.status -in @("live", "disabled")
    Write-SupervisorHeartbeat -Payload ([ordered]@{
        schema_version = "evm.host_runtime_supervisor.v1"
        status = $(if ($healthy) { "healthy" } else { "degraded" })
        supervisor_pid = $PID
        source_commit = $commit
        source_branch = $branch
        last_seen_at = [DateTimeOffset]::UtcNow.ToString("o")
        check_interval_seconds = $CheckIntervalSeconds
        heartbeat_stale_seconds = $HeartbeatStaleSeconds
        children = @($observerState, $workerState)
        restart_counts = $restartCounts
        errors = @($errors)
    })

    if ($Once) {
        break
    }
    Start-Sleep -Seconds ([Math]::Max(1, $CheckIntervalSeconds))
}
