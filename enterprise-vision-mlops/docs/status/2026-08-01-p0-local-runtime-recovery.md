# P0 Local Runtime Recovery

Date: 2026-08-01
Scope: Docker Desktop Kubernetes GPU bridge, production serving, and host
control-plane process durability.

## Incident

Docker Desktop restarted after an NVIDIA driver update. The active WSL
DriverStore directory changed from
`nv_dispi.inf_amd64_6f3cfb7117944855` to
`nv_dispi.inf_amd64_0373d825005116d0`, while the device-plugin DaemonSet kept
mounting the old absolute path. The plugin entered `Error`, the node advertised
`nvidia.com/gpu=0`, and `evm-b0-production` remained Pending.

The host lifecycle worker and Kubernetes observer were also stale because a
plain Compose restart does not launch or supervise host processes. The API
container had no injected source revision for the same reason.

## Corrections

- GPU reconciliation now detects the current DriverStore directory from the
  `nvidia-smi` and `libcuda.so.1.1` file pair, compares it with the DaemonSet,
  and reapplies the plugin only when the path, readiness, or allocatable GPU
  state drifted.
- GPU readiness requires a positive integer. PowerShell string `"0"` can no
  longer be treated as successful capacity, and evidence uses a refreshed Node
  object after reconciliation.
- `-SkipGpuProbe` provides a non-disruptive startup check when the production
  workload already owns the single GPU. The full non-privileged resource probe
  remains available for maintenance verification.
- `start_host_runtime_supervisor.ps1` monitors process ownership and heartbeat
  age, restarts failed or stale observer/worker children, and records source
  commit, branch, child PIDs, revision match, errors, and restart counts.
- PID files are trusted only when the Windows command line matches the expected
  Python module, preventing termination of an unrelated process after PID
  reuse.
- `start_local_stack.ps1` force-recreates the API with the current Git revision,
  reconciles the GPU bridge, and starts the host supervisor by default.

## Live Proof

- GPU bridge evidence:
  `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/kubernetes_gpu_bridge/evm-gpu-bridge-20260801T110227Z`
- Detected WSL driver:
  `/usr/lib/wsl/drivers/nv_dispi.inf_amd64_0373d825005116d0`
- Device plugin: `1/1 Ready`
- Node GPU: capacity `1`, allocatable `1`
- Non-privileged probe: RTX 4080 SUPER, driver `610.88`, `16376 MiB`
- Production Deployment: `evm-production/evm-b0-production`, `1/1 Ready`
- Readiness: model loaded, EfficientNet-B0, CUDA available
- Real image inference: `normal`, confidence `0.998909`, device `cuda`
- Prometheus target `evm-b0-production`: `up`, no scrape error
- Supervisor heartbeat:
  `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/host_runtime/supervisor.json`
- Fault injection: lifecycle worker PID `31672` was terminated; the supervisor
  restored PID `31676` and recorded `restart_counts.lifecycle_worker=1` while
  remaining healthy.

## Verification

```powershell
.\scripts\dev\configure_docker_desktop_kubernetes_gpu.ps1 `
  -SkipDockerRuntimeConfiguration -SkipGpuProbe
kubectl get node docker-desktop `
  -o jsonpath="{.status.capacity.nvidia\.com/gpu}|{.status.allocatable.nvidia\.com/gpu}"
kubectl rollout status deployment/evm-b0-production `
  -n evm-production --timeout=300s
Invoke-RestMethod http://127.0.0.1:30800/ready
Invoke-RestMethod http://127.0.0.1:9090/api/v1/targets
```

PowerShell syntax validation passed for all five modified launch/reconcile
scripts. The focused Python regression set passed `57/57` tests.

## Boundary

This is a local Docker Desktop WSL2 GPU-PV recovery path. Linux production
clusters should use NVIDIA GPU Operator or the official device plugin with
DCGM telemetry. The host supervisor is a development workstation mechanism;
multi-node production process supervision belongs in Kubernetes and the
platform's standard service manager.
