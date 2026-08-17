from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

from evm.control_panel.admission_queue import (
    QUEUE_ADMISSIONS,
    QUEUE_INGRESS_BODY_BYTES,
    QUEUE_INGRESS_IN_FLIGHT_BYTES,
    QUEUE_INGRESS_IN_FLIGHT_REQUESTS,
    QUEUE_INGRESS_PEAK_BYTES,
    QUEUE_INGRESS_PEAK_REQUESTS,
    AdmissionQueueConfig,
    load_admission_queue_config,
)


ASGIApp = Callable[
    [
        dict[str, Any],
        Callable[..., Awaitable[dict]],
        Callable[..., Awaitable[None]],
    ],
    Awaitable[None],
]


class TaskIngressBodyLimitMiddleware:
    """Bound task mutation bodies before FastAPI materializes their JSON."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self._lock = asyncio.Lock()
        self._in_flight_requests = 0
        self._in_flight_bytes = 0
        self._peak_requests = 0
        self._peak_bytes = 0

    async def __call__(self, scope, receive, send) -> None:
        if not self._is_bounded_task_mutation(scope):
            await self.app(scope, receive, send)
            return

        config = load_admission_queue_config()
        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        content_length = headers.get("content-length")
        declared = 0
        if content_length is not None:
            try:
                declared = int(content_length)
            except ValueError:
                await self._reject(
                    send,
                    status=400,
                    reason="invalid_content_length",
                    observed=0,
                    limit=config.ingress_max_body_bytes,
                )
                return
            if declared < 0:
                await self._reject(
                    send,
                    status=400,
                    reason="invalid_content_length",
                    observed=0,
                    limit=config.ingress_max_body_bytes,
                )
                return
            if declared > config.ingress_max_body_bytes:
                await self._reject(
                    send,
                    status=413,
                    reason="ingress_body_limit",
                    observed=declared,
                    limit=config.ingress_max_body_bytes,
                )
                return

        if not await self._reserve(config, declared):
            await self._reject(
                send,
                status=429,
                reason="ingress_aggregate_limit",
                observed=declared,
                limit=config.ingress_max_inflight_bytes,
                retry_after=config.retry_after_seconds,
            )
            return

        reserved_bytes = declared
        messages: list[dict[str, Any]] = []
        observed = 0
        chunks = 0
        try:
            while True:
                message = await receive()
                messages.append(message)
                if message.get("type") == "http.disconnect":
                    break
                chunks += 1
                if chunks > config.ingress_max_chunks:
                    await self._reject(
                        send,
                        status=413,
                        reason="ingress_chunk_limit",
                        observed=chunks,
                        limit=config.ingress_max_chunks,
                    )
                    return
                body = message.get("body", b"")
                observed += len(body)
                if observed > config.ingress_max_body_bytes:
                    await self._reject(
                        send,
                        status=413,
                        reason="ingress_body_limit",
                        observed=observed,
                        limit=config.ingress_max_body_bytes,
                    )
                    return
                additional = max(0, observed - reserved_bytes)
                if additional:
                    if not await self._reserve_bytes(
                        additional,
                        config.ingress_max_inflight_bytes,
                    ):
                        await self._reject(
                            send,
                            status=429,
                            reason="ingress_aggregate_limit",
                            observed=observed,
                            limit=config.ingress_max_inflight_bytes,
                            retry_after=config.retry_after_seconds,
                        )
                        return
                    reserved_bytes += additional
                if not message.get("more_body", False):
                    break
            QUEUE_INGRESS_BODY_BYTES.observe(observed)

            async def replay_receive() -> dict[str, Any]:
                if messages:
                    return messages.pop(0)
                return {"type": "http.disconnect"}

            await self.app(scope, replay_receive, send)
        finally:
            await self._release(reserved_bytes)

    @staticmethod
    def _is_bounded_task_mutation(scope: dict[str, Any]) -> bool:
        if scope.get("type") != "http" or scope.get("method") != "POST":
            return False
        path = str(scope.get("path") or "")
        return path == "/control-panel/v1/tasks" or (
            path.startswith("/control-panel/v1/tasks/") and path.endswith("/confirm")
        )

    async def _reserve(self, config: AdmissionQueueConfig, body_bytes: int) -> bool:
        async with self._lock:
            if self._in_flight_requests + 1 > config.ingress_max_concurrent_requests:
                return False
            if self._in_flight_bytes + body_bytes > config.ingress_max_inflight_bytes:
                return False
            self._in_flight_requests += 1
            self._in_flight_bytes += body_bytes
            self._observe_in_flight()
            return True

    async def _reserve_bytes(self, body_bytes: int, limit: int) -> bool:
        async with self._lock:
            if self._in_flight_bytes + body_bytes > limit:
                return False
            self._in_flight_bytes += body_bytes
            self._observe_in_flight()
            return True

    async def _release(self, body_bytes: int) -> None:
        async with self._lock:
            self._in_flight_requests = max(0, self._in_flight_requests - 1)
            self._in_flight_bytes = max(0, self._in_flight_bytes - body_bytes)
            self._observe_in_flight()

    def _observe_in_flight(self) -> None:
        self._peak_requests = max(self._peak_requests, self._in_flight_requests)
        self._peak_bytes = max(self._peak_bytes, self._in_flight_bytes)
        QUEUE_INGRESS_IN_FLIGHT_REQUESTS.set(self._in_flight_requests)
        QUEUE_INGRESS_IN_FLIGHT_BYTES.set(self._in_flight_bytes)
        QUEUE_INGRESS_PEAK_REQUESTS.set(self._peak_requests)
        QUEUE_INGRESS_PEAK_BYTES.set(self._peak_bytes)

    @staticmethod
    async def _reject(
        send,
        *,
        status: int,
        reason: str,
        observed: int,
        limit: int,
        retry_after: int | None = None,
    ) -> None:
        QUEUE_INGRESS_BODY_BYTES.observe(observed)
        QUEUE_ADMISSIONS.labels(outcome="rejected", reason=reason).inc()
        body = json.dumps(
            {
                "detail": {
                    "error": reason,
                    "message": "Task mutation exceeded the bounded ingress contract.",
                    "observed": observed,
                    "limit": limit,
                }
            },
            separators=(",", ":"),
        ).encode("utf-8")
        response_headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
        ]
        if retry_after is not None:
            response_headers.append(
                (b"retry-after", str(int(retry_after)).encode("ascii"))
            )
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": response_headers,
            }
        )
        await send({"type": "http.response.body", "body": body})
