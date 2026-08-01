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

## Injection Modes

### Default: staging Pod restart

Delete one Pod selected by
`app.kubernetes.io/name=evm-b7-serving`. The Deployment controller recreates
it. The executor must capture the exact Pod UID and command before execution.

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
unapproved resource changes occur, the exact expected model returns real CUDA
inference, and Prometheus returns `up`. All evidence validates under the shared
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
