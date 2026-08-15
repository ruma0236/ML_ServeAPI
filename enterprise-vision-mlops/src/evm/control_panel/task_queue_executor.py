from __future__ import annotations

import argparse
import json

from evm.control_panel.operations import TaskDispatchError, dispatch_queued_task_assignment
from evm.control_panel.transactional_store import (
    ControlPlaneStoreError,
    get_transactional_store,
)
from evm.observability.otel import configure_tracing, runtime_service_version, shutdown_tracing


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Execute one claimed durable task.")
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--queue-id", required=True)
    parser.add_argument("--lease-owner", required=True)
    parser.add_argument("--lease-epoch", required=True, type=int)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    configure_tracing("evm-task-queue-executor", service_version=runtime_service_version())
    try:
        store = get_transactional_store()
        lease = store.load_task_queue_lease(
            queue_id=args.queue_id,
            task_id=args.task_id,
            lease_owner=args.lease_owner,
            lease_epoch=args.lease_epoch,
        )
        with store.bind_task_queue_lease(lease):
            task = dispatch_queued_task_assignment(args.task_id)
        if task is None:
            print(json.dumps({"outcome": "permanent", "failure_class": "task_missing"}))
            raise SystemExit(65)
        queue_item = store.get_task_queue_item(queue_id=args.queue_id)
        print(
            json.dumps(
                {
                    "outcome": "completed",
                    "task_status": task.status,
                    "queue_state": queue_item["state"] if queue_item else "missing",
                    "runtime_state": task.runtime_state,
                    "runtime_id": task.runtime_id,
                }
            )
        )
    except TaskDispatchError as exc:
        print(
            json.dumps(
                {
                    "outcome": "transient" if exc.retryable else "permanent",
                    "failure_class": exc.code,
                }
            )
        )
        raise SystemExit(75 if exc.retryable else 65) from exc
    except ControlPlaneStoreError as exc:
        print(
            json.dumps(
                {
                    "outcome": "transient",
                    "failure_class": type(exc).__name__,
                }
            )
        )
        raise SystemExit(75) from exc
    finally:
        shutdown_tracing()


if __name__ == "__main__":
    main()
