"""Planner: pick a fresh scene and build a hook.

Scene rotation walks the *entire* pool before repeating anything, using a
lookback over the persona's cycle history. The hook pattern and topic rotate
deterministically by cycle count so headless runs stay varied and repeatable.
"""

from __future__ import annotations

from typing import Any

from .config import PersonaConfig


def pick_fresh_scene(pool: list[str], used_newest_first: list[str]) -> str:
    """Return the scene that keeps the rotation moving through the whole pool.

    A scene used within the last ``len(pool) - 1`` cycles is excluded; among the
    remaining candidates we pick the one used longest ago (never-used first, in
    pool order). This guarantees the full pool is consumed before any repeat.
    """
    if not pool:
        raise ValueError("scene_pool must not be empty")

    window = used_newest_first[: max(0, len(pool) - 1)]
    candidates = [s for s in pool if s not in window] or list(pool)

    def last_used_distance(scene: str) -> float:
        # 0 == most recently used; larger == longer ago; inf == never used.
        return used_newest_first.index(scene) if scene in used_newest_first else float("inf")

    # max() returns the first item among ties -> never-used scenes resolve in
    # pool order, which yields a clean sequential walk on a fresh persona.
    return max(candidates, key=last_used_distance)


def plan(config: PersonaConfig, history_newest_first: list[dict[str, Any]]) -> dict[str, Any]:
    """Produce the plan for the next cycle.

    Returns ``{scene, hook, topic, hook_pattern}``.
    """
    used_scenes = [c.get("scene") for c in history_newest_first if c.get("scene")]
    scene = pick_fresh_scene(config.scene_pool, used_scenes)

    count = len(history_newest_first)
    pattern = config.hook_patterns[count % len(config.hook_patterns)]
    if config.style_keywords:
        topic = config.style_keywords[count % len(config.style_keywords)]
    else:
        topic = config.niche
    hook = pattern.replace("{topic}", topic)

    return {"scene": scene, "hook": hook, "topic": topic, "hook_pattern": pattern}
