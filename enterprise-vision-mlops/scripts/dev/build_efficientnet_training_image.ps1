param(
    [string]$Image = "enterprise-vision-mlops-efficientnet-training:local",
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$CatalogPath = Join-Path $ProjectRoot "configs\model_components.json"
$Dockerfile = Join-Path $ProjectRoot "infra\docker\efficientnet-training\Dockerfile"

Push-Location $ProjectRoot
try {
    if (-not $SkipBuild) {
        & docker build --provenance=false -f $Dockerfile -t $Image .
        if ($LASTEXITCODE -ne 0) {
            throw "EfficientNet training image build failed."
        }
    }

    $inspection = & docker image inspect $Image | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or -not $inspection) {
        throw "EfficientNet training image inspection failed."
    }
    $repoDigest = @($inspection[0].RepoDigests) |
        Where-Object { $_ -like "enterprise-vision-mlops-efficientnet-training@sha256:*" } |
        Select-Object -First 1
    if (-not $repoDigest) {
        throw "Immutable training image RepoDigest is missing."
    }

    $catalog = Get-Content -LiteralPath $CatalogPath -Raw | ConvertFrom-Json
    $expectedImages = @(
        @($catalog.components | ForEach-Object { $_.training_image }) |
            Sort-Object -Unique
    )
    if ($expectedImages.Count -ne 1) {
        throw "Model catalog training image digests are inconsistent."
    }

    $status = if ($repoDigest -eq $expectedImages[0]) { "pass" } else { "blocked" }
    [pscustomobject]@{
        status = $status
        image = $Image
        observed_repo_digest = $repoDigest
        catalog_repo_digest = $expectedImages[0]
        provenance_attestation = "disabled-for-stable-local-runtime-digest"
    } | ConvertTo-Json

    if ($status -ne "pass") {
        throw "Built training image digest does not match the model catalog."
    }
}
finally {
    Pop-Location
}
