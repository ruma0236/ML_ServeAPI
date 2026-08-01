# Scenario A: GPU and Serving Failure

Issue: `EVM-266 / SCRUM-172`
State: contract defined; implementation and execution not started.

## Purpose

Validate detection and recovery for GPU capacity, NVIDIA device-plugin,
staging serving Pod, inference endpoint, and Prometheus target failures.

## Preconditions

- Git, API, worker and observer revisions match.
- Node reports GPU capacity and allocatable `1/1`.
- Device-plugin and selected serving Deployment are Ready.
- Known-good model, image and rollback digests are immutable.
- Before/after VisA request and Prometheus target snapshots are writable.

## Baseline Checks

```powershell
git rev-parse HEAD
kubectl get node docker-desktop `
  -o jsonpath="{.status.capacity.nvidia\.com/gpu}|{.status.allocatable.nvidia\.com/gpu}"
kubectl get daemonset,pod -n kube-system -l app=nvidia-device-plugin
kubectl rollout status deployment/evm-b7-serving -n evm-staging --timeout=300s
Invoke-RestMethod http://127.0.0.1:9090/api/v1/targets
```

Baseline is blocked if staging cannot run without evicting or mutating the
known-good production model. Single-GPU contention must be recorded, not hidden.

## Current Read-only Preflight

On 2026-08-01, GPU and device-plugin are `1/1`, production B0 is `1/1 Ready`,
CUDA readiness and Prometheus pass, and staging B7 is intentionally `0/0`.
Production owns the only GPU, so there is no no-impact live GPU-serving fault
injection on this machine.

Git is at documentation checkpoint `be41fb6`, while the healthy runtime still
reports executable baseline `1c6e908`. This is reconciled as a docs-only delta
for planning. After Scenario A implementation changes code, exact runtime
revision convergence is mandatory before injection.

The observer aggregate currently includes historical terminal failed Jobs and
replaced Pods. Admission uses target-scoped active health and preserves those
failures as historical context instead of treating the aggregate as proof that
the active production Deployment is down.

## Injection Modes

### Preferred live mode: production Pod restart

During an approved local maintenance window, delete the one production B0 Pod
selected by `app.kubernetes.io/name=evm-b0-production`. The Deployment
controller recreates the same specification and model identity. The endpoint
can be briefly unavailable because replicas equal one; this interruption is
measured and is not described as zero-downtime recovery.

### Alternative: production-to-staging handover

Capture production identity, scale production to zero, scale B7 staging to one,
establish a healthy baseline, restart one staging Pod, then restore the exact
production state. This has a larger mutation surface and is not preferred.
Both live modes require explicit approval because they change the only active
GPU serving path.

### Non-disruptive plugin reconciliation

Feed an isolated stale-hostPath manifest fixture to the discovery/reconcile
planner and assert the proposed current WSL driver path. Do not patch the live
DaemonSet.

### Approval-required plugin outage

Changing the live device-plugin hostPath affects the only cluster GPU and may
interrupt production serving. It requires explicit user approval and a stated
maintenance window. Without both, result `blocked` is correct.

## Signals

- Kubernetes Deployment/Pod/DaemonSet state and events;
- node GPU capacity/allocatable;
- readiness/liveness and serving logs;
- inference HTTP result, latency, device and model digest;
- Prometheus target health and scrape error;
- observer and supervisor heartbeats.

## Success

Detection is at most 30 seconds, serving recovery at most 300 seconds, no
unapproved resource or Deployment specification changes occur, the exact
expected model returns real CUDA inference, and Prometheus returns `up`. Any
single-replica outage is measured. All evidence validates under the shared
contract.

## Rollback

Stop injection, restore the captured known-good DaemonSet/Deployment spec and
approved model identity, wait for Ready, run real inference, and verify the
Prometheus target. A different model digest is a failed rollback even when the
endpoint is healthy.

## Interview Evidence

- Demonstrates: GPU scheduling, Kubernetes reconciliation, observability and RTO.
- Expected questions: GPU capacity versus allocatable; probe versus metric;
  controller recovery versus custom supervisor; exact identity verification.
- Claim allowed: bounded local single-node recovery with real CUDA.
- Claim prohibited: multi-node GPU HA or production SLA.
