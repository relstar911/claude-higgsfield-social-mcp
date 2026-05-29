"""Shared helpers: result wrappers, error formatting, small utilities.

All tools return plain JSON-serialisable dicts. Success results carry
``{"ok": True, ...}``; failures carry ``{"ok": False, "error": ..., "hint": ...}``.
Errors are written to *lead the agent to a fix* -- concrete and actionable,
never a silent fake success.
"""

from __future__ import annotations

import datetime as _dt
import re
import uuid
from typing import Any

import httpx


class ToolError(Exception):
    """An expected, actionable failure inside a tool.

    The ``hint`` should tell the agent exactly what to do to recover.
    """

    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


def ok(**kwargs: Any) -> dict[str, Any]:
    """Wrap a successful result."""
    return {"ok": True, **kwargs}


def fail(error: str, hint: str | None = None, **extra: Any) -> dict[str, Any]:
    """Wrap a failure result with an actionable hint."""
    result: dict[str, Any] = {"ok": False, "error": error}
    if hint:
        result["hint"] = hint
    result.update(extra)
    return result


def fail_from(exc: Exception, *, context: str) -> dict[str, Any]:
    """Turn an exception into a structured, actionable failure dict."""
    if isinstance(exc, ToolError):
        return fail(f"{context}: {exc.message}", exc.hint)
    if isinstance(exc, httpx.HTTPStatusError):
        body = ""
        try:
            body = exc.response.text[:500]
        except Exception:  # noqa: BLE001 - defensive
            body = "<unreadable response body>"
        return fail(
            f"{context}: HTTP {exc.response.status_code} from {exc.request.url}",
            "Inspect the response body and verify credentials / payload.",
            response_body=body,
        )
    if isinstance(exc, httpx.RequestError):
        return fail(
            f"{context}: network error talking to {exc.request.url if exc.request else 'remote host'}",
            "Check connectivity and the target endpoint, then retry.",
        )
    return fail(f"{context}: {type(exc).__name__}: {exc}")


def now_iso() -> str:
    """UTC timestamp in ISO-8601 form."""
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    """Short prefixed id, e.g. ``asset_3f1c2a``."""
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def slugify(text: str) -> str:
    """Stable slug used as a persona id derived from its name."""
    slug = re.sub(r"[^a-z0-9]+", "_", text.strip().lower()).strip("_")
    return slug or "persona"


async def maybe_await(value: Any) -> Any:
    """Await ``value`` if it is awaitable, otherwise return it unchanged.

    Lets ``caption_fn`` (and similar hooks) be either sync or async.
    """
    if hasattr(value, "__await__"):
        return await value
    return value
