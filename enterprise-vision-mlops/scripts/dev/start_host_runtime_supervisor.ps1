param(
    [int]$CheckIntervalSeconds = 3,
    [int]$HeartbeatStaleSeconds = 20,
    [string]$PythonPath = $env:EVM_PYTHON_PATH,
    [string]$PolicyPath = "",
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
$LeasePath = Join-Path $RuntimeRoot "supervisor.lease.json"
$StatePath = Join-Path $RuntimeRoot "supervisor-state.json"
$AuditPath = Join-Path $RuntimeRoot "supervisor-audit.jsonl"
$LedgerPath = Join-Path $RuntimeRoot "supervisor-restart-ledger.json"
$ObservationRoot = Join-Path $RuntimeRoot "observations"
$DecisionRoot = Join-Path $RuntimeRoot "decisions"
$StdoutPath = Join-Path $RuntimeRoot "supervisor.stdout.log"
$StderrPath = Join-Path $RuntimeRoot "supervisor.stderr.log"
$ObserverRoot = Join-Path $ArtifactsRoot "w7\kubernetes_observer"
$LifecycleRoot = Join-Path $ArtifactsRoot "w7\lifecycle_runs"
$PowerShellPath = (Get-Process -Id $PID).Path
$ResolvedPolicyPath = if ($PolicyPath) {
    (Resolve-Path -LiteralPath $PolicyPath).Path
} else {
    Join-Path $ProjectRoot "configs\operations\scenario_d_supervision.toml"
}

New-Item -ItemType Directory -Force -Path $RuntimeRoot, $ObservationRoot, $DecisionRoot | Out-Null

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

function Write-AtomicJson {
    param(
        [string]$Path,
        [System.Collections.IDictionary]$Payload
    )

    $temporary = "$Path.$PID.tmp"
    $Payload | ConvertTo-Json -Depth 15 | Set-Content -LiteralPath $temporary -Encoding utf8
    Move-Item -LiteralPath $temporary -Destination $Path -Force
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

function Get-MarkerProcesses {
    param([string]$CommandMarker)

    return @(
        Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
            Where-Object {
                $_.ProcessId -ne $PID -and $_.CommandLine -like "*$CommandMarker*"
            }
    )
}

function Resolve-PythonRuntime {
    $candidates = [System.Collections.Generic.List[string]]::new()
    foreach ($candidate in @(
        $PythonPath,
        $(if ($env:CONDA_PREFIX) { Join-Path $env:CONDA_PREFIX "python.exe" }),
        $(if ($env:USERPROFILE) { Join-Path $env:USERPROFILE "miniconda3\python.exe" }),
        "C:\Users\opop0\miniconda3\python.exe",
        $(if (Get-Command python -ErrorAction SilentlyContinue) {
            (Get-Command python -ErrorAction Stop).Source
        })
    )) {
        if ($candidate -and -not $candidates.Contains($candidate)) {
            $candidates.Add($candidate)
        }
    }
    foreach ($candidate in $candidates) {
        if (-not (Test-Path -LiteralPath $candidate)) {
            continue
        }
        & $candidate -c "import pydantic" 2>$null
        if ($LASTEXITCODE -eq 0) {
            return $candidate
        }
    }
    throw "No Python runtime with the project dependencies was found. Set EVM_PYTHON_PATH."
}

function Stop-LegacyChildForUpgrade {
    param(
        [string]$Name,
        [string]$PidPath,
        [string]$IdentityPath,
        [string]$CommandMarker
    )

    if (-not (Test-Path -LiteralPath $PidPath)) {
        return
    }
    $childPid = 0
    if (-not [int]::TryParse((Get-Content -LiteralPath $PidPath -Raw).Trim(), [ref]$childPid)) {
        throw "$Name upgrade blocked because its PID file is malformed."
    }
    $all = Get-MarkerProcesses -CommandMarker $CommandMarker
    $target = @($all | Where-Object { $_.ProcessId -eq $childPid })
    if ($all.Count -gt 1) {
        throw "$Name upgrade blocked because multiple command-matched processes exist."
    }
    if ($target.Count -eq 0) {
        if (Get-Process -Id $childPid -ErrorAction SilentlyContinue) {
            throw "$Name upgrade blocked because PID $childPid belongs to an unknown process."
        }
    } else {
        Stop-Process -Id $childPid -Force
        Start-Sleep -Milliseconds 500
    }
    Remove-Item -LiteralPath $PidPath -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $IdentityPath -Force -ErrorAction SilentlyContinue
}

function New-SupervisorLease {
    param(
        [string]$Commit,
        [string]$Branch
    )

    $previous = Read-JsonFile -Path $LeasePath
    $fencingToken = if ($previous -and $previous.fencing_token) {
        [int]$previous.fencing_token + 1
    } else {
        1
    }
    $startedAt = (Get-Process -Id $PID).StartTime.ToUniversalTime().ToString("o")
    $now = [DateTimeOffset]::UtcNow.ToString("o")
    return [ordered]@{
        schema_version = "evm.host_runtime_supervisor_lease.v1"
        supervisor_pid = $PID
        supervisor_started_at = $startedAt
        source_commit = $Commit
        source_branch = $Branch
        lease_id = [Guid]::NewGuid().ToString("N")
        fencing_token = $fencingToken
        created_at = $now
        last_seen_at = $now
    }
}

function Convert-HeartbeatIdentity {
    param(
        [string]$Name,
        [object]$Payload,
        [string]$HeartbeatProperty
    )

    if (-not $Payload) {
        return $null
    }
    $processStartedAt = if ($Name -eq "lifecycle_worker") {
        $Payload.started_at
    } else {
        $Payload.process_started_at
    }
    if (-not $Payload.pid -or -not $processStartedAt -or `
        -not $Payload.process_instance_id -or -not $Payload.source_commit -or `
        -not $Payload.supervisor_lease_id -or -not $Payload.fencing_token -or `
        -not $Payload.$HeartbeatProperty) {
        return $null
    }
    return [ordered]@{
        child_name = $Name
        pid = [int]$Payload.pid
        process_started_at = [string]$processStartedAt
        process_instance_id = [string]$Payload.process_instance_id
        source_commit = [string]$Payload.source_commit
        supervisor_lease_id = [string]$Payload.supervisor_lease_id
        fencing_token = [int]$Payload.fencing_token
        observed_at = [string]$Payload.$HeartbeatProperty
    }
}

function New-ChildObservation {
    param(
        [string]$Name,
        [string]$PidPath,
        [string]$IdentityPath,
        [string]$HeartbeatFile,
        [string]$HeartbeatProperty,
        [string]$CommandMarker,
        [string]$ExpectedCommit,
        [object]$Lease
    )

    $childPid = $null
    if (Test-Path -LiteralPath $PidPath) {
        $parsedPid = 0
        if ([int]::TryParse((Get-Content -LiteralPath $PidPath -Raw).Trim(), [ref]$parsedPid)) {
            $childPid = $parsedPid
        }
    }
    $pidFileProcessExists = $false
    if ($childPid) {
        $pidFileProcessExists = $null -ne (Get-Process -Id $childPid -ErrorAction SilentlyContinue)
    }
    $processes = [System.Collections.Generic.List[object]]::new()
    foreach ($item in (Get-MarkerProcesses -CommandMarker $CommandMarker)) {
        $process = Get-Process -Id $item.ProcessId -ErrorAction SilentlyContinue
        if (-not $process) {
            continue
        }
        $processes.Add([ordered]@{
            pid = [int]$item.ProcessId
            process_started_at = $process.StartTime.ToUniversalTime().ToString("o")
            command_matches = $true
            executable = [string]$item.ExecutablePath
            command_line = [string]$item.CommandLine
        })
    }
    $heartbeat = Read-JsonFile -Path $HeartbeatFile
    return [ordered]@{
        schema_version = "evm.scenario_d_child_observation.v1"
        child_name = $Name
        observed_at = [DateTimeOffset]::UtcNow.ToString("o")
        expected_source_commit = $ExpectedCommit
        expected_lease_id = [string]$Lease.lease_id
        expected_fencing_token = [int]$Lease.fencing_token
        pid_file_pid = $childPid
        pid_file_process_exists = $pidFileProcessExists
        identity = Read-JsonFile -Path $IdentityPath
        heartbeat = Convert-HeartbeatIdentity `
            -Name $Name `
            -Payload $heartbeat `
            -HeartbeatProperty $HeartbeatProperty
        processes = @($processes)
    }
}

function Invoke-ChildDecision {
    param(
        [string]$Name,
        [System.Collections.IDictionary]$Observation
    )

    $observationPath = Join-Path $ObservationRoot "$Name.json"
    $decisionPath = Join-Path $DecisionRoot "$Name.json"
    Write-AtomicJson -Path $observationPath -Payload $Observation
    $output = & $EnginePython -m evm.operations.scenario_d_supervision evaluate `
        --policy $ResolvedPolicyPath `
        --observation $observationPath `
        --state $StatePath `
        --ledger $LedgerPath `
        --audit $AuditPath `
        --output $decisionPath 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Scenario D evaluator failed for ${Name}: $($output -join [Environment]::NewLine)"
    }
    $decision = Read-JsonFile -Path $decisionPath
    if (-not $decision) {
        throw "Scenario D evaluator did not produce a valid decision for $Name."
    }
    return $decision
}

function Complete-RestartAttempt {
    param(
        [string]$IncidentFingerprint,
        [string]$Result,
        [string]$Message = ""
    )

    $arguments = @(
        "-m", "evm.operations.scenario_d_supervision", "complete-restart",
        "--ledger", $LedgerPath,
        "--incident-fingerprint", $IncidentFingerprint,
        "--result", $Result
    )
    if ($Message) {
        $arguments += @("--message", $Message)
    }
    $output = & $EnginePython @arguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Could not complete restart ledger entry: $($output -join [Environment]::NewLine)"
    }
}

