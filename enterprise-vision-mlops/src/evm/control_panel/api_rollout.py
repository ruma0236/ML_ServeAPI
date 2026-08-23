from __future__ import annotations

import argparse
import json
import os
import threading
import time
import urllib.request
from dataclasses import asdict, dataclass
from typing import Final

from prometheus_client import Counter, Gauge, Histogram


API_DRAINING = Gauge(
    "evm_api_runtime_draining",
    "Whether this API replica has stopped accepting non-probe requests.",
)
API_DRAIN_REJECTIONS = Counter(
    "evm_api_runtime_drain_rejections_total",
    "Requests rejected after this API replica entered its drain phase.",
)
API_DRAIN_SECONDS = Histogram(
    "evm_api_runtime_drain_seconds",
    "Seconds required for an API replica to drain accepted in-flight requests.",
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 30),
)

_EXEMPT_PATHS: Final[frozenset[str]] = frozenset(
    {
        "/health",
        "/ready",
        "/metrics",
        "/control-panel/v1/runtime/status",
        "/control-panel/v1/runtime/drain",
    }
)


class ApiDrainingError(RuntimeError):
    pass


@dataclass(frozen=True)
class ApiDrainSnapshot:
    state: str
    draining: bool
    in_flight: int
    peak_in_flight: int
    reason: str | None
    started_at_monotonic: float | None
    release_id: str
    instance_id: str
    source_revision: str

    def public_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload.pop("started_at_monotonic", None)
        return payload


class ApiDrainController:
    """Process-local admission and drain state for a stateless API replica."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._draining = False
        self._in_flight = 0
        self._peak_in_flight = 0
        self._reason: str | None = None
        self._started_at_monotonic: float | None = None

    def enter(self, path: str) -> bool:
        if path in _EXEMPT_PATHS:
            return False
        with self._condition:
            if self._draining:
                API_DRAIN_REJECTIONS.inc()
                raise ApiDrainingError("api_replica_draining")
            self._in_flight += 1
            self._peak_in_flight = max(self._peak_in_flight, self._in_flight)
            return True

    def exit(self, counted: bool) -> None:
        if not counted:
            return
        with self._condition:
            self._in_flight = max(0, self._in_flight - 1)
            self._condition.notify_all()

    def begin_drain(self, reason: str) -> ApiDrainSnapshot:
        normalized = reason.strip() or "kubernetes_pre_stop"
        with self._condition:
            if not self._draining:
                self._draining = True
                self._reason = normalized
                self._started_at_monotonic = time.monotonic()
                API_DRAINING.set(1)
            return self.snapshot_locked()

    def wait_until_drained(self, timeout_seconds: float) -> tuple[ApiDrainSnapshot, float]:
        started = time.monotonic()
        deadline = started + max(0.0, timeout_seconds)
        with self._condition:
            while self._in_flight > 0:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._condition.wait(timeout=remaining)
            elapsed = time.monotonic() - started
            snapshot = self.snapshot_locked()
        API_DRAIN_SECONDS.observe(elapsed)
        return snapshot, elapsed

    def snapshot(self) -> ApiDrainSnapshot:
        with self._condition:
            return self.snapshot_locked()

    def snapshot_locked(self) -> ApiDrainSnapshot:
        return ApiDrainSnapshot(
            state=(
                "accepting"
                if not self._draining
                else "drained"
                if self._in_flight == 0
                else "draining"
            ),
            draining=self._draining,
            in_flight=self._in_flight,
            peak_in_flight=self._peak_in_flight,
            reason=self._reason,
            started_at_monotonic=self._started_at_monotonic,
            release_id=os.getenv("EVM_API_RELEASE_ID", "unknown"),
            instance_id=(
                os.getenv("EVM_POD_UID")
                or os.getenv("HOSTNAME")
                or "unknown"
            ),
            source_revision=(
                os.getenv("EVM_IMAGE_SOURCE_REVISION")
                or os.getenv("GIT_COMMIT")
                or os.getenv("EVM_GIT_COMMIT")
                or "unknown"
            ),
        )

    def reset_for_test(self) -> None:
        with self._condition:
            self._draining = False
            self._in_flight = 0
            self._peak_in_flight = 0
            self._reason = None
            self._started_at_monotonic = None
            API_DRAINING.set(0)


API_DRAIN_CONTROLLER = ApiDrainController()


def request_local_drain(url: str, timeout_seconds: float) -> int:
    payload = json.dumps(
        {"reason": "kubernetes_pre_stop", "timeout_seconds": timeout_seconds}
    ).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={"content-type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds + 2) as response:
        result = json.loads(response.read().decode("utf-8"))
    return 0 if result.get("state") == "drained" else 2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Drain one local API replica")
    parser.add_argument(
        "--url",
        default="http://127.0.0.1:8000/control-panel/v1/runtime/drain",
    )
    parser.add_argument("--timeout-seconds", type=float, default=20.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return request_local_drain(args.url, args.timeout_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
