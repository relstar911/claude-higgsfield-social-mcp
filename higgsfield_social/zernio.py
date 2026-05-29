"""Zernio unified-publishing provider.

One ``POST /api/v1/posts`` fans out to many platforms. Higgsfield already returns
a public media URL, so we pass it straight into ``mediaItems[].url`` (no upload
step). Zernio returns a per-platform result, so the "one platform failing must
not block the others" property is preserved server-side.

The single coroutine :func:`submit_post` is the *seam*: it performs the only
network call, and the smoke test monkeypatches it with a fake (same pattern as
:mod:`higgsfield_social.higgsfield_client`).

NOTE on the request body: public sources disagree on the exact ``platforms[]``
item shape. :func:`build_post_body` is the single, documented place to reconcile
with the live OpenAPI once an API key is available -- nothing else needs to
change. The response parser is deliberately tolerant of the response shape.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from .helpers import ToolError, fail_from, now_iso

ZERNIO_BASE = os.environ.get("ZERNIO_BASE_URL", "https://zernio.com/api")

# Zernio's supported platforms. Note: it uses "twitter", not "x".
SUPPORTED_PLATFORMS = (
    "instagram",
    "tiktok",
    "twitter",
    "facebook",
    "linkedin",
    "youtube",
    "pinterest",
    "reddit",
    "bluesky",
    "threads",
    "googlebusiness",
    "telegram",
    "snapchat",
    "whatsapp",
    "discord",
)

# Accept our historical platform names and normalise them to Zernio's.
PLATFORM_ALIASES = {"x": "twitter"}

_HTTP_TIMEOUT = httpx.Timeout(60.0, connect=15.0)


def normalize_platform(name: str) -> str:
    """Map an input platform name to Zernio's canonical name."""
    return PLATFORM_ALIASES.get(name, name)


# ---------------------------------------------------------------------------
# Credentials & profile
# ---------------------------------------------------------------------------
def _zernio_api_key() -> str:
    """Return the Zernio API key or raise an actionable error."""
    key = os.environ.get("ZERNIO_API_KEY")
    if not key:
        raise ToolError(
            "Zernio API key is missing -- cannot publish via Zernio.",
            "Set ZERNIO_API_KEY (Bearer key, starts with 'sk_'), or switch to the "
            "native provider with PUBLISH_PROVIDER=native.",
        )
    return key


def _resolve_profile_id(persona_profile_id: str | None) -> str:
    """Per-persona profile id wins; otherwise the global env default."""
    profile_id = persona_profile_id or os.environ.get("ZERNIO_PROFILE_ID")
    if not profile_id:
        raise ToolError(
            "No Zernio profile id for this persona.",
            "Set zernio_profile_id in the PersonaConfig, or the ZERNIO_PROFILE_ID "
            "environment variable.",
        )
    return profile_id


# ---------------------------------------------------------------------------
# Request body (the single place to reconcile with the live OpenAPI)
# ---------------------------------------------------------------------------
def build_post_body(
    *,
    content: str,
    platforms: list[str],
    media_item: dict[str, Any] | None,
    profile_id: str,
    ai_disclosure: bool,
) -> dict[str, Any]:
    """Construct the ``POST /v1/posts`` request body.

    ``platforms`` here are already Zernio-canonical names. AI disclosure is sent
    via each platform's ``platformSpecificData`` where Zernio supports it and is
    otherwise expected to live in ``content``.
    """
    platform_specific: dict[str, Any] = {}
    if ai_disclosure:
        # AI-generated content flag; harmless if a platform ignores it.
        platform_specific["aiGenerated"] = True

    body: dict[str, Any] = {
        "content": content,
        "profileId": profile_id,
        "platforms": [
            {"name": name, "platformSpecificData": dict(platform_specific)}
            for name in platforms
        ],
    }
    if media_item:
        body["mediaItems"] = [media_item]
    return body


def media_item_for_asset(asset: dict[str, Any]) -> dict[str, Any] | None:
    """Build a Zernio mediaItems entry from one of our assets (URL pass-through)."""
    url = asset.get("url")
    if not url:
        return None
    kind = "video" if asset.get("type") == "video" else "image"
    return {"type": kind, "url": url, "filename": f"{asset.get('id', 'asset')}.{ 'mp4' if kind == 'video' else 'jpg'}"}


