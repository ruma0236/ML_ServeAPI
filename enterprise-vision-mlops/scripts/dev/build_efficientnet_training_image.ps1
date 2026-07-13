param(
    [string]$TrainingImage = "enterprise-vision-mlops-efficientnet-training:local",
    [string]$ServingImage = "enterprise-vision-mlops-efficientnet-serving:local",
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$CatalogPath = Join-Path $ProjectRoot "configs\model_components.json"
$Dockerfile = Join-Path $ProjectRoot "infra\docker\efficientnet-training\Dockerfile"

Push-Location $ProjectRoot
try {
    $sourceRevision = (git rev-parse HEAD).Trim()
    if (-not $SkipBuild) {
        & docker build --provenance=false --build-arg "SOURCE_REVISION=$sourceRevision" -f $Dockerfile -t $TrainingImage .
        if ($LASTEXITCODE -ne 0) {
            throw "EfficientNet training image build failed."
        }
        & docker build --provenance=false --build-arg "SOURCE_REVISION=$sourceRevision" -f (Join-Path $ProjectRoot "infra\docker\efficientnet-serving\Dockerfile") -t $ServingImage .
        if ($LASTEXITCODE -ne 0) {
            throw "EfficientNet serving image build failed."
        }
    }

    $trainingInspection = & docker image inspect $TrainingImage | ConvertFrom-Json
    $servingInspection = & docker image inspect $ServingImage | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or -not $trainingInspection -or -not $servingInspection) {
        throw "EfficientNet runtime image inspection failed."
    }
    $trainingRepoDigest = @($trainingInspection[0].RepoDigests) |
        Where-Object { $_ -like "enterprise-vision-mlops-efficientnet-training@sha256:*" } |
        Select-Object -First 1
    $servingRepoDigest = @($servingInspection[0].RepoDigests) |
        Where-Object { $_ -like "enterprise-vision-mlops-efficientnet-serving@sha256:*" } |
        Select-Object -First 1
    if (-not $trainingRepoDigest -or -not $servingRepoDigest) {
        throw "Immutable runtime image RepoDigest is missing."
    }
    $trainingRevision = $trainingInspection[0].Config.Labels.'org.opencontainers.image.revision'
    $servingRevision = $servingInspection[0].Config.Labels.'org.opencontainers.image.revision'

    $catalog = Get-Content -LiteralPath $CatalogPath -Raw | ConvertFrom-Json
    $expectedTrainingImages = @(
        @($catalog.components | ForEach-Object { $_.training_image }) |
            Sort-Object -Unique
    )
    $expectedServingImages = @(
        @($catalog.components | ForEach-Object { $_.serving_image }) |
            Sort-Object -Unique
    )
    $expectedRevisions = @(
        @($catalog.components | ForEach-Object { $_.source_revision }) |
            Sort-Object -Unique
    )
    if ($expectedTrainingImages.Count -ne 1 -or $expectedServingImages.Count -ne 1 -or $expectedRevisions.Count -ne 1) {
        throw "Model catalog runtime identities are inconsistent."
    }

    $status = if (
        $trainingRepoDigest -eq $expectedTrainingImages[0] -and
        $servingRepoDigest -eq $expectedServingImages[0] -and
        $trainingRevision -eq $expectedRevisions[0] -and
        $servingRevision -eq $expectedRevisions[0]
    ) { "pass" } else { "blocked" }
    [pscustomobject]@{
        status = $status
        source_revision = $sourceRevision
        training_image = $TrainingImage
        observed_training_repo_digest = $trainingRepoDigest
        catalog_training_repo_digest = $expectedTrainingImages[0]
        observed_training_revision = $trainingRevision
        serving_image = $ServingImage
        observed_serving_repo_digest = $servingRepoDigest
        catalog_serving_repo_digest = $expectedServingImages[0]
        observed_serving_revision = $servingRevision
        catalog_source_revision = $expectedRevisions[0]
        provenance_attestation = "disabled-for-stable-local-runtime-digest"
    } | ConvertTo-Json

    if ($status -ne "pass") {
        throw "Built runtime image identities do not match the model catalog."
    }
}
finally {
    Pop-Location
}
