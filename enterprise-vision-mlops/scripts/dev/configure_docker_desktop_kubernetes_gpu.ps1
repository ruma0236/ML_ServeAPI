param(
    [int]$RolloutTimeoutSeconds = 180,
    [string]$EvidenceRoot = "F:\EnterpriseMLOps_Data\enterprise-vision-mlops\artifacts\w7\kubernetes_gpu_bridge",
    [switch]$SkipDockerRuntimeConfiguration
)

$ErrorActionPreference = "Stop"
if (Get-Variable PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
    $PSNativeCommandUseErrorActionPreference = $false
}

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$TemplatePath = Join-Path $ProjectRoot "infra\kubernetes\docker-desktop-gpu\nvidia-device-plugin.yaml.tmpl"
$ProbeTemplatePath = Join-Path $ProjectRoot "infra\kubernetes\docker-desktop-gpu\gpu-resource-probe.yaml.tmpl"
$DaemonConfigPath = Join-Path $env:USERPROFILE ".docker\daemon.json"

function Wait-DockerEngine {
    param([int]$TimeoutSeconds = 180)

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        $runtime = & docker info --format "{{.DefaultRuntime}}" 2>$null
        if ($LASTEXITCODE -eq 0 -and $runtime) {
            return $runtime.Trim()
        }
        Start-Sleep -Seconds 3
    } while ((Get-Date) -lt $deadline)
    throw "Docker Engine did not become ready within $TimeoutSeconds seconds."
}

function Wait-KubernetesNode {
    param([int]$TimeoutSeconds = 180)

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        $ready = & kubectl get node docker-desktop `
            -o "jsonpath={.status.conditions[?(@.type=='Ready')].status}" 2>$null
        if ($LASTEXITCODE -eq 0 -and $ready -eq "True") {
            return
        }
        Start-Sleep -Seconds 3
    } while ((Get-Date) -lt $deadline)
    throw "Docker Desktop Kubernetes node did not become Ready within $TimeoutSeconds seconds."
}

$defaultRuntime = Wait-DockerEngine
if ($defaultRuntime -ne "nvidia") {
    if ($SkipDockerRuntimeConfiguration) {
        throw "Docker default runtime is '$defaultRuntime'. Remove -SkipDockerRuntimeConfiguration or configure nvidia."
    }

    & docker desktop stop | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw "Docker Desktop stop failed."
    }

    $daemonConfig = if (Test-Path -LiteralPath $DaemonConfigPath) {
        Get-Content -LiteralPath $DaemonConfigPath -Raw | ConvertFrom-Json
    } else {
        [pscustomobject]@{}
    }
    if (Test-Path -LiteralPath $DaemonConfigPath) {
        $backupPath = "$DaemonConfigPath.pre-evm-gpu-$([DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ'))"
        Copy-Item -LiteralPath $DaemonConfigPath -Destination $backupPath
    }
    $daemonConfig | Add-Member -NotePropertyName "default-runtime" -NotePropertyValue "nvidia" -Force
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $DaemonConfigPath) | Out-Null
    $daemonConfig | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $DaemonConfigPath -Encoding utf8

    & docker desktop start | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw "Docker Desktop start failed after runtime configuration."
    }
    $defaultRuntime = Wait-DockerEngine
    if ($defaultRuntime -ne "nvidia") {
        throw "Docker Desktop restarted but default runtime is '$defaultRuntime'."
    }
}

Wait-KubernetesNode

$driverPath = (& wsl.exe -d docker-desktop -u root -- sh -lc `
    "find /usr/lib/wsl/drivers -name nvidia-smi -type f | head -n 1 | xargs dirname").Trim()
if ($LASTEXITCODE -ne 0 -or -not $driverPath -or -not $driverPath.StartsWith("/usr/lib/wsl/drivers/")) {
    throw "Unable to detect the active Docker Desktop WSL NVIDIA driver directory."
}

& docker run --rm --privileged -v "/:/host" alpine:3.21 `
    sh -lc "rm -f /host/etc/cdi/evm-wsl-gpu.yaml /host/etc/cdi/evm-wsl-nvidia-gpu.yaml /host/etc/cdi/evm-wsl-k8s-gpu.yaml" | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Unable to remove stale EVM CDI experiments from the Docker engine host."
}

$manifest = (Get-Content -LiteralPath $TemplatePath -Raw).Replace("__WSL_DRIVER_PATH__", $driverPath)
$manifest | & kubectl apply -f - | Out-Host
if ($LASTEXITCODE -ne 0) {
    throw "NVIDIA device plugin apply failed."
}

& kubectl rollout status daemonset/nvidia-device-plugin-daemonset `
    -n kube-system --timeout="$($RolloutTimeoutSeconds)s" | Out-Host
