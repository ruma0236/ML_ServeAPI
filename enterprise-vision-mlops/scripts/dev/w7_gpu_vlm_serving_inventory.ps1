param(
  [string]$EvidenceRoot = "F:\EnterpriseMLOps_Data\enterprise-vision-mlops\artifacts\w7\gpu_vlm_serving",
  [string]$ConfigPath = "configs\local.toml",
  [switch]$SkipRemoteJob
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $repoRoot

$runId = "evm-gpu-vlm-serving-{0}" -f (Get-Date -Format "yyyyMMddTHHmmss")
$evidenceDir = Join-Path $EvidenceRoot $runId
New-Item -ItemType Directory -Force -Path $evidenceDir | Out-Null

function Invoke-Capture {
  param(
    [string]$Name,
    [scriptblock]$Script,
    [switch]$AllowFailure
  )

  $stdoutPath = Join-Path $evidenceDir "$Name.stdout.txt"
  $startedAt = Get-Date
  $output = @()
  $exitCode = 0
  try {
    $output = & $Script 2>&1
    $exitCode = $LASTEXITCODE
  } catch {
    $output += $_.Exception.Message
    $exitCode = if ($null -ne $LASTEXITCODE -and $LASTEXITCODE -ne 0) { $LASTEXITCODE } else { 1 }
  }
  if ($null -eq $exitCode) {
    $exitCode = 0
  }
  $finishedAt = Get-Date
  ($output | ForEach-Object { [string]$_ }) -join [Environment]::NewLine |
    Set-Content -Encoding UTF8 $stdoutPath

  $result = [ordered]@{
    name = $Name
    exit_code = $exitCode
    started_at = $startedAt.ToString("o")
    finished_at = $finishedAt.ToString("o")
    duration_seconds = [math]::Round(($finishedAt - $startedAt).TotalSeconds, 3)
    stdout = $stdoutPath
  }
  if ($exitCode -ne 0 -and -not $AllowFailure) {
    $script:commandResults += $result
    throw "Command failed: $Name exit_code=$exitCode"
  }
  return $result
}

function Read-JsonText {
  param([string]$Path)
  if (-not (Test-Path -LiteralPath $Path)) {
    return $null
  }
  try {
    return Get-Content -Raw -LiteralPath $Path | ConvertFrom-Json
  } catch {
    return $null
  }
}

$commandResults = @()
$gitHead = (git rev-parse --short HEAD).Trim()
$gitBranch = (git branch --show-current).Trim()

$torchProbePath = Join-Path $evidenceDir "torch_probe.py"
@'
import json
import platform

info = {"python": platform.python_version(), "platform": platform.platform()}
try:
    import torch

    info.update(
        {
            "torch": torch.__version__,
            "cuda_available": torch.cuda.is_available(),
            "cuda_device_count": torch.cuda.device_count(),
            "cuda_version": torch.version.cuda,
        }
    )
    info["devices"] = [
        {
            "index": i,
            "name": torch.cuda.get_device_name(i),
            "capability": torch.cuda.get_device_capability(i),
            "memory_total": torch.cuda.get_device_properties(i).total_memory,
        }
        for i in range(torch.cuda.device_count())
    ]
except Exception as exc:
    info["torch_error"] = repr(exc)

print(json.dumps(info, indent=2))
'@ | Set-Content -Encoding UTF8 $torchProbePath

$commandResults += Invoke-Capture -Name "nvidia-smi-query" -Script {
  nvidia-smi --query-gpu=name,memory.total,memory.used,memory.free,driver_version --format=csv,noheader
} -AllowFailure
$commandResults += Invoke-Capture -Name "nvidia-smi-full" -Script {
  nvidia-smi
} -AllowFailure
$commandResults += Invoke-Capture -Name "python-torch-probe" -Script {
  python $torchProbePath
} -AllowFailure
$commandResults += Invoke-Capture -Name "docker-compose-ps" -Script {
  docker compose ps --format json
} -AllowFailure
$commandResults += Invoke-Capture -Name "docker-desktop-kubernetes-status" -Script {
  docker desktop kubernetes status --format json
} -AllowFailure
$commandResults += Invoke-Capture -Name "remote-inventory" -Script {
  python scripts\run_pipeline.py remote-inventory --config $ConfigPath
} -AllowFailure

if (-not $SkipRemoteJob) {
  $commandResults += Invoke-Capture -Name "remote-job" -Script {
    python scripts\run_pipeline.py remote-job --config $ConfigPath
  } -AllowFailure
}

$nvidiaOutput = Get-Content -Raw -LiteralPath (Join-Path $evidenceDir "nvidia-smi-query.stdout.txt")
$torchProbe = Read-JsonText -Path (Join-Path $evidenceDir "python-torch-probe.stdout.txt")
$remoteInventory = Read-JsonText -Path (Join-Path $evidenceDir "remote-inventory.stdout.txt")
$remoteJob = if ($SkipRemoteJob) { $null } else { Read-JsonText -Path (Join-Path $evidenceDir "remote-job.stdout.txt") }
$k8sStatus = Read-JsonText -Path (Join-Path $evidenceDir "docker-desktop-kubernetes-status.stdout.txt")

$gpuAvailable = -not [string]::IsNullOrWhiteSpace($nvidiaOutput) -and $nvidiaOutput -notmatch "not recognized|failed|error"
$torchCudaAvailable = [bool]($torchProbe -and $torchProbe.cuda_available)
$macMiniReady = [bool]($remoteInventory -and ($remoteInventory.inventory | Where-Object {
  $_.worker_id -eq "ruma_macmini" -and $_.remote_exec_ready
}))
$remoteJobSuccess = [bool]($remoteJob -and $remoteJob.status -eq "success")
$k8sAvailable = [bool]($k8sStatus -and $k8sStatus.status -eq "running")

$recommendation = [ordered]@{
  near_term = "Use Windows RTX 4080 SUPER as the primary CUDA trainer and GPU serving candidate after installing a pinned Torch/TorchVision runtime."
  mac_mini = "Use ruma-macmini as ARM64 evaluator, artifact verifier, remote CI candidate, and optional MPS/CoreML experiment target."
  kubernetes = "Keep KServe/Triton/Ray Serve deployment as blocked until a current Kubernetes context exists; do not claim in-cluster serving readiness yet."
  serving_runtime = "Prefer Triton for EfficientNet/CV serving, vLLM only for selected VLMs with confirmed support, Ray Serve for Python composition, and KServe for Kubernetes rollout once the cluster is enabled."
}

$blockers = @()
if (-not $gpuAvailable) {
  $blockers += "Windows GPU was not visible through nvidia-smi."
}
if (-not $torchCudaAvailable) {
  $blockers += "Default Python runtime does not expose torch CUDA; install/pin a W7 Torch/TorchVision CUDA runtime before EfficientNet/VLM GPU acceptance."
}
if (-not $macMiniReady) {
  $blockers += "Mac mini remote execution is not ready."
}
if (-not $k8sAvailable) {
  $blockers += "Kubernetes serving proof is blocked because Docker Desktop Kubernetes is not running."
}

$summary = [ordered]@{
  schema_version = "evm.w7.gpu_vlm_serving_inventory.v1"
  status = if ($gpuAvailable -and $macMiniReady) { "design_ready_with_blockers" } else { "blocked" }
  run_id = $runId
  evidence_dir = $evidenceDir
  git_head = $gitHead
  git_branch = $gitBranch
  gpu_available = $gpuAvailable
  torch_cuda_available = $torchCudaAvailable
  mac_mini_remote_exec_ready = $macMiniReady
  remote_job_success = $remoteJobSuccess
  kubernetes_available = $k8sAvailable
  blockers = $blockers
  recommendation = $recommendation
  commands = $commandResults
}

$summaryPath = Join-Path $evidenceDir "gpu_vlm_serving_summary.json"
$summary | ConvertTo-Json -Depth 12 | Set-Content -Encoding UTF8 $summaryPath
$summary | ConvertTo-Json -Depth 12
