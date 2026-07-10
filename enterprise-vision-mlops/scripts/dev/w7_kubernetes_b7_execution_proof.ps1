param(
    [switch]$SkipBuild,
    [switch]$AllowBlocked,
    [int]$TrainingSeed = 20260710,
    [int]$TrainingTimeoutSeconds = 7200,
    [int]$RolloutTimeoutSeconds = 600
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$SourceGitCommit = (git -C $ProjectRoot rev-parse HEAD).Trim()
$EvidenceRoot = "F:\EnterpriseMLOps_Data\enterprise-vision-mlops\artifacts\w7\kubernetes_b7"
$RunId = "w7-k8s-b7-{0}" -f (Get-Date -Format "yyyyMMddTHHmmss")
$EvidenceDir = Join-Path $EvidenceRoot $RunId
$CandidateId = "effnet-b7-img600-finetune-adamw"
$MatrixDir = "F:\EnterpriseMLOps_Data\enterprise-vision-mlops\artifacts\w7\efficientnet\w7-efficientnet-real-test-matrix"
$SourceCandidateDir = Join-Path $MatrixDir $CandidateId
$RollbackRegistryPath = "F:\EnterpriseMLOps_Data\enterprise-vision-mlops\artifacts\registry\efficientnet-b7\rollback.json"
$CandidateDir = Join-Path $MatrixDir "runs\$RunId\$CandidateId"
$SourceModelPath = Join-Path $SourceCandidateDir "model.pt"
$SourceSplitManifestPath = Join-Path $SourceCandidateDir "split_manifest.json"
$SourceMlflowRunId = ""
$ModelPath = Join-Path $CandidateDir "model.pt"
$SplitManifestPath = Join-Path $CandidateDir "split_manifest.json"
$TrainingImage = "enterprise-vision-mlops-efficientnet-training:local"
$ServingImage = "enterprise-vision-mlops-efficientnet-serving:local"
$MlflowArtifactModelUploaded = $false
$Blockers = [System.Collections.Generic.List[string]]::new()

New-Item -ItemType Directory -Force -Path $EvidenceDir | Out-Null

function Invoke-Captured {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$ArgumentList = @(),
        [switch]$IgnoreFailure
    )

    $OutputPath = Join-Path $EvidenceDir "$Name.log"
    $PreviousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $Output = & $FilePath @ArgumentList 2>&1
    $ExitCode = $LASTEXITCODE
    $ErrorActionPreference = $PreviousPreference
    @($Output) | ForEach-Object { "$_" } | Set-Content -Path $OutputPath -Encoding utf8
    if ($ExitCode -ne 0 -and -not $IgnoreFailure) {
        throw "$FilePath exited with code $ExitCode. See $OutputPath"
    }
    return [pscustomobject]@{
        exit_code = $ExitCode
        output = (@($Output) | ForEach-Object { "$_" }) -join "`n"
        log_path = $OutputPath
    }
}

function Wait-KubernetesJobTerminal {
    param(
        [Parameter(Mandatory = $true)][string]$JobName,
        [Parameter(Mandatory = $true)][string]$Namespace,
        [Parameter(Mandatory = $true)][int]$TimeoutSeconds,
        [Parameter(Mandatory = $true)][string]$EvidenceName
    )

    $OutputPath = Join-Path $EvidenceDir "$EvidenceName.log"
    $Deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $Trace = [System.Collections.Generic.List[string]]::new()
    do {
        $Job = & kubectl get "job/$JobName" -n $Namespace -o json 2>&1
        if ($LASTEXITCODE -ne 0) {
            @($Job) | ForEach-Object { "$($_)" } | Set-Content -Path $OutputPath -Encoding utf8
            throw "Unable to read Kubernetes Job $Namespace/$JobName"
        }
        $Payload = $Job | ConvertFrom-Json
        $StatusProperties = $Payload.status.PSObject.Properties.Name
        $Succeeded = if ($StatusProperties -contains "succeeded") { [int]$Payload.status.succeeded } else { 0 }
        $Failed = if ($StatusProperties -contains "failed") { [int]$Payload.status.failed } else { 0 }
        $Active = if ($StatusProperties -contains "active") { [int]$Payload.status.active } else { 0 }
        $Trace.Add("$([DateTime]::UtcNow.ToString('o')) active=$Active succeeded=$Succeeded failed=$Failed")
        if ($Succeeded -ge 1) {
            $Trace | Set-Content -Path $OutputPath -Encoding utf8
            return
        }
        if ($Failed -ge 1) {
            $Trace | Set-Content -Path $OutputPath -Encoding utf8
            throw "Kubernetes Job $Namespace/$JobName failed"
        }
        Start-Sleep -Seconds 5
    } while ((Get-Date) -lt $Deadline)

    $Trace | Set-Content -Path $OutputPath -Encoding utf8
    throw "Kubernetes Job $Namespace/$JobName did not finish within $TimeoutSeconds seconds"
}

