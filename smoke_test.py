"""Self-contained smoke test -- no real keys, posts nothing.

Mocks the Higgsfield SDK seam, runs a multi-persona cycle through the *same*
code, and asserts each stage. Must be green before real credentials enter play.

Run:  python smoke_test.py
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

# Import package modules.
from higgsfield_social import cycle, higgsfield_client, planner
from higgsfield_social.config import PersonaConfig
from higgsfield_social.state import configure_store

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"

_results: list[tuple[bool, str]] = []


def check(cond: bool, label: str) -> None:
    _results.append((bool(cond), label))
    print(f"  [{PASS if cond else FAIL}] {label}")


# --- Fake Higgsfield SDK seam (no network, no keys) -----------------------
async def _fake_image(*, prompt, reference_url=None, ratio="3:4", resolution="1080p", model=None, wait=True):
    return {
        "status": "completed",
        "url": "https://example.test/img/" + str(abs(hash(prompt)) % 10**8) + ".jpg",
        "job_id": "img_job_1",
        "nsfw": False,
        "raw": {"prompt": prompt, "reference_url": reference_url},
    }


async def _fake_video(*, image_url, motion_prompt, ratio="9:16", model=None, wait=True):
    return {
        "status": "completed",
        "url": "https://example.test/vid/" + str(abs(hash(motion_prompt)) % 10**8) + ".mp4",
        "job_id": "vid_job_1",
        "nsfw": False,
        "raw": {"image_url": image_url, "motion_prompt": motion_prompt},
    }


async def _fake_status(job_id):
    return {"status": "completed", "url": "https://example.test/x.jpg", "job_id": job_id, "nsfw": False, "raw": {}}


def _install_fakes() -> None:
    higgsfield_client.generate_image = _fake_image  # type: ignore[assignment]
    higgsfield_client.generate_video = _fake_video  # type: ignore[assignment]
    higgsfield_client.job_status = _fake_status  # type: ignore[assignment]


# --- Two different niches, proving niche == config ------------------------
def _fitness_config() -> PersonaConfig:
    return PersonaConfig(
        name="Mia Fit",
        look="athletic woman, 25, freckles, ponytail, photoreal",
        bio="Daily home-workout motivation.",
        niche="fitness",
        style_keywords=["energetic", "bright", "minimal"],
        scene_pool=["sunrise rooftop yoga", "home gym kettlebell set", "park sprint intervals"],
        hook_patterns=["Your {topic} reset starts now", "3 ways to level up your {topic}", "The {topic} habit nobody talks about"],
        platforms=["instagram", "tiktok"],
        autonomous=True,
        dry_run=True,
    )


def _travel_config() -> PersonaConfig:
    return PersonaConfig(
        name="Leo Roams",
        look="rugged man, 30, beard, film-grain aesthetic",
        bio="Off-grid travel diaries.",
        niche="travel",
        style_keywords=["wanderlust", "golden hour", "cinematic"],
        scene_pool=["misty mountain ridge", "neon night market", "empty desert highway"],
        hook_patterns=["Found this {topic} spot", "Why {topic} hits different here"],
        format_rules={"make_video": False, "aspect_ratio": "4:5"},
        platforms=["instagram", "x"],
        autonomous=False,  # approval gate
        dry_run=True,
    )


async def _run() -> None:
    tmp = Path(tempfile.mkdtemp()) / "state.json"
    configure_store(tmp)
    _install_fakes()

    print("\n== Tool registration ==")
    from higgsfield_social.server import mcp

    tools = {t.name for t in await mcp.list_tools()}
    expected = {
        "create_persona", "get_persona", "generate_image", "generate_video",
        "check_job", "caption_brief", "publish", "post_log",
        "save_persona_config", "run_cycle", "run_all",
    }
    check(expected <= tools, f"all {len(expected)} tools registered ({len(tools)} total)")

    print("\n== Save two configs (two niches, same code) ==")
    fit = _fitness_config()
    trav = _travel_config()
    r1 = cycle.save_persona_config(config=fit)
    r2 = cycle.save_persona_config(config=trav)
    check(r1["ok"] and r2["ok"], "both persona configs saved")
    check(r1["persona_id"] == "mia_fit" and r2["persona_id"] == "leo_roams", "persona ids slugged")

    print("\n== run_all over multiple personas (isolated) ==")
    agent_caption = lambda brief: f"[agent] {brief['hook']} #ai"  # noqa: E731
    out = await cycle.run_all(caption_fn=agent_caption)
    check(out["ok"] and out["ran"] == 2, "run_all ran both personas")
    by_id = {c["persona_id"]: c for c in out["cycles"]}

    print("\n== Autonomous persona (fitness): video + dry-run publish ==")
    fit_cycle_id = by_id["mia_fit"]["cycle_id"]
    fit_cycle = next(c for c in mcp_store_cycles() if c["id"] == fit_cycle_id)
    check(fit_cycle["status"] == "published", "fitness cycle reached 'published' (dry-run)")
    steps = [s["step"] for s in fit_cycle["steps"]]
    check(steps == ["image", "video", "caption_brief", "publish"], f"all stages ran in order: {steps}")
    check(fit_cycle.get("video_asset_id"), "video asset produced (make_video=true)")
    check(fit_cycle.get("caption_source") == "agent", "agent caption overrode the template")

    print("\n== Approval gate (travel, autonomous=false) ==")
    trav_cycle_id = by_id["leo_roams"]["cycle_id"]
    trav_cycle = next(c for c in mcp_store_cycles() if c["id"] == trav_cycle_id)
    check(trav_cycle["status"] == "captioned", "travel cycle stopped at 'captioned'")
    check(trav_cycle.get("awaiting_approval") is True, "approval gate engaged (not published)")
    check("publish" not in [s["step"] for s in trav_cycle["steps"]], "publish never ran for gated persona")
    check("video" not in [s["step"] for s in trav_cycle["steps"]], "no video stage (make_video=false)")

    print("\n== dry_run posted nothing for real ==")
    from higgsfield_social.state import get_store

    posts = get_store().list_values("posts")
    check(all(p["dry_run"] for p in posts), "every recorded post is dry_run")
    check(all(r.get("dry_run") for p in posts for r in p["results"].values()), "no live platform calls made")

    print("\n== Scene rotation: whole pool before repeat ==")
    # Fresh persona; run len(pool)+1 cycles and inspect scene order.
    rot = PersonaConfig(
        name="Rot Test",
        look="x", bio="y", niche="z", style_keywords=["a"],
        scene_pool=["S0", "S1", "S2"],
        hook_patterns=["{topic}"],
        platforms=["x"], autonomous=False, dry_run=True,
        format_rules={"make_video": False},
    )
    cycle.save_persona_config(config=rot)
    scenes = []
    for _ in range(4):
        res = await cycle.run_cycle(persona_id="rot_test")
        scenes.append(res["cycle"]["scene"])
    check(set(scenes[:3]) == {"S0", "S1", "S2"}, f"all 3 scenes used before any repeat: {scenes[:3]}")
    check(scenes[3] == scenes[0], f"4th cycle repeats the 1st only after a full pass: {scenes}")

    print("\n== Planner unit: deterministic full-pool walk ==")
    pool = ["A", "B", "C"]
    used: list[str] = []
    walk = []
    for _ in range(3):
        s = planner.pick_fresh_scene(pool, used)
        walk.append(s)
        used.insert(0, s)
    check(sorted(walk) == pool, f"planner covers the full pool: {walk}")

    print("\n== Zernio provider: live-mapping (mocked seam, posts nothing real) ==")
    import os

    from higgsfield_social import zernio

    # Fake the ONLY network seam: echo a per-platform success response.
    async def _fake_submit(body):
        return {
            "id": "zpost_smoke_1",
            "results": [{"name": p["name"], "status": "submitted"} for p in body["platforms"]],
        }

    zernio.submit_post = _fake_submit  # type: ignore[assignment]
    os.environ["PUBLISH_PROVIDER"] = "zernio"

    check(zernio.normalize_platform("x") == "twitter", "platform alias x -> twitter")

    # Full Zernio platform list is accepted by the config validator.
    broad = PersonaConfig(
        name="Broad", look="l", bio="b", niche="n", style_keywords=["k"],
        scene_pool=["sc"], hook_patterns=["{topic}"],
        platforms=["linkedin", "youtube", "threads", "bluesky", "reddit"],
        autonomous=False, dry_run=True, format_rules={"make_video": False},
    )
    check(
        broad.platforms == ["linkedin", "youtube", "threads", "bluesky", "reddit"],
        "full Zernio platform list accepted by config",
    )

    # Real publish path (mocked): per-persona profile id, x normalises to twitter.
    zlive = PersonaConfig(
        name="Zee Live", look="l", bio="b", niche="n", style_keywords=["k"],
        scene_pool=["sc"], hook_patterns=["{topic}"],
        platforms=["x", "instagram"],
        zernio_profile_id="prof_zee",
        autonomous=True, dry_run=False, format_rules={"make_video": False},
    )
    cycle.save_persona_config(config=zlive)
    zres = await cycle.run_cycle(persona_id="zee_live")
    zc = zres["cycle"]
    check(zc["status"] == "published", "zernio cycle published (mocked)")
    zpost = get_store().get("posts", zc["post_id"])
    check(zpost["provider"] == "zernio" and not zpost["dry_run"], "post recorded as live zernio post")
    check(zpost["post_id"] == "zpost_smoke_1", "zernio post id captured")
    zr = zpost["results"]
    check("twitter" in zr and "instagram" in zr, f"results keyed by canonical names: {list(zr)}")
    check(all(r.get("ok") for r in zr.values()), "all platforms ok (mocked)")

    print("\n== Zernio: missing profile id -> actionable per-platform error ==")
    os.environ.pop("ZERNIO_PROFILE_ID", None)
    znop = PersonaConfig(
        name="No Profile", look="l", bio="b", niche="n", style_keywords=["k"],
        scene_pool=["sc"], hook_patterns=["{topic}"],
        platforms=["instagram"],
        autonomous=True, dry_run=False, format_rules={"make_video": False},
    )
    cycle.save_persona_config(config=znop)
    nres = await cycle.run_cycle(persona_id="no_profile")
    npost = get_store().get("posts", nres["cycle"]["post_id"])
    nr = npost["results"]
    check(not any(r.get("ok") for r in nr.values()), "no platform ok without a profile id")
    check(
        any("profile" in (str(r.get("error", "")) + str(r.get("hint", ""))).lower() for r in nr.values()),
        "actionable profile-id error surfaced",
    )

    # Restore default provider for any later use.
    os.environ.pop("PUBLISH_PROVIDER", None)


def mcp_store_cycles():
    from higgsfield_social.state import get_store

    return get_store().list_values("cycles")


def main() -> int:
    try:
        asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001
        print(f"\n{FAIL} smoke test crashed: {type(exc).__name__}: {exc}")
        import traceback

        traceback.print_exc()
        return 1

    passed = sum(1 for ok_, _ in _results if ok_)
    total = len(_results)
    print(f"\n{'='*48}\n{passed}/{total} checks passed")
    if passed != total:
        for ok_, label in _results:
            if not ok_:
                print(f"  {FAIL} {label}")
        return 1
    print(f"{PASS} all green")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
