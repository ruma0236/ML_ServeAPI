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
$IdentityPath = Join-Path $ObserverRoot "observer.identity.json"
$StdoutPath = Join-Path $ObserverRoot "observer.stdout.log"
$StderrPath = Join-Path $ObserverRoot "observer.stderr.log"

New-Item -ItemType Directory -Force -Path $ObserverRoot | Out-Null

function Get-OwnedObserverProcess {
    param([int]$ProcessId)

    $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if (-not $process) {
        return $null
    }
    $details = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
    if (-not $details -or $details.CommandLine -notlike "*evm.control_panel.kubernetes_observer*") {
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
    $existing = if ($existingPid -gt 0) { Get-OwnedObserverProcess -ProcessId $existingPid } else { $null }
    if ($existing -and -not $Restart) {
        Write-Host "Kubernetes observer already running with PID $existingPid"
        exit 0
    }
    if ($existing) {
        $identity = Read-JsonFile -Path $IdentityPath
        if (-not $identity -or $identity.pid -ne $existingPid -or `
            $identity.child_name -ne "kubernetes_observer") {
            throw "Kubernetes observer restart blocked because exact process identity is missing or mismatched."
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
$env:EVM_GIT_COMMIT = (git -C $ProjectRoot rev-parse HEAD).Trim()
$env:EVM_GIT_BRANCH = (git -C $ProjectRoot branch --show-current).Trim()
if (-not $env:EVM_GIT_BRANCH) {
    $env:EVM_GIT_BRANCH = (git -C $ProjectRoot rev-parse --abbrev-ref HEAD).Trim()
}
if (-not $env:EVM_SUPERVISOR_LEASE_ID) {
    $env:EVM_SUPERVISOR_LEASE_ID = "standalone-$([Guid]::NewGuid().ToString('N'))"
}
if (-not $env:EVM_SUPERVISOR_FENCING_TOKEN) {
    $env:EVM_SUPERVISOR_FENCING_TOKEN = "1"
}
$env:EVM_PROCESS_INSTANCE_ID = [Guid]::NewGuid().ToString("N")
$previousOutputWrite = if (Test-Path -LiteralPath $OutputPath) {
    (Get-Item -LiteralPath $OutputPath).LastWriteTimeUtc
} else {
    [DateTime]::MinValue
}
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
$deadline = [DateTimeOffset]::UtcNow.AddSeconds(15)
do {
    Start-Sleep -Milliseconds 500
    if ($process.HasExited) {
        $stderr = if (Test-Path -LiteralPath $StderrPath) {
            Get-Content -LiteralPath $StderrPath -Raw
        } else {
            "No observer stderr was produced."
        }
        throw "Kubernetes observer exited during startup: $stderr"
    }
    $outputFresh = (Test-Path -LiteralPath $OutputPath) -and `
        (Get-Item -LiteralPath $OutputPath).LastWriteTimeUtc -gt $previousOutputWrite
    if ($outputFresh) {
        $payload = Get-Content -LiteralPath $OutputPath -Raw | ConvertFrom-Json
        $outputFresh = $payload.pid -eq $process.Id -and `
            $payload.source_commit -eq $env:EVM_GIT_COMMIT -and `
            $payload.process_instance_id -eq $env:EVM_PROCESS_INSTANCE_ID -and `
            $payload.supervisor_lease_id -eq $env:EVM_SUPERVISOR_LEASE_ID -and `
            $payload.fencing_token -eq [int]$env:EVM_SUPERVISOR_FENCING_TOKEN -and `
            -not [string]::IsNullOrWhiteSpace([string]$payload.process_started_at)
    }
} while (-not $outputFresh -and [DateTimeOffset]::UtcNow -lt $deadline)
if (-not $outputFresh) {
    Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
    throw "Kubernetes observer did not produce a fresh snapshot within 15 seconds: $OutputPath"
}
$identity = [ordered]@{
    child_name = "kubernetes_observer"
    pid = $process.Id
    process_started_at = $payload.process_started_at
    process_instance_id = $payload.process_instance_id
    source_commit = $payload.source_commit
    supervisor_lease_id = $payload.supervisor_lease_id
    fencing_token = $payload.fencing_token
}
$identityTemporary = "$IdentityPath.tmp"
$identity | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $identityTemporary -Encoding utf8
Move-Item -LiteralPath $identityTemporary -Destination $IdentityPath -Force

Write-Host "Kubernetes observer PID=$($process.Id) output=$OutputPath"
