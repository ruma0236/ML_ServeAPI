from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from evm.control_panel.admission_queue import (
    QUEUE_ADMISSIONS,
    QUEUE_INGRESS_BODY_BYTES,
    load_admission_queue_config,
)


ASGIApp = Callable[[dict[str, Any], Callable[..., Awaitable[dict]], Callable[..., Awaitable[None]]], Awaitable[None]]


class TaskIngressBodyLimitMiddleware:
    """Bound task JSON before FastAPI materializes it, including chunked bodies."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if (
            scope.get("type") != "http"
            or scope.get("method") != "POST"
            or scope.get("path") != "/control-panel/v1/tasks"
        ):
            await self.app(scope, receive, send)
            return
        limit = load_admission_queue_config().ingress_max_body_bytes
        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        content_length = headers.get("content-length")
        if content_length is not None:
            try:
                declared = int(content_length)
            except ValueError:
                await self._reject(send, status=400, reason="invalid_content_length", observed=0, limit=limit)
                return
            if declared < 0:
                await self._reject(send, status=400, reason="invalid_content_length", observed=0, limit=limit)
                return
            if declared > limit:
                await self._reject(
                    send,
                    status=413,
                    reason="ingress_body_limit",
                    observed=declared,
                    limit=limit,
                )
                return

        messages: list[dict[str, Any]] = []
        observed = 0
        while True:
            message = await receive()
            messages.append(message)
            if message.get("type") == "http.disconnect":
                break
            body = message.get("body", b"")
            observed += len(body)
            if observed > limit:
                await self._reject(
                    send,
                    status=413,
                    reason="ingress_body_limit",
                    observed=observed,
                    limit=limit,
                )
                return
            if not message.get("more_body", False):
                break
        QUEUE_INGRESS_BODY_BYTES.observe(observed)

        async def replay_receive() -> dict[str, Any]:
            if messages:
                return messages.pop(0)
            return {"type": "http.disconnect"}

        await self.app(scope, replay_receive, send)

    @staticmethod
    async def _reject(
        send,
        *,
        status: int,
        reason: str,
        observed: int,
        limit: int,
    ) -> None:
        QUEUE_INGRESS_BODY_BYTES.observe(observed)
        QUEUE_ADMISSIONS.labels(outcome="rejected", reason=reason).inc()
        body = json.dumps(
            {
                "detail": {
                    "error": reason,
                    "message": "Task request body exceeded the bounded ingress contract.",
                    "body_bytes": observed,
                    "max_body_bytes": limit,
                }
            },
            separators=(",", ":"),
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