function Assert-AndStopExactTarget {
    param(
        [string]$Name,
        [int]$TargetPid,
        [string]$IdentityPath,
        [string]$CommandMarker
    )

    $identity = Read-JsonFile -Path $IdentityPath
    if (-not $identity -or $identity.pid -ne $TargetPid -or $identity.child_name -ne $Name) {
        throw "$Name exact restart blocked by identity mismatch for PID $TargetPid."
    }
    $matching = Get-MarkerProcesses -CommandMarker $CommandMarker
    if ($matching.Count -ne 1 -or $matching[0].ProcessId -ne $TargetPid) {
        throw "$Name exact restart blocked by zero or multiple matching processes."
    }
    $process = Get-OwnedProcess -ProcessId $TargetPid -CommandMarker $CommandMarker
    if (-not $process) {
        throw "$Name exact restart blocked because PID $TargetPid is not owned."
    }
    $expectedStart = [DateTimeOffset]::Parse([string]$identity.process_started_at)
    $actualStart = [DateTimeOffset]$process.StartTime.ToUniversalTime()
    if ([Math]::Abs(($actualStart - $expectedStart).TotalMilliseconds) -gt 1.0) {
        throw "$Name exact restart blocked by process-start identity mismatch."
    }
    Stop-Process -Id $TargetPid -Force
    $process.WaitForExit(5000)
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
        "-Restart",
        "-PythonPath", ('"{0}"' -f $EnginePython)
    )
    $launcher = Start-Process `
        -FilePath $PowerShellPath `
        -ArgumentList $arguments `
        -WorkingDirectory $ProjectRoot `
        -WindowStyle Hidden `
        -PassThru
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds(25)
    do {
        Start-Sleep -Milliseconds 250
        $launcher.Refresh()
    } while (-not $launcher.HasExited -and [DateTimeOffset]::UtcNow -lt $deadline)
    if (-not $launcher.HasExited) {
        Stop-Process -Id $launcher.Id -Force -ErrorAction SilentlyContinue
        throw "$Name launcher did not exit within 25 seconds."
    }
    if ($launcher.ExitCode -ne 0) {
        throw "$Name launcher exited with code $($launcher.ExitCode)."
    }
}

function Invoke-RestartDecision {
    param(
        [string]$Name,
        [object]$Decision,
        [string]$ScriptName,
        [string]$IdentityPath,
        [string]$CommandMarker
    )

    if ($Decision.action -ne "restart_exact") {
        return
    }
    try {
        if ($Decision.target_pid) {
            Assert-AndStopExactTarget `
                -Name $Name `
                -TargetPid ([int]$Decision.target_pid) `
                -IdentityPath $IdentityPath `
                -CommandMarker $CommandMarker
        } elseif ((Get-MarkerProcesses -CommandMarker $CommandMarker).Count -ne 0) {
            throw "$Name missing-process restart blocked because a matching process appeared."
        }
        Start-ChildRuntime -ScriptName $ScriptName -Name $Name
        Complete-RestartAttempt `
            -IncidentFingerprint $Decision.incident_fingerprint `
            -Result "succeeded"
    } catch {
        try {
            Complete-RestartAttempt `
                -IncidentFingerprint $Decision.incident_fingerprint `
                -Result "failed" `
                -Message $_.Exception.Message
        } catch {
        }
        throw
    }
}

