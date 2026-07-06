.PHONY: stack-up stack-down stack-logs stack-ps stack-config api-logs mlflow-logs clean-volumes domain-pack-check dataset-intake-audit object-store-bootstrap data-ingest data-validate image-quality dataset-shards vlm-contract vlm-batch-eval vlm-reliability vlm-rca vlm-observability train register-model deploy-check monitor-check remote-inventory remote-job mac-mini-smoke local-mvp w4-vlm

stack-up:
	docker compose up -d --build

stack-down:
	docker compose down

stack-logs:
	docker compose logs -f

stack-ps:
	docker compose ps

stack-config:
	docker compose config

api-logs:
	docker compose logs -f api

mlflow-logs:
	docker compose logs -f mlflow

clean-volumes:
	docker compose down -v

domain-pack-check:
	python scripts/run_pipeline.py domain-pack-check --config configs/local.toml

dataset-intake-audit:
	python scripts/run_pipeline.py dataset-intake-audit --config configs/local.toml

object-store-bootstrap:
	python scripts/run_pipeline.py object-store-bootstrap --config configs/local.toml

data-ingest:
	python scripts/run_pipeline.py data-ingest --config configs/local.toml

data-validate:
	python scripts/run_pipeline.py data-validate --config configs/local.toml

image-quality:
	python scripts/run_pipeline.py image-quality --config configs/local.toml

dataset-shards:
	python scripts/run_pipeline.py dataset-shards --config configs/local.toml

vlm-contract:
	python scripts/run_pipeline.py vlm-contract --config configs/local.toml

vlm-batch-eval:
	python scripts/run_pipeline.py vlm-batch-eval --config configs/local.toml

vlm-reliability:
	python scripts/run_pipeline.py vlm-reliability --config configs/local.toml

vlm-rca:
	python scripts/run_pipeline.py vlm-rca --config configs/local.toml

vlm-observability:
	python scripts/run_pipeline.py vlm-observability --config configs/local.toml

train:
	python scripts/run_pipeline.py train --config configs/local.toml

register-model:
	python scripts/run_pipeline.py register-model --config configs/local.toml

deploy-check:
	python scripts/run_pipeline.py deploy-check --config configs/local.toml

monitor-check:
	python scripts/run_pipeline.py monitor-check --config configs/local.toml

remote-inventory:
	python scripts/run_pipeline.py remote-inventory --config configs/local.toml

remote-job:
	python scripts/run_pipeline.py remote-job --config configs/local.toml

mac-mini-smoke:
	bash infra/remote-workers/mac-mini/run_mac_worker_smoke.sh

w4-vlm: dataset-intake-audit domain-pack-check object-store-bootstrap data-ingest data-validate image-quality dataset-shards vlm-contract vlm-batch-eval vlm-reliability vlm-rca vlm-observability

local-mvp: w4-vlm train register-model deploy-check monitor-check remote-inventory remote-job