# ---------------------------------------------------------------------------
# Network seam (monkeypatched in tests)
# ---------------------------------------------------------------------------
async def submit_post(body: dict[str, Any]) -> dict[str, Any]:  # pragma: no cover - network
    """Perform the actual Zernio create-post call. Returns parsed JSON."""
    api_key = _zernio_api_key()
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        resp = await client.post(
            f"{ZERNIO_BASE}/v1/posts",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=body,
        )
        resp.raise_for_status()
        return resp.json()


# ---------------------------------------------------------------------------
# Response normalisation -> our per-platform results shape
# ---------------------------------------------------------------------------
def _extract_post_id(response: dict[str, Any]) -> str | None:
    for key in ("id", "postId", "post_id"):
        val = response.get(key)
        if val:
            return str(val)
    data = response.get("data")
    if isinstance(data, dict):
        return _extract_post_id(data)
    return None


def _per_platform_from_response(response: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Best-effort extraction of per-platform results from Zernio's response.

    Tolerant of shape: looks for a list of result objects under common keys,
    each carrying a platform name and a status/error.
    """
    candidates: Any = None
    for key in ("results", "platforms", "platformResults", "posts"):
        if isinstance(response.get(key), list):
            candidates = response[key]
            break
    if candidates is None and isinstance(response.get("data"), dict):
        return _per_platform_from_response(response["data"])

    parsed: dict[str, dict[str, Any]] = {}
    if isinstance(candidates, list):
        for item in candidates:
            if not isinstance(item, dict):
                continue
            name = item.get("name") or item.get("platform")
            if not name:
                continue
            status = str(item.get("status", "")).lower()
            errored = bool(item.get("error")) or status in ("failed", "error", "rejected")
            parsed[name] = {
                "ok": not errored,
                "platform": name,
                "status": item.get("status"),
                "error": item.get("error"),
            }
    return parsed


def normalize_results(
    response: dict[str, Any], requested_platforms: list[str]
) -> tuple[dict[str, dict[str, Any]], str | None]:
    """Map Zernio's response into our per-platform ``results`` dict.

    Returns ``(results, post_id)``. Platforms not itemised in the response inherit
    the overall outcome (a post id implies the call was accepted).
    """
    post_id = _extract_post_id(response)
    per_platform = _per_platform_from_response(response)

    results: dict[str, dict[str, Any]] = {}
    accepted = post_id is not None
    for name in requested_platforms:
        if name in per_platform:
            results[name] = {**per_platform[name], "zernio_post_id": post_id}
        else:
            results[name] = {
                "ok": accepted,
                "platform": name,
                "status": "submitted" if accepted else "unknown",
                "zernio_post_id": post_id,
            }
    return results, post_id


# ---------------------------------------------------------------------------
# Provider entrypoint
# ---------------------------------------------------------------------------
async def publish_via_zernio(
    *,
    asset: dict[str, Any],
    caption: str,
    platforms: list[str],
    ai_disclosure: bool,
    profile_id: str | None,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Publish one asset to many platforms with a single Zernio call.

    Returns ``(results, post_meta)``. On a hard failure (auth/network/HTTP) every
    requested platform is marked failed with the same actionable error, keeping
    the per-platform contract intact.
    """
    canonical = [normalize_platform(p) for p in platforms]
    try:
        resolved_profile = _resolve_profile_id(profile_id)
        media_item = media_item_for_asset(asset)
        if media_item is None:
            raise ToolError(
                "Asset has no public URL to publish via Zernio.",
                "Wait for generation to finish (check_job) before publishing.",
            )
        body = build_post_body(
            content=caption,
            platforms=canonical,
            media_item=media_item,
            profile_id=resolved_profile,
            ai_disclosure=ai_disclosure,
        )
        response = await submit_post(body)
    except Exception as exc:  # noqa: BLE001 - reflect onto every platform
        failure = fail_from(exc, context="publish:zernio")
        results = {p: {**failure, "platform": p} for p in canonical}
        return results, {"provider": "zernio", "post_id": None, "submitted_at": now_iso()}

    results, post_id = normalize_results(response, canonical)
    return results, {
        "provider": "zernio",
        "post_id": post_id,
        "submitted_at": now_iso(),
        "raw": response,
    }
