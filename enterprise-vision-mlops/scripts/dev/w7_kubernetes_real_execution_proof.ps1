param(
  [string]$EvidenceRoot = "F:\EnterpriseMLOps_Data\enterprise-vision-mlops\artifacts\w7\kubernetes_real_execution",
  [string]$KustomizePath = "infra\kubernetes\local",
  [switch]$BuildImages,
  [switch]$AllowBlocked
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $repoRoot

$runId = "evm-k8s-real-proof-{0}" -f (Get-Date -Format "yyyyMMddTHHmmss")
$evidenceDir = Join-Path $EvidenceRoot $runId
New-Item -ItemType Directory -Force -Path $evidenceDir | Out-Null

function Invoke-EvidenceCommand {
  param(
    [string]$Name,
    [scriptblock]$Script,
    [switch]$AllowFailure
  )

  $stdoutPath = Join-Path $evidenceDir "$Name.stdout.txt"
  $stderrPath = Join-Path $evidenceDir "$Name.stderr.txt"
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
  $text = ($output | ForEach-Object { [string]$_ }) -join [Environment]::NewLine
  $text | Set-Content -Encoding UTF8 $stdoutPath
  "" | Set-Content -Encoding UTF8 $stderrPath
  $result = [ordered]@{
    name = $Name
    exit_code = $exitCode
    started_at = $startedAt.ToString("o")
    finished_at = $finishedAt.ToString("o")
    duration_seconds = [math]::Round(($finishedAt - $startedAt).TotalSeconds, 3)
    stdout = $stdoutPath
    stderr = $stderrPath
  }
  if ($exitCode -ne 0 -and -not $AllowFailure) {
    $script:commandResults += $result
    throw "Command failed: $Name exit_code=$exitCode"
  }
  return $result
}

function Write-JsonFile {
  param(
    [string]$Path,
    [object]$Payload
  )
  $Payload | ConvertTo-Json -Depth 12 | Set-Content -Encoding UTF8 $Path
}

$commandResults = @()
$gitHead = (git rev-parse --short HEAD).Trim()
$gitBranch = (git branch --show-current).Trim()

if ($BuildImages) {
  $commandResults += Invoke-EvidenceCommand -Name "docker-build-api" -Script {
    docker build -t enterprise-vision-mlops-api:local -f apps/api/Dockerfile .
  }
  $commandResults += Invoke-EvidenceCommand -Name "docker-build-mlflow" -Script {
    docker build -t enterprise-vision-mlops-mlflow:local -f infra/docker/mlflow/Dockerfile .
  }
  $commandResults += Invoke-EvidenceCommand -Name "docker-build-pipeline" -Script {
    docker build -t enterprise-vision-mlops-pipeline:local -f infra/docker/pipeline/Dockerfile .
  }
}

$commandResults += Invoke-EvidenceCommand -Name "docker-desktop-status" -Script {
  docker desktop status
} -AllowFailure
$commandResults += Invoke-EvidenceCommand -Name "docker-desktop-kubernetes-status" -Script {
  docker desktop kubernetes status --format json
} -AllowFailure
$commandResults += Invoke-EvidenceCommand -Name "kubectl-client-version" -Script {
  kubectl version --client=true
} -AllowFailure
$commandResults += Invoke-EvidenceCommand -Name "kubectl-contexts" -Script {
  kubectl config get-contexts
} -AllowFailure
$commandResults += Invoke-EvidenceCommand -Name "kubectl-current-context" -Script {
  kubectl config current-context
} -AllowFailure
$commandResults += Invoke-EvidenceCommand -Name "docker-images" -Script {
  docker images --format "table {{.Repository}}\t{{.Tag}}\t{{.ID}}\t{{.CreatedSince}}"
} -AllowFailure

$renderPath = Join-Path $evidenceDir "kustomize-render.yaml"
kubectl kustomize $KustomizePath > $renderPath
$commandResults += [ordered]@{
  name = "kubectl-kustomize"
  exit_code = 0
  started_at = (Get-Date).ToString("o")
  finished_at = (Get-Date).ToString("o")
  duration_seconds = 0
  stdout = $renderPath
  stderr = ""
}

$kubernetesStatusPath = Join-Path $evidenceDir "docker-desktop-kubernetes-status.stdout.txt"
$kubernetesStatusText = if (Test-Path $kubernetesStatusPath) { Get-Content -Raw $kubernetesStatusPath } else { "" }
$context = ""
try {
  $context = (kubectl config current-context 2>$null).Trim()
} catch {
  $context = ""
}

$blockerReason = ""
if ($kubernetesStatusText -match '"status"\s*:\s*"disabled"') {
  $blockerReason = "Docker Desktop Kubernetes is disabled or stopped."
} elseif ([string]::IsNullOrWhiteSpace($context)) {
  $blockerReason = "kubectl current-context is empty."
}

$artifactChecks = @(
  "F:\EnterpriseMLOps_Data\enterprise-vision-mlops\data\validated\visa\curation\curation_state.json",
  "F:\EnterpriseMLOps_Data\enterprise-vision-mlops\data\validated\visa\curation\curation_manifest.jsonl",
  "F:\EnterpriseMLOps_Data\enterprise-vision-mlops\data\validated\visa\curation\curated_eval_manifest.jsonl",
  "F:\EnterpriseMLOps_Data\enterprise-vision-mlops\artifacts\lakehouse\visa\lakehouse_probe.json",
  "F:\EnterpriseMLOps_Data\enterprise-vision-mlops\artifacts\lakehouse\visa\engine_tradeoff_matrix.json",
  "F:\EnterpriseMLOps_Data\enterprise-vision-mlops\artifacts\lakehouse\visa\lakehouse_recommendation.md"
)

if (-not [string]::IsNullOrWhiteSpace($blockerReason)) {
  $summary = [ordered]@{
    schema_version = "evm.w7.kubernetes_real_execution.v1"
    status = "blocked"
    blocker_reason = $blockerReason
    run_id = $runId
    evidence_dir = $evidenceDir
    git_head = $gitHead
    git_branch = $gitBranch
    kustomize_render = $renderPath
    kubectl_context = $context
    commands = $commandResults
  }
  $summaryPath = Join-Path $evidenceDir "kubernetes_proof_summary.json"
  Write-JsonFile -Path $summaryPath -Payload $summary
  @(
    '# W7 Kubernetes Real Execution Proof Blocker',
    '',
    '- Status: blocked',
    "- Reason: $blockerReason",
    "- Git head: $gitHead",
    "- Git branch: $gitBranch",
    "- Evidence dir: $evidenceDir",
    "- Kustomize render: $renderPath",
    '',
    '## Recovery Command',
    '',
    'After enabling Docker Desktop Kubernetes and confirming kubectl get nodes, rerun:',
    '',
    '```powershell',
    'scripts\dev\w7_kubernetes_real_execution_proof.ps1 -BuildImages',
    '```'
  ) | Set-Content -Encoding UTF8 (Join-Path $evidenceDir "blocker_report.md")
  $summary | ConvertTo-Json -Depth 12
  if ($AllowBlocked) {
    exit 0
  }
  exit 2
}

$commandResults += Invoke-EvidenceCommand -Name "kubectl-get-nodes-before-apply" -Script {
  kubectl get nodes -o wide
}
$commandResults += Invoke-EvidenceCommand -Name "kubectl-apply" -Script {
  kubectl apply -k $KustomizePath
}
$commandResults += Invoke-EvidenceCommand -Name "kubectl-get-all-after-apply" -Script {
  kubectl get pods,jobs,svc,pvc -A -o wide
}
$commandResults += Invoke-EvidenceCommand -Name "kubectl-wait-api" -Script {
  kubectl wait --for=condition=available deployment/evm-api -n evm-platform --timeout=300s
}

foreach ($job in @("evm-domain-pack-check", "evm-curation-workflow", "evm-lakehouse-probe")) {
  $timeout = if ($job -eq "evm-domain-pack-check") { "600s" } else { "900s" }
  $commandResults += Invoke-EvidenceCommand -Name "kubectl-wait-$job" -Script {
    kubectl wait --for=condition=complete "job/$job" -n evm-pipelines --timeout=$timeout
  }
  $commandResults += Invoke-EvidenceCommand -Name "kubectl-logs-$job" -Script {
    kubectl logs -n evm-pipelines "job/$job"
  }
}

$commandResults += Invoke-EvidenceCommand -Name "kubectl-get-all-final" -Script {
  kubectl get pods,jobs,svc,pvc -A -o wide
}

$artifactResults = foreach ($path in $artifactChecks) {
  $item = Get-Item -LiteralPath $path -ErrorAction SilentlyContinue
  [ordered]@{
    path = $path
    exists = [bool]$item
    size_bytes = if ($item) { $item.Length } else { 0 }
    last_write_time = if ($item) { $item.LastWriteTime.ToString("o") } else { $null }
  }
}

$missingArtifacts = @($artifactResults | Where-Object { -not $_.exists })
$status = if ($missingArtifacts.Count -eq 0) { "pass" } else { "blocked" }

$summary = [ordered]@{
  schema_version = "evm.w7.kubernetes_real_execution.v1"
  status = $status
  blocker_reason = if ($missingArtifacts.Count -eq 0) { "" } else { "One or more expected F-drive artifacts were not produced." }
  run_id = $runId
  evidence_dir = $evidenceDir
  git_head = $gitHead
  git_branch = $gitBranch
  kustomize_render = $renderPath
  kubectl_context = $context
  commands = $commandResults
  artifact_checks = $artifactResults
}

$summaryPath = Join-Path $evidenceDir "kubernetes_proof_summary.json"
Write-JsonFile -Path $summaryPath -Payload $summary
$summary | ConvertTo-Json -Depth 12

if ($status -ne "pass") {
  exit 3
}
