from __future__ import annotations

import argparse
import json

from evm.control_panel.operations import TaskDispatchError, dispatch_queued_task_assignment
from evm.control_panel.transactional_store import ControlPlaneStoreError


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Execute one claimed durable task.")
    parser.add_argument("--task-id", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        task = dispatch_queued_task_assignment(args.task_id)
        if task is None:
            print(json.dumps({"outcome": "permanent", "failure_class": "task_missing"}))
            raise SystemExit(65)
        print(
            json.dumps(
                {
                    "outcome": "completed",
                    "task_status": task.status,
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


if __name__ == "__main__":
    main()
