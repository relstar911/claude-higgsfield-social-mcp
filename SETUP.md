# SETUP

What **you** must supply for this to do real work. The code solves the
mechanics; these are the things only you can provide.

## 0. Install

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python smoke_test.py     # must be green before adding real credentials
```

## 1. Higgsfield key — without it nothing generates

Set **one** of these credential forms:

```bash
export HF_KEY="..."                 # single key, OR:
export HF_API_KEY="..."
export HF_API_SECRET="..."
```

Optional model overrides (defaults shown):

```bash
export HF_IMAGE_MODEL="seedream"
export HF_VIDEO_MODEL="higgsfield-ai/standard"   # DoP / standard motion
```

If credentials are missing, generation tools return a clear, actionable error —
**no silent fake success.**

## 2. Platform access

Each platform is isolated in `publish`: a failure on one never blocks the others.
Missing credentials produce an actionable error; `dry_run=true` needs none.

### Instagram (Graph API)

Requires a **Business/Creator** account and a token with
`instagram_content_publish`.

```bash
export IG_USER_ID="<ig business account id>"
export IG_ACCESS_TOKEN="<long-lived token>"
export IG_GRAPH_VERSION="v21.0"     # optional
```

Reels are videos: the adapter creates the container, polls until `FINISHED`, then
publishes.

### TikTok (Content Posting API) — video only

Requires an app that **passed the Content Posting audit**. Before the audit,
posts are forced to `SELF_ONLY`.

```bash
export TIKTOK_ACCESS_TOKEN="<access token>"
export TIKTOK_PRIVACY_LEVEL="SELF_ONLY"   # keep until your audit passes
```

Videos are pulled via `PULL_FROM_URL`, so the asset URL must be publicly
reachable by TikTok.

### X (Twitter) — OAuth 1.0a user context

Media must be uploaded **before** the tweet (v1.1 `media/upload`, chunked for
video) to obtain a `media_id`. This is fully implemented (`publishing.py` +
`oauth1.py`); it just needs real credentials:

```bash
export X_API_KEY="<consumer key>"
export X_API_SECRET="<consumer secret>"
export X_ACCESS_TOKEN="<access token>"
export X_ACCESS_TOKEN_SECRET="<access token secret>"
```

## 3. Anthropic key — so Claude orchestrates and writes captions

```bash
export ANTHROPIC_API_KEY="..."
export ANTHROPIC_MODEL="claude-sonnet-4-6"   # optional
```

The MCP server itself needs no Anthropic key; only `agent_runner.py` does.

## 4. Cron trigger — the MCP does not start itself

`agent_runner.py` is the trigger. Inspect the request without calling the API:

```bash
python agent_runner.py --print-only
```

Example crontab (every 6 hours):

```cron
0 */6 * * * cd /path/to/repo && . .venv/bin/activate && python agent_runner.py >> runner.log 2>&1
```

If you run the MCP server over HTTP instead of stdio, set `HF_SOCIAL_MCP_URL`.

## 5. AI labeling — keep disclosure on

Meta and TikTok require disclosure of AI-generated content. Leave
`ai_disclosure=true` (the default). It is forwarded where the platform supports
it and is included in the caption text otherwise.

## State

State is a local JSON file (atomic writes; a corrupt file is backed up and a
fresh one started). Override the path with:

```bash
export HF_SOCIAL_STATE="/path/to/state.json"
```

## Recommended rollout

`smoke test → Higgsfield key → first persona → dry_run=true → one live post →
then cron + scale.`

## Reality check

- This does not guarantee reach/virality.
- Account creation is manual (ToS).
- Aggressive posting carries ban risk regardless of code quality.
