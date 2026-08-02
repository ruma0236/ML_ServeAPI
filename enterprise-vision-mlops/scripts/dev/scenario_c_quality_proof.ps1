param(
    [string]$TrainingImage = "enterprise-vision-mlops-efficientnet-training:local",
    [string]$DataRoot = "F:\EnterpriseMLOps_Data\enterprise-vision-mlops",
    [string]$CtRoot = "F:\EnterpriseMLOps_CT\enterprise-vision-mlops"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$DataRoot = (Resolve-Path $DataRoot).Path
$CtRoot = (Resolve-Path $CtRoot).Path
$sourceCommit = (git -C $ProjectRoot rev-parse HEAD).Trim()
$sourceBranch = (git -C $ProjectRoot branch --show-current).Trim()
$dirty = [bool](git -C $ProjectRoot status --porcelain -- .)
if ($dirty) {
    throw "Scenario C requires a clean repository worktree."
}

docker image inspect $TrainingImage *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Required training image is missing: $TrainingImage"
}

$targetUid = (kubectl get deployment evm-b0-production -n evm-production `
    -o jsonpath="{.metadata.uid}").Trim()
if (-not $targetUid) {
    throw "Production B0 deployment UID is unavailable."
}

$previousErrorActionPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$output = & docker run --rm --gpus all --read-only `
    --shm-size 2g `
    --tmpfs "/tmp:rw,noexec,nosuid,size=2g" `
    -e HOME=/tmp `
    -e PYTHONPATH=/app/src `
    -e EVM_HOST_DATA_ROOT=F:/EnterpriseMLOps_Data/enterprise-vision-mlops `
    -e EVM_DATA_MOUNT_ROOT=/mnt/evm-data `
    -e EVM_CT_HOST_ROOT=F:/EnterpriseMLOps_CT/enterprise-vision-mlops `
    -e EVM_CT_MOUNT_ROOT=/mnt/evm-ct `
    -e EVM_SOURCE_COMMIT=$sourceCommit `
    -e EVM_SOURCE_BRANCH=$sourceBranch `
    -e EVM_SOURCE_DIRTY=false `
    -e EVM_TARGET_UID=$targetUid `
    -e EVM_CLUSTER_CONTEXT=docker-desktop `
    -e EVM_CLUSTER_NODE=docker-desktop `
    -v "${DataRoot}:/mnt/evm-data" `
    -v "${CtRoot}:/mnt/evm-ct:ro" `
    -v "${ProjectRoot}\src:/app/src:ro" `
    -v "${ProjectRoot}\scripts:/app/scripts:ro" `
    -v "${ProjectRoot}\configs:/app/configs:ro" `
    $TrainingImage `
    python /app/scripts/dev/run_scenario_c_quality.py `
        --config /app/configs/operations/scenario_c_quality_degradation.toml 2>&1
$dockerExitCode = $LASTEXITCODE
$ErrorActionPreference = $previousErrorActionPreference

$output | ForEach-Object { Write-Host $_ }
if ($dockerExitCode -ne 0) {
    throw "Scenario C quality proof failed."
}

$summary = ($output | Select-Object -Last 1) | ConvertFrom-Json
if ($summary.status -ne "passed" -or $summary.shift_decision -ne "review_required") {
    throw "Scenario C proof did not satisfy the expected closure."
}
if ($summary.real_gate.deployment_intent_created -ne $false) {
    throw "Scenario C created an unexpected deployment intent."
}

Write-Host "Scenario C evidence: $($summary.evidence_index_uri)"
