param(
    [switch]$Build,
    [switch]$NoAirflowRecreate
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Push-Location $ProjectRoot

try {
    $commit = (git rev-parse --short HEAD).Trim()
    $branch = (git branch --show-current).Trim()

    if (-not $branch) {
        $branch = (git rev-parse --abbrev-ref HEAD).Trim()
    }

    $env:EVM_GIT_COMMIT = $commit
    $env:EVM_GIT_BRANCH = $branch

    Write-Host "EVM_GIT_COMMIT=$commit"
    Write-Host "EVM_GIT_BRANCH=$branch"

    $composeArgs = @("compose", "up", "-d")
    if ($Build) {
        $composeArgs += "--build"
    }

    & docker @composeArgs

    if (-not $NoAirflowRecreate) {
        & docker compose up -d --force-recreate airflow-init airflow-webserver airflow-scheduler
    }

    & docker compose ps
}
finally {
    Pop-Location
}
