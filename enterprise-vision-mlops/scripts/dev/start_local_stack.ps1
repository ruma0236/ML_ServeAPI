param(
    [switch]$Build,
    [switch]$NoAirflowRecreate,
    [switch]$NoKubernetesGpuReconcile,
    [switch]$NoKubernetesObserver,
    [switch]$NoLifecycleWorker,
    [switch]$NoHostRuntimeSupervisor
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Push-Location $ProjectRoot

function Invoke-Docker {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    & docker @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "docker $($Arguments -join ' ') exited with code $LASTEXITCODE"
    }
}

try {
    $commit = (git rev-parse HEAD).Trim()
    $branch = (git branch --show-current).Trim()

    if (-not $branch) {
        $branch = (git rev-parse --abbrev-ref HEAD).Trim()
    }

    $env:EVM_GIT_COMMIT = $commit
    $env:EVM_GIT_BRANCH = $branch
    $env:EVM_EXPECTED_CI_COMMIT = $commit
    $env:EVM_LIFECYCLE_GUARD_REQUIRE_RUNTIME_MATCH = "true"

    $artifactRoot = if ($env:EVM_HOST_ARTIFACTS_ROOT) {
        $env:EVM_HOST_ARTIFACTS_ROOT
    } else {
        "F:\EnterpriseMLOps_Data\enterprise-vision-mlops\artifacts"
    }
    $prometheusTargetRoot = Join-Path $artifactRoot "w7\prometheus-targets"
    $prometheusTargetFile = Join-Path $prometheusTargetRoot "lifecycle-serving.json"
    New-Item -ItemType Directory -Force -Path $prometheusTargetRoot | Out-Null
    if (-not (Test-Path -LiteralPath $prometheusTargetFile)) {
        Set-Content -LiteralPath $prometheusTargetFile -Value "[]" -Encoding ascii
    }

    Write-Host "EVM_GIT_COMMIT=$commit"
    Write-Host "EVM_GIT_BRANCH=$branch"
    Write-Host "EVM_EXPECTED_CI_COMMIT=$commit"

    $localImages = @(docker image ls --format "{{.Repository}}:{{.Tag}}")
    if ($LASTEXITCODE -ne 0) {
        throw "docker image inventory failed with code $LASTEXITCODE"
    }
    $buildTargets = [System.Collections.Generic.List[string]]::new()
    $imageTargets = [ordered]@{
        "enterprise-vision-mlops-airflow:local" = "airflow-init"
        "enterprise-vision-mlops-mlflow:latest" = "mlflow"
        "enterprise-vision-mlops-api:latest" = "api"
        "enterprise-vision-mlops-control-panel:latest" = "control-panel"
    }
    foreach ($entry in $imageTargets.GetEnumerator()) {
        if ($Build -or $localImages -notcontains $entry.Key) {
            $buildTargets.Add($entry.Value)
        }
    }
    foreach ($target in $buildTargets) {
        Write-Host "Building Compose image through service: $target"
        Invoke-Docker -Arguments @("compose", "build", $target)
    }

    Invoke-Docker -Arguments @("compose", "up", "-d", "--no-build")
    Invoke-Docker -Arguments @(
        "compose", "up", "-d", "--no-deps", "--force-recreate", "--no-build", "api"
    )

    if (-not $NoAirflowRecreate) {
        Invoke-Docker -Arguments @(
            "compose", "up", "-d", "--force-recreate", "--no-build",
            "airflow-init", "airflow-webserver", "airflow-scheduler"
        )
    }

    Invoke-Docker -Arguments @("compose", "ps")

    if (-not $NoKubernetesGpuReconcile) {
        & (Join-Path $PSScriptRoot "configure_docker_desktop_kubernetes_gpu.ps1") `
            -SkipDockerRuntimeConfiguration `
            -SkipGpuProbe
    }

    if (-not $NoHostRuntimeSupervisor) {
        $supervisorParameters = @{
            Restart = $true
        }
        if ($NoKubernetesObserver) {
            $supervisorParameters.NoKubernetesObserver = $true
        }
        if ($NoLifecycleWorker) {
            $supervisorParameters.NoLifecycleWorker = $true
        }
        & (Join-Path $PSScriptRoot "start_host_runtime_supervisor.ps1") @supervisorParameters
    } else {
        if (-not $NoKubernetesObserver) {
            & (Join-Path $PSScriptRoot "start_kubernetes_observer.ps1") -Restart
        }
        if (-not $NoLifecycleWorker) {
            & (Join-Path $PSScriptRoot "start_lifecycle_worker.ps1") -Restart
        }
    }
}
finally {
    Pop-Location
}
