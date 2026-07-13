param(
    [string]$EvidenceDir = "",
    [switch]$BuildImages
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$CandidateId = "effnet-b7-img600-finetune-adamw"
$DataRoot = "F:\EnterpriseMLOps_Data\enterprise-vision-mlops"
$ModelPath = Join-Path $DataRoot "artifacts\w7\efficientnet\w7-efficientnet-real-test-matrix\$CandidateId\model.pt"
$TrainingImage = "enterprise-vision-mlops-efficientnet-training:local"
$ServingImage = "enterprise-vision-mlops-efficientnet-serving:local"
$ServingContainer = "evm-b7-serving-proof"
$InvalidContainer = "evm-b7-serving-invalid-digest"

if (-not $EvidenceDir) {
    $RunId = "w7-docker-b7-{0}" -f (Get-Date -Format "yyyyMMddTHHmmss")
    $EvidenceDir = Join-Path $DataRoot "artifacts\w7\kubernetes_b7\docker_runtime\$RunId"
}
New-Item -ItemType Directory -Force -Path $EvidenceDir | Out-Null

function Remove-ProofContainer {
    param([Parameter(Mandatory = $true)][string]$Name)
    $ContainerId = & docker ps -aq --filter "name=^/${Name}$"
    if ($ContainerId) {
        & docker rm -f $ContainerId | Out-Null
    }
}

function Wait-ForReady {
    param(
        [Parameter(Mandatory = $true)][int]$Port,
        [int]$TimeoutSeconds = 240
    )
    $Deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        try {
            $Response = Invoke-WebRequest -UseBasicParsing "http://localhost:$Port/ready" -TimeoutSec 3
            if ($Response.StatusCode -eq 200) { return }
        }
        catch {}
        Start-Sleep -Seconds 3
    } while ((Get-Date) -lt $Deadline)
    throw "Serving container did not become ready on port $Port"
}

function Save-ContainerLogs {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Path
    )
    $PreviousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $Logs = & docker logs $Name 2>&1
    $ErrorActionPreference = $PreviousPreference
    @($Logs) | ForEach-Object { "$_" } | Set-Content $Path -Encoding utf8
}

