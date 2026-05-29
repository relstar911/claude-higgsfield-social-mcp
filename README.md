# AI-Influencer-Automation (MCP Server)

An MCP server that gives an agent (Claude) the tools to run AI influencers
end-to-end: from persona definition through image/video generation to publishing
on multiple social platforms. Media comes from **Higgsfield** (official Python
SDK `higgsfield-client`). Orchestration is driven by **Claude** as the agent.

> **The persona niche is pure configuration, not code** — the same deterministic
> cycle runs identically for every persona.

## What this does and does not do

It solves the **mechanics**: generate, post, orchestrate, multi-persona, with an
approval gate and dry-run. It does **not** solve **reach/virality** — that lives
in content quality, niche, and a later feedback loop. The biggest real hurdles
are not the code but the **platform approvals** and the **AI-labeling
obligations** (see [SETUP.md](SETUP.md)).

## Architecture

```
PersonaConfig  ->  run_cycle  ->  plan -> image -> video -> caption -> publish -> log
  (config)         (1 call)        (deterministic, repeatable)
```

Two layers:

1. **Base layer** — single-step tools that each do one thing.
2. **Unification layer** — combines the steps into a deterministic, repeatable
   cycle that runs identically per persona and supports multi-persona natively.

Cycle statuses: `planned -> image_done -> video_done -> captioned -> published / failed`.
With `autonomous=false` the cycle stops at `captioned` (`awaiting_approval`) and
never publishes.

## Tools

| Tool                  | Layer        | Purpose                                                      |
|-----------------------|--------------|-------------------------------------------------------------|
| `create_persona`      | base         | Define a persona (look, bio, niche, style, optional ref).   |
| `get_persona`         | base         | Read a persona.                                             |
| `generate_image`      | base         | Render from fixed look + style + scene (image-edit if ref). |
| `generate_video`      | base         | Animate an image (asset or URL) into a video.               |
| `check_job`           | base         | Poll an async generation job.                              |
| `caption_brief`       | base         | Brief the agent uses to write the caption itself.          |
| `publish`             | base         | Post to multiple platforms; each isolated; `dry_run`.      |
| `post_log`            | base         | Read posting history (avoid repetition).                   |
| `save_persona_config` | unification  | Persist a full `PersonaConfig`.                            |
| `run_cycle`           | unification  | Run one full cycle for one persona.                        |
| `run_all`             | unification  | Run one cycle for every active persona, isolated.          |

Every tool uses a Pydantic input model (`extra="forbid"`, field descriptions /
constraints), a full docstring with the return schema, and MCP annotations
(`readOnlyHint` / `destructiveHint` / `idempotentHint` / `openWorldHint`).

## Project layout

```
higgsfield_social/
  helpers.py            shared result/error helpers
  state.py              atomic JSON state store (namespaces)
  higgsfield_client.py  SDK seam (lazy import, credential check, mockable)
  personas.py           create_persona / get_persona
  media.py              generate_image / generate_video / check_job + prompt build
  captions.py           caption_brief + deterministic template fallback
  publishing.py         publish + Instagram / TikTok / X adapters
  oauth1.py             OAuth 1.0a signer (X media upload)
  config.py             PersonaConfig (the niche-as-config schema)
  planner.py            scene rotation + hook building
  cycle.py              _do_cycle, save_persona_config, run_cycle, run_all
  server.py             FastMCP server: registers all tools
agent_runner.py         cron trigger (Anthropic Messages API + MCP)
smoke_test.py           self-contained, no real keys, posts nothing
```

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 1) Prove the mechanics (no keys, posts nothing):
python smoke_test.py        # -> all checks green

# 2) Inspect what the cron trigger would send (no API call):
python agent_runner.py --print-only

# 3) Run the MCP server (stdio):
python -m higgsfield_social.server
```

Recommended rollout: **smoke test → Higgsfield key → first persona →
`dry_run=true` → a single live post → only then cron + scale.**

See [SETUP.md](SETUP.md) for credentials, platform approvals, AI-labeling, and
the cron trigger.

## Out of scope (named honestly)

- **Reach/virality** — the machine produces consistently but guarantees no
  views. The feedback loop (read performance back → weight scenes/hooks by data;
  the cycle records are the data foundation) is the sensible next step, not part
  of this first build.
- **Account creation** — manual, for ToS reasons.
- **Ban risk** from aggressive posting remains, independent of code quality.
