# 2026-07-10 W7 Kubernetes Real Execution Proof

> Updated target, 2026-07-10: Docker Desktop Kubernetes is the fixed W7
> cluster. Completion now requires the selected B7 GPU training Job and serving
> Deployment; the active execution scope of `EVM-227` is absorbed here.

## Scope

This checkpoint starts `EVM-226` with execution-grade evidence capture. It does
not close `EVM-226` because the local Kubernetes control plane is currently not
available.

## Implementation

- Added `scripts/dev/w7_kubernetes_real_execution_proof.ps1`.
- Evidence root:
  `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/kubernetes_real_execution`.
- Reference run:
  `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/kubernetes_real_execution/evm-k8s-real-proof-20260710T112507`.
- Summary artifact:
  `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/kubernetes_real_execution/evm-k8s-real-proof-20260710T112507/kubernetes_proof_summary.json`.
- Blocker artifact:
  `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/kubernetes_real_execution/evm-k8s-real-proof-20260710T112507/blocker_report.md`.

## Verification Performed

```powershell
scripts\dev\w7_kubernetes_real_execution_proof.ps1 -AllowBlocked
```

The proof runner captured:

- Docker Desktop status.
- Docker Desktop Kubernetes status.
- `kubectl version --client=true`.
- `kubectl config get-contexts`.
- `kubectl config current-context`.
- local Docker image inventory.
- `kubectl kustomize infra/kubernetes/local` render output.

The kustomize render succeeded and produced:

```text
F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/kubernetes_real_execution/evm-k8s-real-proof-20260710T112507/kustomize-render.yaml
```

The render includes the expected pipeline jobs:

- `evm-domain-pack-check`
- `evm-curation-workflow`
- `evm-lakehouse-probe`

## Current Result

Status: blocked.

Reason:

```text
Docker Desktop Kubernetes is disabled or stopped.
```

Observed Kubernetes status:

```json
{
  "content": {
    "mode": "kubeadm",
    "nodeCount": 1,
    "progressMessage": "Kubernetes is stopped",
    "version": "v1.34.1"
  },
  "source": "kubernetes",
  "status": "disabled"
}
```

Observed `kubectl` context result:

```text
error: current-context is not set
```

## Not Completed Yet

The following `EVM-226` acceptance evidence has not been produced yet:

- `kubectl apply -k infra/kubernetes/local`
- `kubectl wait` for API deployment availability
- `kubectl wait` for real pipeline job completion
- job logs from Kubernetes pods
- pod/job/service/PVC status after apply
- Kubernetes-produced F-drive pipeline artifacts

This checkpoint must not be treated as a successful Kubernetes runtime proof.

## Recovery

Enable Docker Desktop Kubernetes, verify a current context, then rerun:

```powershell
kubectl config current-context
kubectl get nodes -o wide
scripts\dev\w7_kubernetes_real_execution_proof.ps1 -BuildImages
```

Completion criteria remain unchanged: at least one configured API or pipeline
job must run in Kubernetes, reach a successful terminal state, emit logs, and
produce the expected F-drive artifact evidence.
