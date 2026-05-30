"""Read posting history -- guards against repetition and powers a future
performance feedback loop (the cycle records are the data foundation)."""

from __future__ import annotations

from typing import Any

from .helpers import ok
from .state import StateStore, get_store


def post_log(
    *,
    persona_id: str | None = None,
    limit: int = 20,
    store: StateStore | None = None,
) -> dict[str, Any]:
    """Return recent posts, newest first, optionally filtered by persona.

    Returns ``{ok, posts, count}``.
    """
    store = store or get_store()
    posts = store.list_values("posts")
    if persona_id:
        posts = [p for p in posts if p.get("persona_id") == persona_id]
    posts.sort(key=lambda p: p.get("created_at", ""), reverse=True)
    posts = posts[: max(1, limit)]
    return ok(posts=posts, count=len(posts))
