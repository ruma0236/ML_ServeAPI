param(
    [string]$TrainingImage = "enterprise-vision-mlops-efficientnet-training:local",
    [string]$DataRoot = "F:\EnterpriseMLOps_Data\enterprise-vision-mlops"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$DataRoot = (Resolve-Path $DataRoot).Path

docker image inspect $TrainingImage *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Required training image is missing: $TrainingImage"
}

$output = & docker run --rm --gpus all --read-only `
    --shm-size 2g `
    --tmpfs "/tmp:rw,noexec,nosuid,size=2g" `
    -e HOME=/tmp `
    -e PYTHONPATH=/app/src `
    -e EVM_HOST_DATA_ROOT=F:/EnterpriseMLOps_Data/enterprise-vision-mlops `
    -e EVM_DATA_MOUNT_ROOT=/mnt/evm-data `
    -v "${DataRoot}:/mnt/evm-data" `
    -v "${ProjectRoot}\src:/app/src:ro" `
    -v "${ProjectRoot}\scripts:/app/scripts:ro" `
    -v "${ProjectRoot}\configs:/app/configs:ro" `
    -v "${ProjectRoot}\pyproject.toml:/app/pyproject.toml:ro" `
    -v "${ProjectRoot}\docker-compose.yml:/app/docker-compose.yml:ro" `
    $TrainingImage `
    python /app/scripts/run_pipeline.py drift-review --config /app/configs/local_visa.toml 2>&1

if ($LASTEXITCODE -ne 0) {
    $output | ForEach-Object { Write-Host $_ }
    throw "Measured B7 drift review failed"
}

$output | ForEach-Object { Write-Host $_ }
$jsonStart = -1
for ($index = 0; $index -lt $output.Count; $index++) {
    if ([string]$output[$index] -match '^\s*\{') {
        $jsonStart = $index
        break
    }
}
if ($jsonStart -lt 0) {
    throw "Drift review did not return a JSON summary"
}
$summary = (($output[$jsonStart..($output.Count - 1)] | ForEach-Object { [string]$_ }) -join "`n") | ConvertFrom-Json
if ($summary.decision -ne "review_required") {
    throw "Expected a measured review_required decision"
}
if ($summary.automatic_retraining -ne $false) {
    throw "Automatic retraining must remain disabled"
}

Write-Host "Measured B7 drift evidence: $($summary.evidence_index)"
