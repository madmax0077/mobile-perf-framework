"""HAR (HTTP Archive) parsing for API response times, failures and backend attribution.

Many mobile backends put every call behind one gateway URL (mPaaS ``mgw.htm``,
GraphQL ``/graphql``, JSON-RPC), so the URL alone does not say which backend
method was slow. ``operation`` is read from, in order: request headers
(``Operation-Type``...), the form / JSON body (``operationType``, ``operationName``,
JSON-RPC ``method``) and the query string. Bodies are never stored; only a value that
looks like a method identifier is kept.
"""

from __future__ import annotations

import contextlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from models.telemetry_model import NetworkRequest

OPERATION_HEADERS = (
    "operation-type",
    "x-operation-type",
    "x-operation-name",
    "x-apollo-operation-name",
    "x-graphql-operation-name",
    "x-amz-target",
    "soapaction",
)
OPERATION_FIELDS = ("operationType", "operationName", "operation")
SERVER_TIME_HEADERS = ("x-envoy-upstream-service-time", "x-upstream-service-time", "x-response-time")
TRACE_HEADERS = (
    "mgw-traceid",
    "traceparent",
    "x-b3-traceid",
    "x-trace-id",
    "x-request-id",
    "x-correlation-id",
    "x-amzn-trace-id",
    "x-cloud-trace-context",
    "uber-trace-id",
)
PHASES = ("blocked", "dns", "connect", "ssl", "send", "wait", "receive")
_IDENTIFIER = re.compile(r"^[\w.\-:/#$]{2,120}$")
_DURATION = re.compile(r"dur=([\d.]+)")
_MS = re.compile(r"^\s*([\d.]+)\s*(ms)?\s*$")


def load_har(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _headers(items: Any) -> dict[str, str]:
    return {
        str(h.get("name", "")).lower(): str(h.get("value", "")) for h in items or [] if isinstance(h, dict)
    }


def _identifier(value: Any) -> str | None:
    text = str(value or "").strip().strip('"')
    return text if _IDENTIFIER.match(text) else None


def _from_mapping(values: dict[str, Any]) -> str | None:
    lowered = {str(k).lower(): v for k, v in values.items()}
    for field in OPERATION_FIELDS:
        value = lowered.get(field.lower())
        if isinstance(value, list):
            value = value[0] if value else None
        if (found := _identifier(value)) is not None:
            return found
    return None


def _body_operation(post: dict[str, Any]) -> str | None:
    params = post.get("params")
    if isinstance(params, list) and params:
        found = _from_mapping({p.get("name"): p.get("value") for p in params if isinstance(p, dict)})
        if found:
            return found
    text = post.get("text")
    if not isinstance(text, str) or not text.strip():
        return None
    mime = str(post.get("mimeType", "")).lower()
    if "json" in mime or text.lstrip().startswith(("{", "[")):
        with contextlib.suppress(ValueError):
            body = json.loads(text)
            if isinstance(body, list) and body:
                body = body[0]
            if isinstance(body, dict):
                if "jsonrpc" in body:
                    return _identifier(body.get("method"))
                return _from_mapping(body)
        return None
    if "form" in mime or "=" in text:
        return _from_mapping(parse_qs(text[:20_000]))
    return None


def request_operation(request: dict[str, Any]) -> str | None:
    """Backend method name of a HAR request, if the request carries one."""
    headers = _headers(request.get("headers"))
    for name in OPERATION_HEADERS:
        if (found := _identifier(headers.get(name))) is not None:
            return found
    post = request.get("postData")
    if isinstance(post, dict) and (found := _body_operation(post)):
        return found
    query = request.get("queryString")
    if isinstance(query, list) and query:
        return _from_mapping({q.get("name"): q.get("value") for q in query if isinstance(q, dict)})
    return None


def server_time_ms(headers: dict[str, str]) -> float | None:
    timing = headers.get("server-timing")
    if timing:
        durations = [float(d) for d in _DURATION.findall(timing)]
        if durations:
            return max(durations)
    for name in SERVER_TIME_HEADERS:
        match = _MS.match(headers.get(name, ""))
        if match:
            return float(match.group(1))
    runtime = headers.get("x-runtime")
    with contextlib.suppress(ValueError):
        if runtime:
            return float(runtime) * 1000.0
    return None


def trace_id(response_headers: dict[str, str], request_headers: dict[str, str]) -> str | None:
    for headers in (response_headers, request_headers):
        for name in TRACE_HEADERS:
            value = headers.get(name, "").strip()
            if not value:
                continue
            if name == "traceparent":
                parts = value.split("-")
                value = parts[1] if len(parts) >= 3 else value
            return value[:80]
    return None


def _phases(timings: Any) -> dict[str, float]:
    if not isinstance(timings, dict):
        return {}
    return {
        k: round(float(timings[k]), 1)
        for k in PHASES
        if isinstance(timings.get(k), int | float) and timings[k] > 0
    }


def parse_har(har: dict[str, Any] | list[Any], *, url_filter: str | None = None) -> list[NetworkRequest]:
    """Parse a HAR document, or a bare list of HAR entries."""
    entries = har if isinstance(har, list) else har.get("log", {}).get("entries", [])
    requests: list[NetworkRequest] = []
    for entry in entries:
        request = entry.get("request", {})
        response = entry.get("response", {})
        url = str(request.get("url", ""))
        if url_filter and url_filter not in url:
            continue
        status = int(response.get("status") or 0)
        started = entry.get("startedDateTime")
        extra: dict[str, Any] = {"source": "har"}
        if started:
            with contextlib.suppress(ValueError):
                extra["timestamp"] = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
        size = response.get("bodySize")
        response_headers = _headers(response.get("headers"))
        requests.append(
            NetworkRequest(
                url=url,
                method=str(request.get("method", "GET")),
                status=status,
                duration_ms=float(entry.get("time") or 0.0),
                size_bytes=int(size) if isinstance(size, int | float) and size > 0 else 0,
                failed=status == 0 or status >= 400 or bool(response.get("_error")),
                operation=request_operation(request),
                server_ms=server_time_ms(response_headers),
                phases=_phases(entry.get("timings")),
                trace_id=trace_id(response_headers, _headers(request.get("headers"))),
                **extra,
            )
        )
    return requests
