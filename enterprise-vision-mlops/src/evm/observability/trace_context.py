from __future__ import annotations

import hashlib
import re
import secrets
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Mapping


TRACE_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
SPAN_ID_PATTERN = re.compile(r"^[0-9a-f]{16}$")
TRACE_FLAGS_PATTERN = re.compile(r"^[0-9a-f]{2}$")
TRACEPARENT_PATTERN = re.compile(
    r"^(?P<version>[0-9a-f]{2})-"
    r"(?P<trace_id>[0-9a-f]{32})-"
    r"(?P<span_id>[0-9a-f]{16})-"
    r"(?P<trace_flags>[0-9a-f]{2})$"
)
MAX_TRACESTATE_LENGTH = 512


class TraceContextError(ValueError):
    """Raised when inbound trace context is unsafe or non-conformant."""


def _random_non_zero_hex(byte_count: int) -> str:
    value = "0" * (byte_count * 2)
    while int(value, 16) == 0:
        value = secrets.token_hex(byte_count)
    return value


def normalize_legacy_trace_id(value: str) -> str:
    candidate = value.strip().lower()
    if TRACE_ID_PATTERN.fullmatch(candidate) and int(candidate, 16) != 0:
        return candidate
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


def validate_tracestate(value: str | None) -> str | None:
    if value is None:
        return None
    candidate = value.strip()
    if not candidate:
        return None
    if len(candidate) > MAX_TRACESTATE_LENGTH or "\n" in candidate or "\r" in candidate:
        raise TraceContextError("invalid_tracestate")
    return candidate


@dataclass(frozen=True)
class W3CTraceContext:
    trace_id: str
    span_id: str
    trace_flags: str = "01"
    tracestate: str | None = None
    parent_span_id: str | None = None
    version: str = "00"

    def __post_init__(self) -> None:
        if self.version != "00":
            raise TraceContextError("unsupported_traceparent_version")
        if not TRACE_ID_PATTERN.fullmatch(self.trace_id) or int(self.trace_id, 16) == 0:
            raise TraceContextError("invalid_trace_id")
        if not SPAN_ID_PATTERN.fullmatch(self.span_id) or int(self.span_id, 16) == 0:
            raise TraceContextError("invalid_span_id")
        if not TRACE_FLAGS_PATTERN.fullmatch(self.trace_flags):
            raise TraceContextError("invalid_trace_flags")
        if self.parent_span_id is not None and (
            not SPAN_ID_PATTERN.fullmatch(self.parent_span_id)
            or int(self.parent_span_id, 16) == 0
        ):
            raise TraceContextError("invalid_parent_span_id")
        object.__setattr__(self, "tracestate", validate_tracestate(self.tracestate))

    @property
    def traceparent(self) -> str:
        return f"{self.version}-{self.trace_id}-{self.span_id}-{self.trace_flags}"

    @property
    def sampled(self) -> bool:
        return bool(int(self.trace_flags, 16) & 1)

    @classmethod
    def new_root(
        cls,
        *,
        sampled: bool = True,
        trace_id: str | None = None,
        tracestate: str | None = None,
    ) -> "W3CTraceContext":
        return cls(
            trace_id=trace_id or _random_non_zero_hex(16),
            span_id=_random_non_zero_hex(8),
            trace_flags="01" if sampled else "00",
            tracestate=tracestate,
        )

    @classmethod
    def parse(
        cls,
        traceparent: str,
        *,
        tracestate: str | None = None,
    ) -> "W3CTraceContext":
        candidate = traceparent.strip().lower()
        match = TRACEPARENT_PATTERN.fullmatch(candidate)
        if match is None:
            raise TraceContextError("invalid_traceparent_format")
        values = match.groupdict()
        if values["version"] == "ff":
            raise TraceContextError("invalid_traceparent_version")
        if values["version"] != "00":
            raise TraceContextError("unsupported_traceparent_version")
        return cls(
            version=values["version"],
            trace_id=values["trace_id"],
            span_id=values["span_id"],
            trace_flags=values["trace_flags"],
            tracestate=tracestate,
        )

    @classmethod
    def from_headers(cls, headers: Mapping[str, str]) -> "W3CTraceContext | None":
        traceparent = headers.get("traceparent")
        if not traceparent:
            return None
        return cls.parse(traceparent, tracestate=headers.get("tracestate"))

    def child(self) -> "W3CTraceContext":
        return W3CTraceContext(
            trace_id=self.trace_id,
            span_id=_random_non_zero_hex(8),
            trace_flags=self.trace_flags,
            tracestate=self.tracestate,
            parent_span_id=self.span_id,
        )

    def headers(self) -> dict[str, str]:
        headers = {"traceparent": self.traceparent}
        if self.tracestate:
            headers["tracestate"] = self.tracestate
        return headers


_CURRENT_TRACE_CONTEXT: ContextVar[W3CTraceContext | None] = ContextVar(
    "evm_current_trace_context",
    default=None,
)


def current_trace_context() -> W3CTraceContext | None:
    return _CURRENT_TRACE_CONTEXT.get()


def bind_trace_context(context: W3CTraceContext) -> Token[W3CTraceContext | None]:
    return _CURRENT_TRACE_CONTEXT.set(context)


def reset_trace_context(token: Token[W3CTraceContext | None]) -> None:
    _CURRENT_TRACE_CONTEXT.reset(token)