function Write-EvidenceIndex {
    param(
        [Parameter(Mandatory = $true)][string]$Status,
        [hashtable]$Additional = @{}
    )

    $Payload = [ordered]@{
        schema_version = "evm.w7.kubernetes_b7_execution.v1"
        run_id = $RunId
        status = $Status
        issue = "EVM-226"
        cluster_context = "docker-desktop"
        candidate_id = $CandidateId
        dataset_version = "visa-open-data-f1f1c9ee9922"
        source_mlflow_run_id = $SourceMlflowRunId
        evidence_root = $EvidenceDir
        blockers = @($Blockers)
        git_commit = $SourceGitCommit
        finalization_git_commit = (git -C $ProjectRoot rev-parse HEAD).Trim()
        training_seed = $TrainingSeed
        created_at = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    }
    foreach ($Key in $Additional.Keys) {
        $Payload[$Key] = $Additional[$Key]
    }
    $Payload | ConvertTo-Json -Depth 12 | Set-Content -Path (Join-Path $EvidenceDir "evidence_index.json") -Encoding utf8
}

function New-RuntimeManifestOverlay {
    param(
        [Parameter(Mandatory = $true)][string]$TrainingImageDigest,
        [Parameter(Mandatory = $true)][string]$ServingImageDigest,
        [Parameter(Mandatory = $true)][string]$WslDriverPath
    )

    if ($TrainingImageDigest -notmatch "@sha256:" -or $ServingImageDigest -notmatch "@sha256:") {
        throw "Training and serving images must have immutable local RepoDigests"
    }
    $RuntimeManifestDir = Join-Path $EvidenceDir "runtime-manifests"
    New-Item -ItemType Directory -Force -Path $RuntimeManifestDir | Out-Null
    Copy-Item "infra/kubernetes/model-runtime/*.yaml" $RuntimeManifestDir -Force
    $WorkloadPatchTemplate = Get-Content `
        "infra/kubernetes/docker-desktop-gpu/model-runtime-workload-patch.yaml.tmpl" -Raw
    $WorkloadPatchTemplate.Replace("__WSL_DRIVER_PATH__", $WslDriverPath) |
        Set-Content (Join-Path $RuntimeManifestDir "docker-desktop-gpu-workload-patch.yaml") -Encoding utf8
    $RuntimeModelPath = "/mnt/evm-data/artifacts/w7/efficientnet/w7-efficientnet-real-test-matrix/runs/$RunId/$CandidateId/model.pt"
    @"
apiVersion: batch/v1
kind: Job
metadata:
  name: evm-b7-training
  namespace: evm-training
spec:
  template:
    spec:
      containers:
        - name: trainer
          env:
            - name: EVM_EFFICIENTNET_RUN_ID
              value: $RunId
            - name: EVM_EFFICIENTNET_SEED
              value: "$TrainingSeed"
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: evm-b7-serving
  namespace: evm-staging
spec:
  template:
    spec:
      containers:
        - name: serving
          env:
            - name: EVM_MODEL_PATH
              value: $RuntimeModelPath
"@ | Set-Content (Join-Path $RuntimeManifestDir "runtime-artifact-patch.yaml") -Encoding utf8
    $TrainingDigest = $TrainingImageDigest.Split("@")[1]
    $ServingDigest = $ServingImageDigest.Split("@")[1]
    @"
images:
  - name: enterprise-vision-mlops-efficientnet-training
    newName: enterprise-vision-mlops-efficientnet-training
    digest: $TrainingDigest
  - name: enterprise-vision-mlops-efficientnet-serving
    newName: enterprise-vision-mlops-efficientnet-serving
    digest: $ServingDigest
patches:
  - path: docker-desktop-gpu-workload-patch.yaml
  - path: runtime-artifact-patch.yaml
"@ | Add-Content (Join-Path $RuntimeManifestDir "kustomization.yaml") -Encoding utf8
    return $RuntimeManifestDir
}

Push-Location $ProjectRoot
try {
    $KubernetesStatus = Invoke-Captured -Name "01-docker-desktop-kubernetes-status" -FilePath "docker" -ArgumentList @("desktop", "kubernetes", "status")
    $ContextResult = Invoke-Captured -Name "02-kubectl-current-context" -FilePath "kubectl" -ArgumentList @("config", "current-context")
    $CurrentContext = $ContextResult.output.Trim()
    if ($CurrentContext -ne "docker-desktop") {
        $Blockers.Add("unexpected_kubernetes_context:$CurrentContext")
    }

    Invoke-Captured -Name "03-kubernetes-node" -FilePath "kubectl" -ArgumentList @("get", "nodes", "-o", "wide") | Out-Null
    Invoke-Captured -Name "04-kubernetes-system-pods" -FilePath "kubectl" -ArgumentList @("get", "pods", "-n", "kube-system", "-o", "wide") | Out-Null
    Invoke-Captured -Name "04-release-serving-gpu" -FilePath "kubectl" -ArgumentList @(
        "scale", "deployment/evm-b7-serving", "-n", "evm-staging", "--replicas=0"
    ) -IgnoreFailure | Out-Null
    Invoke-Captured -Name "05-docker-desktop-gpu-bridge" -FilePath "powershell.exe" -ArgumentList @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", "scripts/dev/configure_docker_desktop_kubernetes_gpu.ps1"
    ) | Out-Null
    Start-Sleep -Seconds 5
    Invoke-Captured -Name "06-nvidia-device-plugin-logs" -FilePath "kubectl" -ArgumentList @(
        "logs", "-n", "kube-system", "daemonset/nvidia-device-plugin-daemonset", "--tail=200"
    ) -IgnoreFailure | Out-Null

    $GpuResult = Invoke-Captured -Name "07-node-gpu-allocatable" -FilePath "kubectl" -ArgumentList @(
        "get", "node", "docker-desktop", "-o", "jsonpath={.status.allocatable.nvidia\.com/gpu}"
    ) -IgnoreFailure
    $GpuAllocatable = $GpuResult.output.Trim()
    if (-not $GpuAllocatable -or [int]$GpuAllocatable -lt 1) {
        $Blockers.Add("docker_desktop_kubernetes_gpu_not_advertised")
    }
    $WslDriverPath = (& wsl.exe -d docker-desktop -u root -- sh -lc `
        "find /usr/lib/wsl/drivers -name nvidia-smi -type f | head -n 1 | xargs dirname").Trim()
    if (-not $WslDriverPath -or -not $WslDriverPath.StartsWith("/usr/lib/wsl/drivers/")) {
        $Blockers.Add("docker_desktop_wsl_driver_path_missing")
    }

    $DockerGpuProof = Invoke-Captured -Name "08-docker-gpu-vector-add" -FilePath "docker" -ArgumentList @(
        "run", "--rm", "--gpus", "all", "nvcr.io/nvidia/k8s/cuda-sample:vectoradd-cuda12.5.0"
    ) -IgnoreFailure
    if ($DockerGpuProof.exit_code -ne 0 -or $DockerGpuProof.output -notmatch "Test PASSED") {
        $Blockers.Add("docker_gpu_vector_add_failed")
    }

    $MlflowHealth = Invoke-Captured -Name "09-mlflow-health" -FilePath "curl.exe" -ArgumentList @(
        "-fsS", "http://localhost:5000/health"
    ) -IgnoreFailure
    if ($MlflowHealth.exit_code -ne 0) {
        $Blockers.Add("mlflow_health_failed")
    }

    if (Test-Path -LiteralPath $RollbackRegistryPath) {
        $RollbackRegistry = Get-Content -LiteralPath $RollbackRegistryPath -Raw | ConvertFrom-Json
        if ($RollbackRegistry.candidate_id -eq $CandidateId -and $RollbackRegistry.model_artifact) {
            $SourceModelPath = [string]$RollbackRegistry.model_artifact
            $SourceSplitManifestPath = [string]$RollbackRegistry.split_manifest
            $SourceMlflowRunId = [string]$RollbackRegistry.mlflow_run_id
        }
    }
    if (-not $SourceMlflowRunId) {
        $SourceCandidateSummaryPath = Join-Path $SourceCandidateDir "candidate_summary.json"
        if (Test-Path -LiteralPath $SourceCandidateSummaryPath) {
            $SourceCandidateSummary = Get-Content -LiteralPath $SourceCandidateSummaryPath -Raw | ConvertFrom-Json
            $SourceMlflowRunId = [string]$SourceCandidateSummary.mlflow_run_id
        }
    }

    if (-not (Test-Path $SourceModelPath)) {
        $Blockers.Add("selected_model_artifact_missing")
    }
    if (-not (Test-Path $SourceSplitManifestPath)) {
        $Blockers.Add("selected_split_manifest_missing")
    }
    if (-not $SourceMlflowRunId) {
        $Blockers.Add("selected_source_mlflow_run_missing")
    }
    $SourceModelSha256 = if (Test-Path $SourceModelPath) { (Get-FileHash $SourceModelPath -Algorithm SHA256).Hash.ToLowerInvariant() } else { "" }
    $SplitManifestSha256 = if (Test-Path $SourceSplitManifestPath) { (Get-FileHash $SourceSplitManifestPath -Algorithm SHA256).Hash.ToLowerInvariant() } else { "" }

    $Render = Invoke-Captured -Name "10-kustomize-render" -FilePath "kubectl" -ArgumentList @(
        "kustomize", "infra/kubernetes/model-runtime"
    )
    $Render.output | Set-Content -Path (Join-Path $EvidenceDir "rendered-manifests.yaml") -Encoding utf8

    if ($Blockers.Count -gt 0) {
        $BlockedApplyPath = "infra/kubernetes/model-runtime"
        $BlockedTrainingImage = Invoke-Captured -Name "11-blocked-training-image-digest" -FilePath "docker" -ArgumentList @(
            "image", "inspect", $TrainingImage, "--format", "{{index .RepoDigests 0}}"
        ) -IgnoreFailure
        $BlockedServingImage = Invoke-Captured -Name "12-blocked-serving-image-digest" -FilePath "docker" -ArgumentList @(
            "image", "inspect", $ServingImage, "--format", "{{index .RepoDigests 0}}"
        ) -IgnoreFailure
        if ($BlockedTrainingImage.exit_code -eq 0 -and $BlockedServingImage.exit_code -eq 0) {
            $BlockedApplyPath = New-RuntimeManifestOverlay `
                -TrainingImageDigest $BlockedTrainingImage.output.Trim() `
                -ServingImageDigest $BlockedServingImage.output.Trim() `
                -WslDriverPath $WslDriverPath
            Invoke-Captured -Name "13-blocked-runtime-kustomize-render" -FilePath "kubectl" -ArgumentList @(
                "kustomize", $BlockedApplyPath
            ) -IgnoreFailure | Out-Null
        }
        Invoke-Captured -Name "14-blocked-delete-previous-training-job" -FilePath "kubectl" -ArgumentList @(
            "delete", "job/evm-b7-training", "-n", "evm-training", "--ignore-not-found=true", "--wait=true"
        ) -IgnoreFailure | Out-Null
        Invoke-Captured -Name "15-blocked-kustomize-apply" -FilePath "kubectl" -ArgumentList @(
            "apply", "-k", $BlockedApplyPath
        ) -IgnoreFailure | Out-Null
        Start-Sleep -Seconds 5
        Invoke-Captured -Name "16-blocked-training-resources" -FilePath "kubectl" -ArgumentList @(
            "get", "pods,jobs,deploy,rs,svc,pvc", "-n", "evm-training", "-o", "wide"
        ) -IgnoreFailure | Out-Null
        Invoke-Captured -Name "17-blocked-staging-resources" -FilePath "kubectl" -ArgumentList @(
            "get", "pods,jobs,deploy,rs,svc,pvc", "-n", "evm-staging", "-o", "wide"
        ) -IgnoreFailure | Out-Null
        Invoke-Captured -Name "18-blocked-training-describe" -FilePath "kubectl" -ArgumentList @(
            "describe", "job/evm-b7-training", "-n", "evm-training"
        ) -IgnoreFailure | Out-Null
        Invoke-Captured -Name "19-blocked-training-pod-describe" -FilePath "kubectl" -ArgumentList @(
            "describe", "pod", "-n", "evm-training", "-l", "app.kubernetes.io/name=evm-b7-training"
        ) -IgnoreFailure | Out-Null
        Invoke-Captured -Name "20-blocked-training-events" -FilePath "kubectl" -ArgumentList @(
            "get", "events", "-n", "evm-training", "--sort-by=.lastTimestamp"
        ) -IgnoreFailure | Out-Null
        Write-EvidenceIndex -Status "blocked" -Additional @{
            kubernetes_status = $KubernetesStatus.output
            gpu_allocatable = $GpuAllocatable
            docker_gpu_vector_add = ($DockerGpuProof.output -match "Test PASSED")
            mlflow_ready = ($MlflowHealth.exit_code -eq 0)
            source_model_sha256 = $SourceModelSha256
            split_manifest_sha256 = $SplitManifestSha256
            training_image_digest = $BlockedTrainingImage.output.Trim()
            serving_image_digest = $BlockedServingImage.output.Trim()
            completion_claim_allowed = $false
        }
        if ($AllowBlocked) {
            Write-Host "EVM-226 is blocked. Evidence: $EvidenceDir"
            return
        }
        throw "EVM-226 preflight failed: $($Blockers -join ', ')"
    }

    if (-not $SkipBuild) {
        Invoke-Captured -Name "11-training-image-build" -FilePath "docker" -ArgumentList @(
            "build", "-f", "infra/docker/efficientnet-training/Dockerfile", "-t", $TrainingImage, "."
        ) | Out-Null
        Invoke-Captured -Name "12-serving-image-build" -FilePath "docker" -ArgumentList @(
            "build", "-f", "infra/docker/efficientnet-serving/Dockerfile", "-t", $ServingImage, "."
        ) | Out-Null
    }
    $TrainingImageDigest = (docker image inspect $TrainingImage --format "{{index .RepoDigests 0}}").Trim()
    $ServingImageDigest = (docker image inspect $ServingImage --format "{{index .RepoDigests 0}}").Trim()
    $RuntimeManifestDir = New-RuntimeManifestOverlay `
        -TrainingImageDigest $TrainingImageDigest `
        -ServingImageDigest $ServingImageDigest `
        -WslDriverPath $WslDriverPath
    Invoke-Captured -Name "13-runtime-kustomize-render" -FilePath "kubectl" -ArgumentList @(
        "kustomize", $RuntimeManifestDir
    ) | Out-Null

    $TrainingStartedAtUtc = [DateTimeOffset]::UtcNow
    Invoke-Captured -Name "14-delete-previous-training-job" -FilePath "kubectl" -ArgumentList @(
        "delete", "job/evm-b7-training", "-n", "evm-training", "--ignore-not-found=true", "--wait=true"
    ) -IgnoreFailure | Out-Null
    Invoke-Captured -Name "15-kustomize-apply" -FilePath "kubectl" -ArgumentList @(
        "apply", "-k", $RuntimeManifestDir
    ) | Out-Null
    Wait-KubernetesJobTerminal `
        -JobName "evm-b7-training" `
        -Namespace "evm-training" `
        -TimeoutSeconds $TrainingTimeoutSeconds `
        -EvidenceName "16-training-wait"
    Invoke-Captured -Name "17-training-logs" -FilePath "kubectl" -ArgumentList @(
        "logs", "-n", "evm-training", "job/evm-b7-training", "--all-containers=true"
    ) | Out-Null
    Invoke-Captured -Name "18-training-describe" -FilePath "kubectl" -ArgumentList @(
        "describe", "job/evm-b7-training", "-n", "evm-training"
    ) | Out-Null
    Invoke-Captured -Name "19-training-resources" -FilePath "kubectl" -ArgumentList @(
        "get", "pods,jobs,pvc", "-n", "evm-training", "-o", "wide"
    ) | Out-Null
    Invoke-Captured -Name "20-training-resource-usage" -FilePath "kubectl" -ArgumentList @(
        "top", "pod", "-n", "evm-training"
    ) -IgnoreFailure | Out-Null

    $CandidateSummaryPath = Join-Path $CandidateDir "candidate_summary.json"
    $EnvironmentReportPath = Join-Path $CandidateDir "environment_report.json"
    $GpuProfilePath = Join-Path $CandidateDir "gpu_profile.json"
    $RequiredTrainingArtifacts = @(
        $ModelPath,
        $CandidateSummaryPath,
        $EnvironmentReportPath,
        $GpuProfilePath,
        (Join-Path $CandidateDir "training_history.json"),
        (Join-Path $CandidateDir "confusion_matrix.json"),
        (Join-Path $CandidateDir "confusion_matrix.png"),
        (Join-Path $CandidateDir "threshold_calibration.json"),
        (Join-Path $CandidateDir "model_card.md"),
        (Join-Path $CandidateDir "lineage.json")
    )
    foreach ($ArtifactPath in $RequiredTrainingArtifacts) {
        if (-not (Test-Path -LiteralPath $ArtifactPath)) {
            throw "Training Job completed without required artifact: $ArtifactPath"
        }
    }

    $CandidateSummary = Get-Content -LiteralPath $CandidateSummaryPath -Raw | ConvertFrom-Json
    $EnvironmentReport = Get-Content -LiteralPath $EnvironmentReportPath -Raw | ConvertFrom-Json
    $GpuProfile = Get-Content -LiteralPath $GpuProfilePath -Raw | ConvertFrom-Json
    if ($CandidateSummary.candidate_id -ne $CandidateId -or $CandidateSummary.status -ne "pass") {
        throw "Selected candidate did not pass: $($CandidateSummary.status)"
    }
    if ($CandidateSummary.PSObject.Properties.Name -contains "execution_blockers" -and $CandidateSummary.execution_blockers.Count -gt 0) {
        throw "Selected candidate has execution blockers: $($CandidateSummary.execution_blockers -join ', ')"
    }
    if (-not $CandidateSummary.mlflow_run_id -or $CandidateSummary.mlflow_status -ne "logged") {
        throw "Selected candidate is missing a logged MLflow run"
    }
    if ([int]$CandidateSummary.conditions.epochs -lt 3 -or [int]$CandidateSummary.optimizer_step_count -le 0) {
        throw "Selected candidate lacks real three-epoch optimizer evidence"
    }
    $CandidateCreatedAt = [DateTimeOffset]::Parse([string]$CandidateSummary.created_at)
    if ($CandidateCreatedAt -lt $TrainingStartedAtUtc.AddSeconds(-2)) {
        throw "Candidate summary predates this Kubernetes training execution"
    }
    if ($EnvironmentReport.platform -notmatch "linux" -or -not [bool]$EnvironmentReport.cuda_available -or $EnvironmentReport.device -ne "cuda") {
        throw "Environment report does not prove Linux CUDA execution"
    }
    if ($GpuProfile.device -ne "cuda" -or [double]$GpuProfile.cuda_memory_peak_mb -le 0) {
        throw "GPU profile does not contain CUDA peak-memory evidence"
    }
    if ((Get-Item -LiteralPath $ModelPath).LastWriteTimeUtc -lt $TrainingStartedAtUtc.UtcDateTime.AddSeconds(-2)) {
        throw "Model artifact was not refreshed by this Kubernetes training execution"
    }
    $TrainedModelSha256 = (Get-FileHash $ModelPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($CandidateSummary.model_sha256 -ne $TrainedModelSha256) {
        throw "Candidate summary model digest does not match the trained artifact"
    }
    Copy-Item $CandidateSummaryPath (Join-Path $EvidenceDir "candidate_summary.json") -Force
    Copy-Item $EnvironmentReportPath (Join-Path $EvidenceDir "environment_report.json") -Force
    Copy-Item $GpuProfilePath (Join-Path $EvidenceDir "gpu_profile.json") -Force

    $MlflowArtifactContainerDir = $CandidateDir.Replace(
        "F:\EnterpriseMLOps_Data\enterprise-vision-mlops",
        "/mnt/evm-data"
    ).Replace("\", "/")
    $MlflowUploadCode = (
        "from mlflow import MlflowClient; " +
        "MlflowClient().log_artifacts('$($CandidateSummary.mlflow_run_id)', " +
        "'$MlflowArtifactContainerDir', artifact_path='evidence')"
    )
    Invoke-Captured -Name "20b-mlflow-artifact-upload" -FilePath "docker" -ArgumentList @(
        "run", "--rm", "--network", "evm-local",
        "-e", "MLFLOW_TRACKING_URI=http://mlflow:5000",
        "-v", "F:/EnterpriseMLOps_Data/enterprise-vision-mlops:/mnt/evm-data:ro",
        "enterprise-vision-mlops-mlflow",
        "python", "-c", $MlflowUploadCode
    ) | Out-Null
    $MlflowArtifactList = Invoke-Captured -Name "20c-mlflow-artifact-list" -FilePath "curl.exe" -ArgumentList @(
        "-fsS", "-G",
        "--data-urlencode", "run_id=$($CandidateSummary.mlflow_run_id)",
        "--data-urlencode", "path=evidence",
        "http://localhost:5000/api/2.0/mlflow/artifacts/list"
    )
    if ($MlflowArtifactList.output -notmatch 'evidence/model\.pt') {
        throw "MLflow artifact listing does not contain evidence/model.pt"
    }
    $MlflowArtifactModelUploaded = $true

    Invoke-Captured -Name "21-serving-model-identity" -FilePath "kubectl" -ArgumentList @(
        "set", "env", "deployment/evm-b7-serving", "-n", "evm-staging", "EVM_MODEL_SHA256=$TrainedModelSha256"
    ) | Out-Null
    Invoke-Captured -Name "22-serving-scale" -FilePath "kubectl" -ArgumentList @(
        "scale", "deployment/evm-b7-serving", "-n", "evm-staging", "--replicas=1"
    ) | Out-Null
    Invoke-Captured -Name "23-serving-rollout" -FilePath "kubectl" -ArgumentList @(
        "rollout", "status", "deployment/evm-b7-serving", "-n", "evm-staging", "--timeout=${RolloutTimeoutSeconds}s"
    ) | Out-Null
    Invoke-Captured -Name "24-serving-resources" -FilePath "kubectl" -ArgumentList @(
        "get", "pods,deploy,rs,svc,pvc", "-n", "evm-staging", "-o", "wide"
    ) | Out-Null
    Invoke-Captured -Name "25-serving-describe" -FilePath "kubectl" -ArgumentList @(
        "describe", "deployment/evm-b7-serving", "-n", "evm-staging"
    ) | Out-Null
    Invoke-Captured -Name "26-serving-logs" -FilePath "kubectl" -ArgumentList @(
        "logs", "-n", "evm-staging", "deployment/evm-b7-serving", "--all-containers=true", "--tail=300"
    ) | Out-Null

    $PortForwardOut = Join-Path $EvidenceDir "27-serving-port-forward.stdout.log"
    $PortForwardErr = Join-Path $EvidenceDir "27-serving-port-forward.stderr.log"
    $PortForward = Start-Process -FilePath "kubectl" -ArgumentList @(
        "port-forward", "-n", "evm-staging", "service/evm-b7-serving", "18000:8000"
    ) -PassThru -WindowStyle Hidden -RedirectStandardOutput $PortForwardOut -RedirectStandardError $PortForwardErr
    try {
        Start-Sleep -Seconds 5
        Invoke-Captured -Name "28-serving-ready" -FilePath "curl.exe" -ArgumentList @(
            "-fsS", "http://localhost:18000/ready"
        ) | Out-Null
        $RequestPath = Join-Path $EvidenceDir "inference_request.json"
        @{
            image_uri = "/mnt/evm-data/data/raw/industrial/visa/pcb3/Data/Images/Normal/0682.JPG"
        } | ConvertTo-Json | Set-Content -Path $RequestPath -Encoding utf8
        Invoke-Captured -Name "29-serving-inference-response" -FilePath "curl.exe" -ArgumentList @(
            "-fsS", "-H", "Content-Type: application/json", "--data-binary", "@$RequestPath", "http://localhost:18000/predict"
        ) | Out-Null
    }
    finally {
        if (-not $PortForward.HasExited) {
            Stop-Process -Id $PortForward.Id -Force
        }
    }

    $InvalidDigest = "0" * 64
    Invoke-Captured -Name "30-controlled-failure-patch" -FilePath "kubectl" -ArgumentList @(
        "set", "env", "deployment/evm-b7-serving", "-n", "evm-staging", "EVM_MODEL_SHA256=$InvalidDigest"
    ) | Out-Null
    $FailureRollout = Invoke-Captured -Name "31-controlled-failure-rollout" -FilePath "kubectl" -ArgumentList @(
        "rollout", "status", "deployment/evm-b7-serving", "-n", "evm-staging", "--timeout=180s"
    ) -IgnoreFailure
    if ($FailureRollout.exit_code -eq 0) {
        throw "Controlled invalid-digest rollout unexpectedly became ready"
    }
    Invoke-Captured -Name "32-controlled-failure-pods" -FilePath "kubectl" -ArgumentList @(
        "get", "pods,deploy,rs", "-n", "evm-staging", "-o", "wide"
    ) -IgnoreFailure | Out-Null
    Invoke-Captured -Name "33-controlled-failure-logs" -FilePath "kubectl" -ArgumentList @(
        "logs", "-n", "evm-staging", "deployment/evm-b7-serving", "--all-containers=true", "--tail=300"
    ) -IgnoreFailure | Out-Null
    Invoke-Captured -Name "34-serving-rollback" -FilePath "kubectl" -ArgumentList @(
        "rollout", "undo", "deployment/evm-b7-serving", "-n", "evm-staging"
    ) | Out-Null
    Invoke-Captured -Name "35-serving-rollback-status" -FilePath "kubectl" -ArgumentList @(
        "rollout", "status", "deployment/evm-b7-serving", "-n", "evm-staging", "--timeout=${RolloutTimeoutSeconds}s"
    ) | Out-Null
    Invoke-Captured -Name "36-serving-rollout-history" -FilePath "kubectl" -ArgumentList @(
        "rollout", "history", "deployment/evm-b7-serving", "-n", "evm-staging"
    ) | Out-Null

    Write-EvidenceIndex -Status "pass" -Additional @{
        kubernetes_status = $KubernetesStatus.output
        gpu_allocatable = $GpuAllocatable
        docker_gpu_vector_add = $true
        mlflow_ready = $true
        source_model_sha256 = $SourceModelSha256
        trained_model_sha256 = $TrainedModelSha256
        split_manifest_sha256 = $SplitManifestSha256
        training_image_digest = $TrainingImageDigest
        serving_image_digest = $ServingImageDigest
        mlflow_run_id = $CandidateSummary.mlflow_run_id
        mlflow_artifact_model_uploaded = $MlflowArtifactModelUploaded
        controlled_failure_observed = $true
        rollback_completed = $true
        completion_claim_allowed = $true
    }
    Write-Host "EVM-226 execution proof passed. Evidence: $EvidenceDir"
}
catch {
    if ($Blockers.Count -eq 0) {
        $Blockers.Add($_.Exception.Message)
    }
    $TrainingJobExists = & kubectl get job/evm-b7-training -n evm-training -o name 2>$null
    if ($LASTEXITCODE -eq 0 -and $TrainingJobExists) {
        Invoke-Captured -Name "failure-training-logs" -FilePath "kubectl" -ArgumentList @(
            "logs", "-n", "evm-training", "job/evm-b7-training", "--all-containers=true"
        ) -IgnoreFailure | Out-Null
        Invoke-Captured -Name "failure-training-describe" -FilePath "kubectl" -ArgumentList @(
            "describe", "job/evm-b7-training", "-n", "evm-training"
        ) -IgnoreFailure | Out-Null
        Invoke-Captured -Name "failure-training-pod-describe" -FilePath "kubectl" -ArgumentList @(
            "describe", "pod", "-n", "evm-training", "-l", "app.kubernetes.io/name=evm-b7-training"
        ) -IgnoreFailure | Out-Null
        Invoke-Captured -Name "failure-training-events" -FilePath "kubectl" -ArgumentList @(
            "get", "events", "-n", "evm-training", "--sort-by=.lastTimestamp"
        ) -IgnoreFailure | Out-Null
    }
    if (-not (Test-Path (Join-Path $EvidenceDir "evidence_index.json"))) {
        Write-EvidenceIndex -Status "failed" -Additional @{ completion_claim_allowed = $false }
    }
    throw
}
finally {
    Pop-Location
}