function Convert-DecisionToChildState {
    param(
        [string]$Name,
        [object]$Decision,
        [object]$Heartbeat
    )

    return [ordered]@{
        name = $Name
        status = [string]$Decision.state
        reason = [string]$Decision.reason
        pid = $Decision.target_pid
        process_count = [int]$Decision.process_count
        exact_identity = [bool]$Decision.exact_identity
        heartbeat_age_seconds = $Decision.heartbeat_age_seconds
        revision_matches = [bool]$Decision.revision_matches
        lease_matches = [bool]$Decision.lease_matches
        fencing_matches = [bool]$Decision.fencing_matches
        source_commit = $(if ($Heartbeat) { $Heartbeat.source_commit } else { $null })
        process_instance_id = $(if ($Heartbeat) { $Heartbeat.process_instance_id } else { $null })
        incident_fingerprint = [string]$Decision.incident_fingerprint
    }
}

$commit = (git -C $ProjectRoot rev-parse HEAD).Trim()
$branch = (git -C $ProjectRoot branch --show-current).Trim()
if (-not $branch) {
    $branch = (git -C $ProjectRoot rev-parse --abbrev-ref HEAD).Trim()
}
$env:PYTHONPATH = "$ProjectRoot\src;$ProjectRoot"
$env:EVM_GIT_COMMIT = $commit
$env:EVM_GIT_BRANCH = $branch
$env:EVM_EXPECTED_CI_COMMIT = $commit
if (-not $env:EVM_LIFECYCLE_GUARD_REQUIRE_RUNTIME_MATCH) {
    $env:EVM_LIFECYCLE_GUARD_REQUIRE_RUNTIME_MATCH = "true"
}
$EnginePython = Resolve-PythonRuntime

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
        } elseif ($existingPid -gt 0 -and (Get-Process -Id $existingPid -ErrorAction SilentlyContinue)) {
            throw "Supervisor restart blocked because PID $existingPid belongs to an unknown process."
        }
        Remove-Item -LiteralPath $SupervisorPidPath -Force -ErrorAction SilentlyContinue
    }

    if ($Restart) {
        Stop-LegacyChildForUpgrade `
            -Name "kubernetes_observer" `
            -PidPath (Join-Path $ObserverRoot "observer.pid") `
            -IdentityPath (Join-Path $ObserverRoot "observer.identity.json") `
            -CommandMarker "evm.control_panel.kubernetes_observer"
        Stop-LegacyChildForUpgrade `
            -Name "lifecycle_worker" `
            -PidPath (Join-Path $LifecycleRoot "worker.pid") `
            -IdentityPath (Join-Path $LifecycleRoot "worker.identity.json") `
            -CommandMarker "evm.control_panel.lifecycle_worker"
    }

    $arguments = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", ('"{0}"' -f $PSCommandPath),
        "-Run",
        "-CheckIntervalSeconds", [string]$CheckIntervalSeconds,
        "-HeartbeatStaleSeconds", [string]$HeartbeatStaleSeconds,
        "-PythonPath", ('"{0}"' -f $EnginePython),
        "-PolicyPath", ('"{0}"' -f $ResolvedPolicyPath)
    )
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

    $deadline = [DateTimeOffset]::UtcNow.AddSeconds(45)
    do {
        Start-Sleep -Seconds 1
        $heartbeat = Read-JsonFile -Path $HeartbeatPath
        if ($heartbeat -and $heartbeat.supervisor_pid -eq $process.Id -and `
            $heartbeat.status -eq "healthy" -and $heartbeat.source_commit -eq $commit) {
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
    throw "Host runtime supervisor did not report healthy within 45 seconds. Inspect $StderrPath."
}

$lease = New-SupervisorLease -Commit $commit -Branch $branch
Write-AtomicJson -Path $LeasePath -Payload $lease
$env:EVM_SUPERVISOR_LEASE_ID = [string]$lease.lease_id
$env:EVM_SUPERVISOR_FENCING_TOKEN = [string]$lease.fencing_token

while ($true) {
    $errors = [System.Collections.Generic.List[string]]::new()
    $decisions = [ordered]@{}
    $observations = [ordered]@{}

    foreach ($definition in @(
        [ordered]@{
            Name = "kubernetes_observer"
            Disabled = [bool]$NoKubernetesObserver
            PidPath = Join-Path $ObserverRoot "observer.pid"
            IdentityPath = Join-Path $ObserverRoot "observer.identity.json"
            HeartbeatFile = Join-Path $ObserverRoot "latest.json"
            HeartbeatProperty = "observed_at"
            CommandMarker = "evm.control_panel.kubernetes_observer"
            ScriptName = "start_kubernetes_observer.ps1"
        },
        [ordered]@{
            Name = "lifecycle_worker"
            Disabled = [bool]$NoLifecycleWorker
            PidPath = Join-Path $LifecycleRoot "worker.pid"
            IdentityPath = Join-Path $LifecycleRoot "worker.identity.json"
            HeartbeatFile = Join-Path $LifecycleRoot "_worker.json"
            HeartbeatProperty = "last_seen_at"
            CommandMarker = "evm.control_panel.lifecycle_worker"
            ScriptName = "start_lifecycle_worker.ps1"
        }
    )) {
        if ($definition.Disabled) {
            $decisions[$definition.Name] = [ordered]@{
                state = "disabled"
                reason = "disabled_by_configuration"
                action = "none"
            }
            continue
        }
        try {
            $observation = New-ChildObservation `
                -Name $definition.Name `
                -PidPath $definition.PidPath `
                -IdentityPath $definition.IdentityPath `
                -HeartbeatFile $definition.HeartbeatFile `
                -HeartbeatProperty $definition.HeartbeatProperty `
                -CommandMarker $definition.CommandMarker `
                -ExpectedCommit $commit `
                -Lease $lease
            $observations[$definition.Name] = $observation
            $decision = Invoke-ChildDecision -Name $definition.Name -Observation $observation
            if ($decision.action -eq "restart_exact") {
                Invoke-RestartDecision `
                    -Name $definition.Name `
                    -Decision $decision `
                    -ScriptName $definition.ScriptName `
                    -IdentityPath $definition.IdentityPath `
                    -CommandMarker $definition.CommandMarker
                $observation = New-ChildObservation `
                    -Name $definition.Name `
                    -PidPath $definition.PidPath `
                    -IdentityPath $definition.IdentityPath `
                    -HeartbeatFile $definition.HeartbeatFile `
                    -HeartbeatProperty $definition.HeartbeatProperty `
                    -CommandMarker $definition.CommandMarker `
                    -ExpectedCommit $commit `
                    -Lease $lease
                $observations[$definition.Name] = $observation
                $decision = Invoke-ChildDecision -Name $definition.Name -Observation $observation
            }
            $decisions[$definition.Name] = $decision
        } catch {
            $errors.Add("$($definition.Name):$($_.Exception.Message)")
            $decisions[$definition.Name] = [ordered]@{
                state = "blocked"
                reason = "supervisor_evaluation_error"
                action = "none"
                target_pid = $null
                process_count = 0
                exact_identity = $false
                heartbeat_age_seconds = $null
                revision_matches = $false
                lease_matches = $false
                fencing_matches = $false
                incident_fingerprint = "evaluation-error"
            }
        }
    }

    $children = [System.Collections.Generic.List[object]]::new()
    foreach ($name in @("kubernetes_observer", "lifecycle_worker")) {
        $decision = $decisions[$name]
        if ($decision.state -eq "disabled") {
            $children.Add([ordered]@{ name = $name; status = "disabled"; reason = $decision.reason })
        } else {
            $children.Add(
                (Convert-DecisionToChildState `
                    -Name $name `
                    -Decision $decision `
                    -Heartbeat $observations[$name].heartbeat)
            )
        }
    }
    $healthy = $errors.Count -eq 0 -and @(
        $children | Where-Object { $_.status -notin @("live", "disabled") }
    ).Count -eq 0
    $ledger = Read-JsonFile -Path $LedgerPath
    $restartCounts = [ordered]@{ kubernetes_observer = 0; lifecycle_worker = 0 }
    if ($ledger) {
        foreach ($attempt in @($ledger.attempts)) {
            if ($restartCounts.Contains($attempt.child_name)) {
                $restartCounts[$attempt.child_name]++
            }
        }
    }
    $lease.last_seen_at = [DateTimeOffset]::UtcNow.ToString("o")
    Write-AtomicJson -Path $LeasePath -Payload $lease
    Write-AtomicJson -Path $HeartbeatPath -Payload ([ordered]@{
        schema_version = "evm.host_runtime_supervisor.v1"
        supervision_contract_version = "evm.scenario_d_supervision.v1"
        status = $(if ($healthy) { "healthy" } else { "degraded" })
        supervisor_pid = $PID
        supervisor_started_at = $lease.supervisor_started_at
        source_commit = $commit
        source_branch = $branch
        lease_id = $lease.lease_id
        fencing_token = $lease.fencing_token
        last_seen_at = [DateTimeOffset]::UtcNow.ToString("o")
        check_interval_seconds = $CheckIntervalSeconds
        heartbeat_stale_seconds = $HeartbeatStaleSeconds
        children = @($children)
        restart_counts = $restartCounts
        restart_ledger_path = $LedgerPath
        audit_path = $AuditPath
        errors = @($errors)
    })

    if ($Once) {
        break
    }
    Start-Sleep -Seconds ([Math]::Max(1, $CheckIntervalSeconds))
}
