"""Unification layer: deterministic, repeatable cycle for any persona.

``plan -> image -> video -> caption -> publish``, every step logged with a
status, autonomy flags respected, one cycle record written. The same code runs
identically for every persona/niche; ``run_all`` handles multiple personas with
per-persona isolation.

Cycle statuses: planned -> image_done -> video_done -> captioned ->
published / failed. With ``autonomous=false`` the cycle stops at ``captioned``
(``awaiting_approval``) and never publishes.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from . import captions, media, personas, publishing
from . import planner as planner_mod
from .config import PersonaConfig
from .helpers import fail, maybe_await, new_id, now_iso, ok, slugify
from .state import StateStore, get_store

# A caption writer takes the brief dict and returns the caption string
# (sync or async). When absent, the deterministic template caption is used.
CaptionFn = Callable[[dict[str, Any]], Awaitable[str] | str]


def _persona_cycles(store: StateStore, persona_id: str) -> list[dict[str, Any]]:
    cycles = [
        c for c in store.list_values("cycles") if c.get("persona_id") == persona_id
    ]
    cycles.sort(key=lambda c: c.get("seq", 0), reverse=True)
    return cycles


def save_persona_config(
    *, config: PersonaConfig, store: StateStore | None = None
) -> dict[str, Any]:
    """Persist a PersonaConfig and mirror its identity into the persona record.

    Returns ``{ok, persona_id, config}``.
    """
    store = store or get_store()
    persona_id = slugify(config.name)
    store.put("persona_configs", persona_id, config.model_dump())
    # Mirror identity so the base media/caption tools can resolve the persona.
    personas.create_persona(
        name=config.name,
        look=config.look,
        bio=config.bio,
        niche=config.niche,
        style_keywords=config.style_keywords,
        reference_image_url=config.reference_image_url,
        store=store,
    )
    return ok(persona_id=persona_id, config=config.model_dump())


def _load_config(store: StateStore, persona_id: str) -> PersonaConfig | None:
    raw = store.get("persona_configs", persona_id)
    if not raw:
        return None
    return PersonaConfig.model_validate(raw)


async def _do_cycle(
    config: PersonaConfig,
    *,
    caption_fn: CaptionFn | None = None,
    store: StateStore | None = None,
) -> dict[str, Any]:
    """Run one full cycle for a persona. Always writes a cycle record."""
    store = store or get_store()
    persona_id = slugify(config.name)

    history = _persona_cycles(store, persona_id)
    seq = (history[0]["seq"] + 1) if history else 1

    record: dict[str, Any] = {
        "id": new_id("cycle"),
        "seq": seq,
        "persona_id": persona_id,
        "persona_name": config.name,
        "status": "planned",
        "steps": [],
        "started_at": now_iso(),
        "dry_run": config.dry_run,
        "autonomous": config.autonomous,
    }

    def step(name: str, result: dict[str, Any]) -> bool:
        record["steps"].append(
            {"step": name, "ok": bool(result.get("ok")), "at": now_iso()}
        )
        return bool(result.get("ok"))

    def finish(status: str, **extra: Any) -> dict[str, Any]:
        record["status"] = status
        record["finished_at"] = now_iso()
        record.update(extra)
        store.put("cycles", record["id"], record)
        return ok(cycle=record) if status != "failed" else fail(
            extra.get("error", "cycle failed"), extra.get("hint"), cycle=record
        )

    # --- plan ---
    plan = planner_mod.plan(config, history)
    record.update(
        scene=plan["scene"], hook=plan["hook"], topic=plan["topic"]
    )
    record["status"] = "planned"

    # --- image ---
    img = await media.generate_image(
        persona_id=persona_id,
        scene=plan["scene"],
        ratio=config.format_rules.aspect_ratio,
        resolution=config.format_rules.resolution,
        wait=True,
        store=store,
    )
    if not step("image", img):
        return finish("failed", error=img.get("error"), hint=img.get("hint"))
    image_asset = img["asset"]
    record["status"] = "image_done"
    record["image_asset_id"] = image_asset["id"]

    publish_asset = image_asset

    # --- video (optional) ---
    if config.format_rules.make_video:
        motion = media.build_video_prompt(
            plan["scene"], config.format_rules.motion_style
        )
        vid = await media.generate_video(
            motion_prompt=motion,
            asset_id=image_asset["id"],
            ratio=config.format_rules.aspect_ratio,
            wait=True,
            persona_id=persona_id,
            store=store,
        )
        if not step("video", vid):
            return finish("failed", error=vid.get("error"), hint=vid.get("hint"))
        publish_asset = vid["asset"]
        record["status"] = "video_done"
        record["video_asset_id"] = publish_asset["id"]

    # --- caption ---
    brief = captions.caption_brief(
        persona_id=persona_id,
        scene=plan["scene"],
        hook=plan["hook"],
        topic=plan["topic"],
        platforms=config.platforms,
        ai_disclosure=config.ai_disclosure,
        store=store,
    )
    if not step("caption_brief", brief):
        return finish("failed", error=brief.get("error"), hint=brief.get("hint"))

    if caption_fn is not None:
        caption_text = await maybe_await(caption_fn(brief["brief"]))
        caption_source = "agent"
    else:
        caption_text = captions.template_caption(
            hook=plan["hook"],
            style_keywords=config.style_keywords,
            ai_disclosure=config.ai_disclosure,
        )
        caption_source = "template"
    record["caption"] = caption_text
    record["caption_source"] = caption_source
    record["publish_asset_id"] = publish_asset["id"]
    record["status"] = "captioned"

    # --- autonomy gate ---
    if not config.autonomous:
        record["awaiting_approval"] = True
        return finish(
            "captioned",
            awaiting_approval=True,
            note="autonomous=false -> stopped before publishing; awaiting approval.",
        )

    # --- publish ---
    pub = await publishing.publish(
        asset_id=publish_asset["id"],
        caption=caption_text,
        platforms=config.platforms,
        ai_disclosure=config.ai_disclosure,
        dry_run=config.dry_run,
        persona_id=persona_id,
        profile_id=config.zernio_profile_id,
        store=store,
    )
    if not step("publish", pub):
        return finish("failed", error=pub.get("error"), hint=pub.get("hint"))
    record["post_id"] = pub["post"]["id"]
    record["publish_results"] = pub["post"]["results"]
    return finish("published")


async def run_cycle(
    *,
    persona_id: str,
    caption_fn: CaptionFn | None = None,
    store: StateStore | None = None,
) -> dict[str, Any]:
    """Run one cycle for a saved persona config. Returns ``{ok, cycle}``."""
    store = store or get_store()
    config = _load_config(store, persona_id)
    if config is None:
        known = list(store.all("persona_configs").keys())
        return fail(
            f"No persona config for id '{persona_id}'.",
            f"Save one with save_persona_config first. Known: {known or '[]'}.",
        )
    return await _do_cycle(config, caption_fn=caption_fn, store=store)


async def run_all(
    *, caption_fn: CaptionFn | None = None, store: StateStore | None = None
) -> dict[str, Any]:
    """Run one cycle for every active persona, isolated per persona.

    Returns ``{ok, ran, cycles}`` where each entry reports that persona's outcome.
    """
    store = store or get_store()
    configs = store.all("persona_configs")
    outcomes: list[dict[str, Any]] = []
    for persona_id, raw in configs.items():
        try:
            config = PersonaConfig.model_validate(raw)
        except Exception as exc:  # noqa: BLE001 - one bad config must not stop the rest
            outcomes.append(
                {"persona_id": persona_id, "ok": False, "error": f"invalid config: {exc}"}
            )
            continue
        if not config.active:
            outcomes.append({"persona_id": persona_id, "ok": True, "skipped": "inactive"})
            continue
        try:
            result = await _do_cycle(config, caption_fn=caption_fn, store=store)
            outcomes.append(
                {
                    "persona_id": persona_id,
                    "ok": result.get("ok", False),
                    "status": result.get("cycle", {}).get("status"),
                    "cycle_id": result.get("cycle", {}).get("id"),
                }
            )
        except Exception as exc:  # noqa: BLE001 - isolate persona-level crashes
            outcomes.append(
                {"persona_id": persona_id, "ok": False, "error": f"{type(exc).__name__}: {exc}"}
            )
    return ok(ran=len(outcomes), cycles=outcomes)
