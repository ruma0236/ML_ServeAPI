# 2026-07 Enterprise MLOps MVP Release Note

## Scope

This release turns the local enterprise MLOps control-plane into a VLM-first
manufacturing visual inspection workflow.

## Included

- F-drive backed large data and artifact storage policy.
- Manufacturing domain pack validation.
- Image quality validation with duplicate hash, readability, dimension,
  brightness, blur, label, split, and drift-proxy evidence.
- Deterministic dataset shard/split builder.
- Mock VLM adapter contract and multimodal request router.
- Manifest-based batch VLM evaluation with JSONL outputs.
- Structured VLM response schema validation.
- Prompt/model registry and promotion gate.
- Intentionally bad prompt candidate blocked by regression gate.
- Audit event JSONL and RCA/failure scenario suite.
- VLM benchmark, SLO, and Prometheus-style metric export.
- API `/metrics` exposure for latest VLM observability artifacts.
- Grafana dashboard panels for VLM schema validity, quality errors, and audit
  events.
- GitHub Actions CI smoke workflow.
- Final demo script.

## Current Validation Snapshot

- Quality fatal errors: `0`.
- Quality warnings: `16`, caused by placeholder seed-image header dimension
  fallback and duplicated synthetic content hash.
- Dataset records: `8`.
- Dataset shards: `3`.
- VLM batch records: `8`.
- VLM schema valid rate: `1.0`.
- VLM p95 latency: `8.904 ms`.
- Promotion decision: `promote_candidate`.
- Bad prompt candidate: blocked.
- Audit events: `23`.
- Failure scenarios: `4`.

## Known Limits

- The current VLM adapter is a local mock adapter.
- Real VisA/MVTec dataset import still requires manual/license-controlled data
  acquisition.
- Placeholder seed images intentionally produce duplicate hash warnings.
- Local Python 3.14 may use JSONL fallback at configured Parquet paths if
  `pyarrow` wheels are unavailable; Docker/CI Python 3.11 keeps the Parquet
  path.
