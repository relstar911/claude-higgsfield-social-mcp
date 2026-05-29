"""Persona core logic: define and read an influencer persona.

A persona fixes the *look* (so every render stays on-model) plus bio, niche and
style keywords. An optional reference image enables the image-edit / face-
consistency path during generation.
"""

from __future__ import annotations

from typing import Any

from .helpers import fail, now_iso, ok, slugify
from .state import StateStore, get_store


def create_persona(
    *,
    name: str,
    look: str,
    bio: str,
    niche: str,
    style_keywords: list[str],
    reference_image_url: str | None = None,
    store: StateStore | None = None,
) -> dict[str, Any]:
    """Create or update a persona. The persona id is the slug of its name.

    Returns ``{ok, persona}``.
    """
    store = store or get_store()
    persona_id = slugify(name)
    existing = store.get("personas", persona_id)
    persona = {
        "id": persona_id,
        "name": name,
        "look": look,
        "bio": bio,
        "niche": niche,
        "style_keywords": list(style_keywords),
        "reference_image_url": reference_image_url,
        "created_at": existing["created_at"] if existing else now_iso(),
        "updated_at": now_iso(),
    }
    store.put("personas", persona_id, persona)
    return ok(persona=persona, created=existing is None)


def get_persona(*, persona_id: str, store: StateStore | None = None) -> dict[str, Any]:
    """Read a persona by id. Returns ``{ok, persona}`` or an actionable error."""
    store = store or get_store()
    persona = store.get("personas", persona_id)
    if not persona:
        known = list(store.all("personas").keys())
        return fail(
            f"No persona with id '{persona_id}'.",
            f"Create it first with create_persona. Known ids: {known or '[]'}.",
        )
    return ok(persona=persona)
