from __future__ import annotations

import asyncio
from dataclasses import replace

from apps.api import task_ingress
from apps.api.task_ingress import TaskIngressBodyLimitMiddleware
from evm.control_panel.admission_queue import load_admission_queue_config


async def invoke(
    body_parts: list[bytes],
    *,
    content_length: int | None = None,
    path: str = "/control-panel/v1/tasks",
):
    observed: dict[str, bytes] = {}

    async def app(_scope, receive, send):
        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body", False):
                break
        observed["body"] = body
        await send({"type": "http.response.start", "status": 202, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    headers = []
    if content_length is not None:
        headers.append((b"content-length", str(content_length).encode("ascii")))
    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": headers,
    }
    messages = [
        {
            "type": "http.request",
            "body": part,
            "more_body": index < len(body_parts) - 1,
        }
        for index, part in enumerate(body_parts)
    ]
    sent = []

    async def receive():
        return messages.pop(0)

    async def send(message):
        sent.append(message)

    await TaskIngressBodyLimitMiddleware(app)(scope, receive, send)
    return sent, observed


def test_task_ingress_rejects_declared_oversize_before_body_materialization():
    sent, observed = asyncio.run(invoke([b"{}"], content_length=300_000))

    assert sent[0]["status"] == 413
    assert observed == {}


def test_task_ingress_bounds_chunked_body_without_content_length():
    sent, observed = asyncio.run(invoke([b"x" * 150_000, b"x" * 150_000]))

    assert sent[0]["status"] == 413
    assert observed == {}


def test_task_ingress_replays_bounded_body_to_fastapi():
    sent, observed = asyncio.run(invoke([b'{"a":', b"1}"]))

    assert sent[0]["status"] == 202
    assert observed["body"] == b'{"a":1}'


def test_task_ingress_applies_the_same_bound_to_confirm_mutations():
    sent, observed = asyncio.run(
        invoke(
            [b"{}"],
            content_length=300_000,
            path="/control-panel/v1/tasks/task-1/confirm",
        )
    )

    assert sent[0]["status"] == 413
    assert observed == {}


def test_task_ingress_rejects_concurrent_aggregate_pressure(monkeypatch):
    active = replace(
        load_admission_queue_config(),
        ingress_max_concurrent_requests=1,
        ingress_max_inflight_bytes=16,
        ingress_max_body_bytes=16,
        max_item_bytes=16,
    )
    monkeypatch.setattr(task_ingress, "load_admission_queue_config", lambda: active)

    async def exercise():
        entered = asyncio.Event()
        release = asyncio.Event()

        async def app(_scope, receive, send):
            entered.set()
            await release.wait()
            await receive()
            await send({"type": "http.response.start", "status": 202, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        middleware = TaskIngressBodyLimitMiddleware(app)

        async def request():
            sent = []
            delivered = False

            async def receive():
                nonlocal delivered
                if delivered:
                    return {"type": "http.disconnect"}
                delivered = True
                return {"type": "http.request", "body": b"{}", "more_body": False}

            async def send(message):
                sent.append(message)

            await middleware(
                {
                    "type": "http",
                    "method": "POST",
                    "path": "/control-panel/v1/tasks",
                    "headers": [(b"content-length", b"2")],
                },
                receive,
                send,
            )
            return sent

        first = asyncio.create_task(request())
        await entered.wait()
        second = await request()
        release.set()
        first_result = await first
        return first_result, second

    first, second = asyncio.run(exercise())

    assert first[0]["status"] == 202
    assert second[0]["status"] == 429
    assert (b"retry-after", str(active.retry_after_seconds).encode("ascii")) in second[0][
        "headers"
    ]
