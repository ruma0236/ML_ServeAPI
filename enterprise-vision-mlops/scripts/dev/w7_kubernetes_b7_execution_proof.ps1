param(
    [switch]$SkipBuild,
    [switch]$AllowBlocked,
    [int]$TrainingTimeoutSeconds = 7200,
    [int]$RolloutTimeoutSeconds = 600
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$EvidenceRoot = "F:\EnterpriseMLOps_Data\enterprise-vision-mlops\artifacts\w7\kubernetes_b7"
$RunId = "w7-k8s-b7-{0}" -f (Get-Date -Format "yyyyMMddTHHmmss")
$EvidenceDir = Join-Path $EvidenceRoot $RunId
$CandidateId = "effnet-b7-img600-finetune-adamw"
$CandidateDir = "F:\EnterpriseMLOps_Data\enterprise-vision-mlops\artifacts\w7\efficientnet\w7-efficientnet-real-test-matrix\$CandidateId"
$ModelPath = Join-Path $CandidateDir "model.pt"
$SplitManifestPath = Join-Path $CandidateDir "split_manifest.json"
$TrainingImage = "enterprise-vision-mlops-efficientnet-training:local"
$ServingImage = "enterprise-vision-mlops-efficientnet-serving:local"
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
        source_mlflow_run_id = "a4e2763b28ae494ea67944084edd4b3f"
        evidence_root = $EvidenceDir
        blockers = @($Blockers)
        git_commit = (git -C $ProjectRoot rev-parse HEAD).Trim()
        created_at = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    }
    foreach ($Key in $Additional.Keys) {
        $Payload[$Key] = $Additional[$Key]
    }
    $Payload | ConvertTo-Json -Depth 12 | Set-Content -Path (Join-Path $EvidenceDir "evidence_index.json") -Encoding utf8
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
    Invoke-Captured -Name "05-nvidia-device-plugin-apply" -FilePath "kubectl" -ArgumentList @(
        "apply",
        "-f",
        "https://raw.githubusercontent.com/NVIDIA/k8s-device-plugin/v0.17.1/deployments/static/nvidia-device-plugin.yml"
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

    if (-not (Test-Path $ModelPath)) {
        $Blockers.Add("selected_model_artifact_missing")
    }
    if (-not (Test-Path $SplitManifestPath)) {
        $Blockers.Add("selected_split_manifest_missing")
    }
    $SourceModelSha256 = if (Test-Path $ModelPath) { (Get-FileHash $ModelPath -Algorithm SHA256).Hash.ToLowerInvariant() } else { "" }
    $SplitManifestSha256 = if (Test-Path $SplitManifestPath) { (Get-FileHash $SplitManifestPath -Algorithm SHA256).Hash.ToLowerInvariant() } else { "" }

    $Render = Invoke-Captured -Name "10-kustomize-render" -FilePath "kubectl" -ArgumentList @(
        "kustomize", "infra/kubernetes/model-runtime"
    )
    $Render.output | Set-Content -Path (Join-Path $EvidenceDir "rendered-manifests.yaml") -Encoding utf8

    if ($Blockers.Count -gt 0) {
        Invoke-Captured -Name "11-blocked-kustomize-apply" -FilePath "kubectl" -ArgumentList @(
            "apply", "-k", "infra/kubernetes/model-runtime"
        ) -IgnoreFailure | Out-Null
        Start-Sleep -Seconds 5
        Invoke-Captured -Name "12-blocked-training-resources" -FilePath "kubectl" -ArgumentList @(
            "get", "pods,jobs,deploy,rs,svc,pvc", "-n", "evm-training", "-o", "wide"
        ) -IgnoreFailure | Out-Null
        Invoke-Captured -Name "13-blocked-staging-resources" -FilePath "kubectl" -ArgumentList @(
            "get", "pods,jobs,deploy,rs,svc,pvc", "-n", "evm-staging", "-o", "wide"
        ) -IgnoreFailure | Out-Null
        Invoke-Captured -Name "14-blocked-training-describe" -FilePath "kubectl" -ArgumentList @(
            "describe", "job/evm-b7-training", "-n", "evm-training"
        ) -IgnoreFailure | Out-Null
        Invoke-Captured -Name "15-blocked-training-pod-describe" -FilePath "kubectl" -ArgumentList @(
            "describe", "pod", "-n", "evm-training", "-l", "app.kubernetes.io/name=evm-b7-training"
        ) -IgnoreFailure | Out-Null
        Invoke-Captured -Name "16-blocked-training-events" -FilePath "kubectl" -ArgumentList @(
            "get", "events", "-n", "evm-training", "--sort-by=.lastTimestamp"
        ) -IgnoreFailure | Out-Null
        Write-EvidenceIndex -Status "blocked" -Additional @{
            kubernetes_status = $KubernetesStatus.output
            gpu_allocatable = $GpuAllocatable
            docker_gpu_vector_add = ($DockerGpuProof.output -match "Test PASSED")
            mlflow_ready = ($MlflowHealth.exit_code -eq 0)
            source_model_sha256 = $SourceModelSha256
            split_manifest_sha256 = $SplitManifestSha256
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
    if ($TrainingImageDigest -notmatch "@sha256:" -or $ServingImageDigest -notmatch "@sha256:") {
        throw "Training and serving images must have immutable local RepoDigests"
    }

    $RuntimeManifestDir = Join-Path $EvidenceDir "runtime-manifests"
    New-Item -ItemType Directory -Force -Path $RuntimeManifestDir | Out-Null
    Copy-Item "infra/kubernetes/model-runtime/*.yaml" $RuntimeManifestDir -Force
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
"@ | Add-Content (Join-Path $RuntimeManifestDir "kustomization.yaml") -Encoding utf8
    Invoke-Captured -Name "13-runtime-kustomize-render" -FilePath "kubectl" -ArgumentList @(
        "kustomize", $RuntimeManifestDir
    ) | Out-Null

    Invoke-Captured -Name "14-delete-previous-training-job" -FilePath "kubectl" -ArgumentList @(
        "delete", "job/evm-b7-training", "-n", "evm-training", "--ignore-not-found=true", "--wait=true"
    ) -IgnoreFailure | Out-Null
    Invoke-Captured -Name "15-kustomize-apply" -FilePath "kubectl" -ArgumentList @(
        "apply", "-k", $RuntimeManifestDir
    ) | Out-Null
    Invoke-Captured -Name "16-training-wait" -FilePath "kubectl" -ArgumentList @(
        "wait", "--for=condition=complete", "job/evm-b7-training", "-n", "evm-training", "--timeout=${TrainingTimeoutSeconds}s"
    ) | Out-Null
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

    if (-not (Test-Path $ModelPath)) {
        throw "Training Job completed without model artifact: $ModelPath"
    }
    $TrainedModelSha256 = (Get-FileHash $ModelPath -Algorithm SHA256).Hash.ToLowerInvariant()
    Copy-Item (Join-Path $CandidateDir "candidate_summary.json") (Join-Path $EvidenceDir "candidate_summary.json") -Force
    Copy-Item (Join-Path $CandidateDir "environment_report.json") (Join-Path $EvidenceDir "environment_report.json") -Force
    Copy-Item (Join-Path $CandidateDir "gpu_profile.json") (Join-Path $EvidenceDir "gpu_profile.json") -Force

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

    $CandidateSummary = Get-Content (Join-Path $CandidateDir "candidate_summary.json") -Raw | ConvertFrom-Json
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
    if (-not (Test-Path (Join-Path $EvidenceDir "evidence_index.json"))) {
        Write-EvidenceIndex -Status "failed" -Additional @{ completion_claim_allowed = $false }
    }
    throw
}
finally {
    Pop-Location
}