Push-Location $ProjectRoot
try {
    if (-not (Test-Path $ModelPath)) {
        throw "Selected B7 model artifact is missing: $ModelPath"
    }
    $ModelSha256 = (Get-FileHash $ModelPath -Algorithm SHA256).Hash.ToLowerInvariant()

    if ($BuildImages) {
        & docker build -f infra/docker/efficientnet-serving/Dockerfile -t $ServingImage .
        if ($LASTEXITCODE -ne 0) { throw "Serving image build failed" }
        & docker build --provenance=false -f infra/docker/efficientnet-training/Dockerfile -t $TrainingImage .
        if ($LASTEXITCODE -ne 0) { throw "Training image build failed" }
    }
    & docker image inspect $ServingImage 1>$null
    if ($LASTEXITCODE -ne 0) { throw "Serving image is missing: $ServingImage" }
    & docker image inspect $TrainingImage 1>$null
    if ($LASTEXITCODE -ne 0) { throw "Training image is missing: $TrainingImage" }

    $TrainingProof = @'
import json
from pathlib import Path
import torch
import torchvision
from evm.core.torch_efficientnet import VisaImageDataset, load_shard_records

index, splits = load_shard_records(
    Path("/mnt/evm-data/data/validated/visa/shards/shard_index.json")
)
sample_shapes = {
    name: list(VisaImageDataset(records, 600)[0][0].shape)
    for name, records in splits.items()
}
print(json.dumps({
    "torch": torch.__version__,
    "torchvision": torchvision.__version__,
    "cuda_available": torch.cuda.is_available(),
    "device": torch.cuda.get_device_name(0),
    "record_count": sum(len(records) for records in splits.values()),
    "split_counts": {name: len(records) for name, records in splits.items()},
    "sample_tensor_shapes": sample_shapes,
    "shard_count": len(index.get("shards", [])),
}))
'@
    $TrainingProofPath = Join-Path $EvidenceDir "docker_training_runtime_preflight.py"
    $TrainingProof | Set-Content $TrainingProofPath -Encoding utf8
    $TrainingOutput = & docker run --rm --gpus all --read-only `
        --tmpfs "/tmp:rw,noexec,nosuid,size=1g" `
        -e HOME=/tmp `
        -e EVM_HOST_DATA_ROOT=F:/EnterpriseMLOps_Data/enterprise-vision-mlops `
        -e EVM_DATA_MOUNT_ROOT=/mnt/evm-data `
        -v "${DataRoot}:/mnt/evm-data:ro" `
        -v "${EvidenceDir}:/proof:ro" `
        $TrainingImage python /proof/docker_training_runtime_preflight.py 2>&1
    if ($LASTEXITCODE -ne 0) {
        @($TrainingOutput) | Set-Content (Join-Path $EvidenceDir "docker_training_runtime_preflight.log") -Encoding utf8
        throw "Training image GPU/data preflight failed"
    }
    $TrainingLines = @($TrainingOutput) | ForEach-Object { "$_" }
    $TrainingJson = $TrainingLines | Where-Object { $_.Trim().StartsWith("{") } | Select-Object -Last 1
    if (-not $TrainingJson) {
        $TrainingLines | Set-Content (Join-Path $EvidenceDir "docker_training_runtime_preflight.log") -Encoding utf8
        throw "Training preflight did not return a JSON payload"
    }
    $TrainingJson | Set-Content (Join-Path $EvidenceDir "docker_training_runtime_preflight.json") -Encoding utf8
    $TrainingPayload = $TrainingJson | ConvertFrom-Json
    if (-not $TrainingPayload.cuda_available -or [int]$TrainingPayload.record_count -ne 10821) {
        throw "Training preflight did not satisfy CUDA/full-VisA acceptance"
    }

    Remove-ProofContainer -Name $ServingContainer
    & docker run -d --name $ServingContainer --gpus all -p 18001:8000 --read-only `
        --tmpfs "/tmp:rw,noexec,nosuid,size=1g" `
        -e "EVM_MODEL_SHA256=$ModelSha256" `
        -e "EVM_MODEL_CANDIDATE_ID=$CandidateId" `
        -e EVM_REQUIRE_CUDA=true `
        -v "${DataRoot}:/mnt/evm-data:ro" `
        $ServingImage | Set-Content (Join-Path $EvidenceDir "docker_serving_container_id.log") -Encoding utf8
    if ($LASTEXITCODE -ne 0) { throw "Serving container failed to start" }
    Wait-ForReady -Port 18001
    & curl.exe -fsS http://localhost:18001/ready | Set-Content (Join-Path $EvidenceDir "docker_serving_ready.json") -Encoding utf8

    $RequestPath = Join-Path $EvidenceDir "docker_serving_request.json"
    @{
        image_uri = "file:///F:/EnterpriseMLOps_Data/enterprise-vision-mlops/data/raw/industrial/visa/pcb3/Data/Images/Normal/0682.JPG"
    } | ConvertTo-Json -Compress | Set-Content $RequestPath -Encoding utf8
    & curl.exe -fsS -H "Content-Type: application/json" --data-binary "@$RequestPath" `
        http://localhost:18001/predict | Set-Content (Join-Path $EvidenceDir "docker_serving_response.json") -Encoding utf8
    if ($LASTEXITCODE -ne 0) { throw "Real VisA inference request failed" }
    Save-ContainerLogs -Name $ServingContainer -Path (Join-Path $EvidenceDir "docker_serving.log")

    Remove-ProofContainer -Name $InvalidContainer
    $InvalidSha256 = "0" * 64
    & docker run -d --name $InvalidContainer --gpus all -p 18002:8000 --read-only `
        --tmpfs "/tmp:rw,noexec,nosuid,size=1g" `
        -e "EVM_MODEL_SHA256=$InvalidSha256" `
        -e "EVM_MODEL_CANDIDATE_ID=$CandidateId" `
        -e EVM_REQUIRE_CUDA=true `
        -v "${DataRoot}:/mnt/evm-data:ro" `
        $ServingImage | Set-Content (Join-Path $EvidenceDir "docker_invalid_digest_container_id.log") -Encoding utf8
    if ($LASTEXITCODE -ne 0) { throw "Invalid-digest container failed to start" }
    Start-Sleep -Seconds 8
    $InvalidBodyPath = Join-Path $EvidenceDir "docker_invalid_digest_ready.json"
    $InvalidStatus = & curl.exe -sS -o $InvalidBodyPath -w "%{http_code}" http://localhost:18002/ready
    if ($InvalidStatus -ne "503") {
        throw "Invalid model digest did not fail readiness with HTTP 503: $InvalidStatus"
    }
    Save-ContainerLogs -Name $InvalidContainer -Path (Join-Path $EvidenceDir "docker_invalid_digest_serving.log")

    $ServingInspect = & docker image inspect $ServingImage | ConvertFrom-Json
    $TrainingInspect = & docker image inspect $TrainingImage | ConvertFrom-Json
    $InferencePayload = Get-Content (Join-Path $EvidenceDir "docker_serving_response.json") -Raw | ConvertFrom-Json
    [ordered]@{
        schema_version = "evm.w7.docker_b7_runtime_proof.v1"
        status = "pass"
        scope = "supplemental_docker_gpu_runtime"
        candidate_id = $CandidateId
        model_sha256 = $ModelSha256
        training_image_id = $TrainingInspect[0].Id
        training_image_size_bytes = $TrainingInspect[0].Size
        serving_image_id = $ServingInspect[0].Id
        serving_image_size_bytes = $ServingInspect[0].Size
        cuda_available = $TrainingPayload.cuda_available
        cuda_device = $TrainingPayload.device
        record_count = $TrainingPayload.record_count
        split_counts = $TrainingPayload.split_counts
        inference = $InferencePayload
        invalid_digest_readiness_status = 503
        kubernetes_completion_claim_allowed = $false
        note = "Docker GPU proves packaging and model execution only; EVM-226 remains blocked until Kubernetes advertises nvidia.com/gpu."
        created_at = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    } | ConvertTo-Json -Depth 12 | Set-Content (Join-Path $EvidenceDir "docker_runtime_proof.json") -Encoding utf8

    Write-Host "Docker B7 runtime proof passed. Evidence: $EvidenceDir"
}
finally {
    Remove-ProofContainer -Name $ServingContainer
    Remove-ProofContainer -Name $InvalidContainer
    Pop-Location
}
