[CmdletBinding()]
param(
    [switch]$Disable,
    [switch]$IncludeDataPlane
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command tailscale -ErrorAction SilentlyContinue)) {
    throw "tailscale CLI is not installed or is not available on PATH."
}

$status = tailscale status --json | ConvertFrom-Json
if ($status.BackendState -ne "Running" -or -not $status.Self.Online) {
    throw "Tailscale is not online on this host."
}

$dnsName = $status.Self.DNSName.TrimEnd(".")
$routes = @(
    [pscustomobject]@{ Name = "Control Panel"; RemotePort = 4173; LocalPort = 4173 },
    [pscustomobject]@{ Name = "Grafana"; RemotePort = 3001; LocalPort = 3000 },
    [pscustomobject]@{ Name = "MLflow"; RemotePort = 5001; LocalPort = 5000 },
    [pscustomobject]@{ Name = "Airflow"; RemotePort = 8081; LocalPort = 8080 },
    [pscustomobject]@{ Name = "Control API"; RemotePort = 8001; LocalPort = 8000 },
    [pscustomobject]@{ Name = "MinIO Console"; RemotePort = 9002; LocalPort = 9001 },
    [pscustomobject]@{ Name = "Prometheus"; RemotePort = 9091; LocalPort = 9090 }
)

if ($IncludeDataPlane) {
    $routes += [pscustomobject]@{ Name = "MinIO S3 API"; RemotePort = 9003; LocalPort = 9000 }
}

foreach ($route in $routes) {
    if ($Disable) {
        & tailscale serve "--http=$($route.RemotePort)" off | Out-Null
        continue
    }

    $ready = Test-NetConnection -ComputerName 127.0.0.1 -Port $route.LocalPort -InformationLevel Quiet
    if (-not $ready) {
        throw "$($route.Name) is not listening on 127.0.0.1:$($route.LocalPort)."
    }

    $arguments = @(
        "serve",
        "--bg",
        "--yes",
        "--http=$($route.RemotePort)",
        "http://127.0.0.1:$($route.LocalPort)"
    )
    & tailscale @arguments | Out-Null
}

if ($Disable) {
    Write-Output "Tailnet remote access routes were disabled."
    exit 0
}

$routes | Select-Object Name, LocalPort, RemotePort, @{
    Name = "TailnetUrl"
    Expression = { "http://$dnsName`:$($_.RemotePort)/" }
} | Format-Table -AutoSize
