"""Media core logic: build prompts, generate images/videos, poll jobs.

Prompts are built from the *fixed* persona look + style keywords + the scene, so
identity stays consistent across renders. All generation goes through the
:mod:`higgsfield_client` seam.
"""

from __future__ import annotations

from typing import Any

from . import higgsfield_client as hf
from .helpers import fail, fail_from, new_id, now_iso, ok
from .state import StateStore, get_store


def build_image_prompt(persona: dict[str, Any], scene: str) -> str:
    """Fixed look + scene + style keywords -> a single prompt string."""
    parts = [persona.get("look", ""), scene]
    parts.extend(persona.get("style_keywords", []) or [])
    return ", ".join(p.strip() for p in parts if p and p.strip())


def build_video_prompt(scene: str, motion_style: str) -> str:
    """Scene + motion style -> a motion prompt for image->video."""
    bits = [b for b in (motion_style, scene) if b and b.strip()]
    return ", ".join(b.strip() for b in bits)


async def generate_image(
    *,
    persona_id: str,
    scene: str,
    ratio: str = "3:4",
    resolution: str = "1080p",
    wait: bool = True,
    store: StateStore | None = None,
) -> dict[str, Any]:
    """Generate an image for a persona+scene and record it as an asset.

    Uses the image-edit path automatically when the persona has a reference
    image (face consistency). Returns ``{ok, asset}``.
    """
    store = store or get_store()
    persona = store.get("personas", persona_id)
    if not persona:
        return fail(
            f"No persona with id '{persona_id}'.",
            "Create it first with create_persona / save_persona_config.",
        )

    prompt = build_image_prompt(persona, scene)
    reference_url = persona.get("reference_image_url")
    try:
        result = await hf.generate_image(
            prompt=prompt,
            reference_url=reference_url,
            ratio=ratio,
            resolution=resolution,
            wait=wait,
        )
    except Exception as exc:  # noqa: BLE001 - normalise into actionable result
        return fail_from(exc, context="generate_image")

    if result.get("nsfw"):
        return fail(
            "Generation was flagged NSFW by Higgsfield and cannot be published.",
            "Soften the scene/look wording and regenerate.",
            raw=result.get("raw"),
        )

    asset = {
        "id": new_id("asset"),
        "persona_id": persona_id,
        "type": "image",
        "prompt": prompt,
        "scene": scene,
        "used_reference": bool(reference_url),
        "status": result["status"],
        "url": result["url"],
        "job_id": result["job_id"],
        "created_at": now_iso(),
    }
    store.put("assets", asset["id"], asset)
    return ok(asset=asset)


async def generate_video(
    *,
    motion_prompt: str,
    asset_id: str | None = None,
    image_url: str | None = None,
    ratio: str = "9:16",
    wait: bool = True,
    persona_id: str | None = None,
    store: StateStore | None = None,
) -> dict[str, Any]:
    """Animate an image into a video. Source is an existing asset or a URL.

    Returns ``{ok, asset}``.
    """
    store = store or get_store()
    source_url = image_url
    source_asset = None
    if asset_id:
        source_asset = store.get("assets", asset_id)
        if not source_asset:
            return fail(
                f"No asset with id '{asset_id}'.",
                "Pass a valid asset_id from generate_image, or supply image_url.",
            )
        source_url = source_asset.get("url")
        persona_id = persona_id or source_asset.get("persona_id")

    if not source_url:
        return fail(
            "No source image for the video.",
            "Provide asset_id (with a ready url) or image_url.",
        )

    try:
        result = await hf.generate_video(
            image_url=source_url,
            motion_prompt=motion_prompt,
            ratio=ratio,
            wait=wait,
        )
    except Exception as exc:  # noqa: BLE001
        return fail_from(exc, context="generate_video")

    if result.get("nsfw"):
        return fail(
            "Video generation was flagged NSFW and cannot be published.",
            "Soften the motion/scene wording and regenerate.",
            raw=result.get("raw"),
        )

    asset = {
        "id": new_id("asset"),
        "persona_id": persona_id,
        "type": "video",
        "source_asset_id": asset_id,
        "prompt": motion_prompt,
        "status": result["status"],
        "url": result["url"],
        "job_id": result["job_id"],
        "created_at": now_iso(),
    }
    store.put("assets", asset["id"], asset)
    return ok(asset=asset)


async def check_job(*, job_id: str, store: StateStore | None = None) -> dict[str, Any]:
    """Poll an async generation job. Returns ``{ok, status}``."""
    try:
        result = await hf.job_status(job_id)
    except Exception as exc:  # noqa: BLE001
        return fail_from(exc, context="check_job")
    return ok(status=result)
