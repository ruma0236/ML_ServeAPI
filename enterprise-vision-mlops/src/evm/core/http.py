from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from evm.observability.otel import trace_span


def request_json(
    method: str,
    url: str,
    payload: dict[str, Any] | None = None,
    timeout: int = 5,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, Any] | list[Any] | str]:
    parsed_url = urllib.parse.urlsplit(url)
    with trace_span(
        f"HTTP {method.upper()}",
        kind="client",
        attributes={
            "http.request.method": method.upper(),
            "server.address": parsed_url.hostname or "unknown",
            "url.path": parsed_url.path,
        },
    ) as active:
        data = None
        request_headers = {"Content-Type": "application/json", **(headers or {})}
        request_headers.update(active.context.headers())
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=data,
            headers=request_headers,
            method=method.upper(),
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                body = response.read().decode("utf-8")
                active.set_attribute("http.response.status_code", response.status)
                if not body:
                    return response.status, ""
                try:
                    return response.status, json.loads(body)
                except json.JSONDecodeError:
                    return response.status, body
        except urllib.error.HTTPError as exc:
            active.set_attribute("http.response.status_code", exc.code)
            body = exc.read().decode("utf-8")
            try:
                return exc.code, json.loads(body)
            except json.JSONDecodeError:
                return exc.code, body
        except urllib.error.URLError as exc:
            active.set_attribute("error.type", type(exc.reason).__name__)
            return 0, str(exc.reason)
