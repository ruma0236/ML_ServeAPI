.PHONY: stack-up stack-down stack-logs stack-ps stack-config api-logs mlflow-logs clean-volumes object-store-bootstrap data-ingest data-validate train register-model deploy-check monitor-check remote-inventory mac-mini-smoke local-mvp

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

object-store-bootstrap:
	python scripts/run_pipeline.py object-store-bootstrap --config configs/local.toml

data-ingest:
	python scripts/run_pipeline.py data-ingest --config configs/local.toml

data-validate:
	python scripts/run_pipeline.py data-validate --config configs/local.toml

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

mac-mini-smoke:
	bash infra/remote-workers/mac-mini/run_mac_worker_smoke.sh

local-mvp: object-store-bootstrap data-ingest data-validate train register-model deploy-check monitor-check remote-inventory
