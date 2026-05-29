"""Caption brief + deterministic fallback caption.

``caption_brief`` hands the *agent* (Claude) everything it needs to write the
caption itself -- no second LLM call. The deterministic template caption is the
headless fallback so an autonomous run never blocks waiting for a writer.
"""

from __future__ import annotations

from typing import Any

from .helpers import fail, ok
from .state import StateStore, get_store

AI_NOTE = "AI-generated content."


def caption_brief(
    *,
    persona_id: str,
    scene: str,
    hook: str,
    topic: str,
    platforms: list[str],
    ai_disclosure: bool = True,
    store: StateStore | None = None,
) -> dict[str, Any]:
    """Return a brief Claude uses to write the caption itself.

    Returns ``{ok, brief}``.
    """
    store = store or get_store()
    persona = store.get("personas", persona_id)
    if not persona:
        return fail(
            f"No persona with id '{persona_id}'.",
            "Create it first with create_persona / save_persona_config.",
        )
    brief = {
        "persona": persona.get("name"),
        "bio": persona.get("bio"),
        "niche": persona.get("niche"),
        "tone": persona.get("style_keywords", []),
        "scene": scene,
        "hook": hook,
        "topic": topic,
        "platforms": platforms,
        "ai_disclosure_required": ai_disclosure,
        "instructions": (
            "Write one platform-native caption (1-2 short sentences) that opens "
            "with the hook, stays in the persona's voice/niche, and ends with "
            "3-6 relevant hashtags. "
            + (
                "Include a clear AI-generated disclosure (e.g. 'AI-generated')."
                if ai_disclosure
                else "No AI disclosure required for these platforms."
            )
        ),
    }
    return ok(brief=brief)


def template_caption(
    *,
    hook: str,
    style_keywords: list[str],
    ai_disclosure: bool = True,
) -> str:
    """Deterministic fallback caption (used when no caption_fn is supplied)."""
    tags = " ".join("#" + k.replace(" ", "").lower() for k in style_keywords[:5])
    caption = hook
    if tags:
        caption += f"\n\n{tags}"
    if ai_disclosure:
        caption += f"\n\n{AI_NOTE} #ai #aiart"
    return caption
