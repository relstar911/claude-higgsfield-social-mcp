# SETUP

What **you** must supply for this to do real work. The code solves the
mechanics; these are the things only you can provide.

## 0. Install

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python smoke_test.py     # must be green before adding real credentials
```

**Configuration:** copy the committed placeholder file to a local `.env`
(gitignored — never commit real secrets), fill in your values, and load it:

```bash
cp .env.example .env
# edit .env, then:
set -a && source .env && set +a
```

## 1. Higgsfield key — without it nothing generates

Set **one** of these credential forms:

```bash
export HF_KEY="..."                 # single key, OR:
export HF_API_KEY="..."
export HF_API_SECRET="..."
```

**Application endpoints (must match your Higgsfield account).** The SDK calls an
*application* endpoint on `https://platform.higgsfield.ai` with a JSON argument
payload. The endpoint paths and their exact argument schema are account/app
specific — set them to the real values from your Higgsfield app:

```bash
export HF_IMAGE_MODEL="seedream"                  # image application endpoint
export HF_VIDEO_MODEL="higgsfield-ai/standard"    # video application endpoint (DoP / standard)
```

If your application expects different argument keys than the defaults
(`prompt`, `aspect_ratio`, `resolution`, `reference_image`/`mode` for image;
`input_image`, `prompt`, `aspect_ratio` for video), adjust the mapping in
`higgsfield_social/higgsfield_client.py` (`_image_arguments` / `_video_arguments`)
— that is the single, documented place for it.

If credentials are missing, generation tools return a clear, actionable error —
**no silent fake success.** (Verified: with the SDK installed but no keys, a
cycle fails gracefully at the image step with that error.)

## 2. Platform access

Choose a publishing provider with `PUBLISH_PROVIDER` (default `zernio`). Each
platform is isolated in `publish`: a failure on one never blocks the others.
`dry_run=true` needs no platform credentials.

### Option A — Zernio (default, recommended)

One unified API for up to 15 platforms; you connect your accounts once in
Zernio and post via a single call.

```bash
export PUBLISH_PROVIDER="zernio"          # default; can be omitted
export ZERNIO_API_KEY="sk_..."            # Bearer key from your Zernio account
export ZERNIO_PROFILE_ID="<profile id>"   # global fallback if a persona has none
```

- **Per-persona accounts:** set `zernio_profile_id` in each `PersonaConfig` so
  every AI influencer posts to *its own* connected accounts. If unset, the
  `ZERNIO_PROFILE_ID` env var is used.
- **Platform names** are Zernio's: `instagram`, `tiktok`, `twitter`, `facebook`,
  `linkedin`, `youtube`, `pinterest`, `reddit`, `bluesky`, `threads`,
  `googlebusiness`, `telegram`, `snapchat`, `whatsapp`, `discord`. `'x'` is
  accepted and normalised to `twitter`.
- **Media:** Higgsfield's public output URL is passed straight into the post —
  no upload step. The URL must stay publicly reachable until Zernio fetches it.
- **Reconciling the request body:** public docs vary slightly on the exact
  `platforms[]` field shape. It is built in one place,
  `higgsfield_social/zernio.py:build_post_body`. After your first live call,
  if a field name differs, adjust it there only. Verify a post via
  `GET https://zernio.com/api/v1/posts/{postId}`.

Still account-level work you must do: connect each platform inside Zernio and
pass any platform approvals those platforms require (e.g. Instagram needs a
Business/Creator account; TikTok needs its Content Posting audit for public
posts).

### Option B — Native adapters (fallback)

`PUBLISH_PROVIDER=native` uses the built-in per-platform adapters; reaches
`instagram` / `tiktok` / `x` only.

```bash
export PUBLISH_PROVIDER="native"

# Instagram (Graph API) — Business/Creator account + instagram_content_publish
export IG_USER_ID="<ig business account id>"
export IG_ACCESS_TOKEN="<long-lived token>"
export IG_GRAPH_VERSION="v21.0"                 # optional

# TikTok (Content Posting API) — video only; SELF_ONLY until your audit passes
export TIKTOK_ACCESS_TOKEN="<access token>"
export TIKTOK_PRIVACY_LEVEL="SELF_ONLY"

# X (Twitter) — OAuth 1.0a user context; media uploaded before the tweet
export X_API_KEY="<consumer key>"
export X_API_SECRET="<consumer secret>"
export X_ACCESS_TOKEN="<access token>"
export X_ACCESS_TOKEN_SECRET="<access token secret>"
```

Instagram Reels and TikTok pull media via a public URL (`PULL_FROM_URL`), so the
asset URL must be publicly reachable; the X adapter downloads + chunk-uploads it.

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

**Important — how the agent reaches the server.** The Anthropic *Messages API*
MCP connector reaches MCP servers by **URL**, not stdio. Two options:

1. **Hosted/HTTP:** run the server over HTTP and point the runner at it:
   ```bash
   HF_SOCIAL_TRANSPORT=streamable-http python -m higgsfield_social.server
   export HF_SOCIAL_MCP_URL="https://your-host/mcp"
   ```
2. **Local stdio:** drive it with the **Claude Agent SDK** / Claude Code, which
   can spawn `python -m higgsfield_social.server` directly over stdio.

`python agent_runner.py --print-only` shows exactly which descriptor it will send.

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