if ($LASTEXITCODE -ne 0) {
    & kubectl logs -n kube-system daemonset/nvidia-device-plugin-daemonset --tail=160 | Out-Host
    throw "NVIDIA device plugin rollout failed."
}

$deadline = (Get-Date).AddSeconds($RolloutTimeoutSeconds)
do {
    $node = & kubectl get node docker-desktop -o json | ConvertFrom-Json
    $allocatableGpu = $node.status.allocatable.'nvidia.com/gpu'
    if ($allocatableGpu) {
        break
    }
    Start-Sleep -Seconds 3
} while ((Get-Date) -lt $deadline)
if (-not $allocatableGpu) {
    throw "The device plugin is Ready but nvidia.com/gpu was not advertised."
}

$probeManifest = (Get-Content -LiteralPath $ProbeTemplatePath -Raw).Replace("__WSL_DRIVER_PATH__", $driverPath)
& kubectl delete pod evm-gpu-resource-probe -n evm-training --ignore-not-found=true --wait=true | Out-Host
$probeManifest | & kubectl apply -f - | Out-Host
if ($LASTEXITCODE -ne 0) {
    throw "GPU resource probe apply failed."
}
& kubectl wait --for=jsonpath='{.status.phase}'=Succeeded pod/evm-gpu-resource-probe `
    -n evm-training --timeout="$($RolloutTimeoutSeconds)s" | Out-Host
if ($LASTEXITCODE -ne 0) {
    & kubectl describe pod evm-gpu-resource-probe -n evm-training | Out-Host
    & kubectl logs pod/evm-gpu-resource-probe -n evm-training | Out-Host
    throw "The non-privileged nvidia.com/gpu resource probe failed."
}
$probeLog = (& kubectl logs pod/evm-gpu-resource-probe -n evm-training).Trim()
if ($LASTEXITCODE -ne 0 -or $probeLog -notmatch "NVIDIA GeForce RTX 4080 SUPER") {
    throw "GPU resource probe did not report the expected RTX 4080 SUPER."
}

$runId = "evm-gpu-bridge-{0}" -f [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ")
$evidenceDirectory = Join-Path $EvidenceRoot $runId
New-Item -ItemType Directory -Force -Path $evidenceDirectory | Out-Null
$manifest | Set-Content -LiteralPath (Join-Path $evidenceDirectory "device-plugin.yaml") -Encoding utf8
$probeManifest | Set-Content -LiteralPath (Join-Path $evidenceDirectory "gpu-resource-probe.yaml") -Encoding utf8
& kubectl get daemonset nvidia-device-plugin-daemonset -n kube-system -o json |
    Set-Content -LiteralPath (Join-Path $evidenceDirectory "device-plugin.json") -Encoding utf8
& kubectl get node docker-desktop -o json |
    Set-Content -LiteralPath (Join-Path $evidenceDirectory "node.json") -Encoding utf8
& kubectl logs -n kube-system daemonset/nvidia-device-plugin-daemonset --tail=200 |
    Set-Content -LiteralPath (Join-Path $evidenceDirectory "device-plugin.log") -Encoding utf8
& kubectl get pod evm-gpu-resource-probe -n evm-training -o json |
    Set-Content -LiteralPath (Join-Path $evidenceDirectory "gpu-resource-probe.json") -Encoding utf8
$probeLog | Set-Content -LiteralPath (Join-Path $evidenceDirectory "gpu-resource-probe.log") -Encoding utf8

$summary = [ordered]@{
    schema_version = "evm.w7.docker_desktop_gpu_bridge.v1"
    run_id = $runId
    generated_at = [DateTime]::UtcNow.ToString("o")
    cluster_context = (& kubectl config current-context).Trim()
    node = "docker-desktop"
    docker_default_runtime = $defaultRuntime
    device_plugin_image = "nvcr.io/nvidia/k8s-device-plugin:v0.18.0"
    wsl_driver_path = $driverPath
    gpu_capacity = $node.status.capacity.'nvidia.com/gpu'
    gpu_allocatable = $allocatableGpu
    gpu_resource_probe = "pass"
    gpu_resource_probe_output = $probeLog
    evidence_directory = $evidenceDirectory.Replace("\", "/")
}
$summaryPath = Join-Path $evidenceDirectory "summary.json"
$summary | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $summaryPath -Encoding utf8
$summary | ConvertTo-Json -Depth 10
