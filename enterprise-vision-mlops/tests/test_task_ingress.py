from __future__ import annotations

import asyncio

from apps.api.task_ingress import TaskIngressBodyLimitMiddleware


async def invoke(body_parts: list[bytes], *, content_length: int | None = None):
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
        "path": "/control-panel/v1/tasks",
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
