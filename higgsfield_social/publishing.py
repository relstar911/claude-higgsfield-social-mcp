"""Publishing: one tool, two selectable providers.

Provider is chosen via ``PUBLISH_PROVIDER`` (default ``"zernio"``):

  * ``zernio`` -- one unified call fans out to many platforms (see
    :mod:`higgsfield_social.zernio`). Higgsfield's public URL is passed straight
    through; per-platform results come back from Zernio.
  * ``native`` -- the hand-built per-platform adapters below (Instagram Graph
    API, TikTok Content Posting, X v1.1 upload + OAuth 1.0a). Kept as a fallback.

Either way each platform is isolated -- one failing never blocks the others --
and the ``dry_run`` path validates/records intent without contacting anything.

AI disclosure (``ai_disclosure``) is forwarded where the platform supports it
and is otherwise expected to be present in the caption text (Meta/TikTok require
disclosure of AI-generated content).
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx

from . import oauth1, zernio
from .helpers import ToolError, fail, fail_from, new_id, now_iso, ok
from .state import StateStore, get_store

# Platforms the native (per-adapter) provider can reach.
NATIVE_PLATFORMS = ("instagram", "tiktok", "x")
# Backwards-compatible alias (older imports).
SUPPORTED_PLATFORMS = NATIVE_PLATFORMS


def _provider() -> str:
    """Selected publishing provider (default: zernio)."""
    return os.environ.get("PUBLISH_PROVIDER", "zernio").lower()


def supported_platforms(provider: str | None = None) -> tuple[str, ...]:
    """Platforms allowed for the given (or current) provider."""
    provider = provider or _provider()
    if provider == "native":
        return NATIVE_PLATFORMS
    return zernio.SUPPORTED_PLATFORMS

GRAPH_VERSION = os.environ.get("IG_GRAPH_VERSION", "v21.0")
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_VERSION}"
TIKTOK_BASE = "https://open.tiktokapis.com/v2"
X_UPLOAD_URL = "https://upload.twitter.com/1.1/media/upload.json"
X_TWEET_URL = "https://api.twitter.com/2/tweets"

_HTTP_TIMEOUT = httpx.Timeout(60.0, connect=15.0)
_X_CHUNK = 4 * 1024 * 1024  # 4 MiB chunks for chunked media upload


# ---------------------------------------------------------------------------
# Instagram (Graph API)
# ---------------------------------------------------------------------------
def _ig_credentials() -> tuple[str, str]:
    user_id = os.environ.get("IG_USER_ID")
    token = os.environ.get("IG_ACCESS_TOKEN")
    if not user_id or not token:
        raise ToolError(
            "Instagram credentials missing.",
            "Set IG_USER_ID (a Business/Creator account) and IG_ACCESS_TOKEN "
            "with the instagram_content_publish permission.",
        )
    return user_id, token


async def _publish_instagram(
    client: httpx.AsyncClient, asset: dict[str, Any], caption: str, ai_disclosure: bool
) -> dict[str, Any]:
    user_id, token = _ig_credentials()
    url = asset.get("url")
    if not url:
        raise ToolError("Asset has no public URL to publish.", "Generate it first.")

    is_video = asset.get("type") == "video"
    container_params: dict[str, Any] = {"caption": caption, "access_token": token}
    if is_video:
        container_params.update({"media_type": "REELS", "video_url": url})
    else:
        container_params["image_url"] = url

    create = await client.post(f"{GRAPH_BASE}/{user_id}/media", data=container_params)
    create.raise_for_status()
    creation_id = create.json()["id"]

    # Reels must finish processing before they can be published.
    if is_video:
        await _ig_wait_ready(client, creation_id, token)

    publish = await client.post(
        f"{GRAPH_BASE}/{user_id}/media_publish",
        data={"creation_id": creation_id, "access_token": token},
    )
    publish.raise_for_status()
    media_id = publish.json().get("id")
    return {"ok": True, "platform": "instagram", "media_id": media_id, "creation_id": creation_id}


async def _ig_wait_ready(
    client: httpx.AsyncClient, creation_id: str, token: str, *, attempts: int = 30
) -> None:
    for _ in range(attempts):
        resp = await client.get(
            f"{GRAPH_BASE}/{creation_id}",
            params={"fields": "status_code", "access_token": token},
        )
        resp.raise_for_status()
        status = resp.json().get("status_code")
        if status == "FINISHED":
            return
        if status == "ERROR":
            raise ToolError(
                "Instagram reported ERROR while processing the Reel.",
                "Check the source video format/encoding and retry.",
            )
        await asyncio.sleep(5)
    raise ToolError(
        "Instagram Reel did not finish processing in time.",
        "Retry publish later; the container may still finish.",
    )


# ---------------------------------------------------------------------------
# TikTok (Content Posting API) -- video only, PULL_FROM_URL
# ---------------------------------------------------------------------------
def _tiktok_token() -> str:
    token = os.environ.get("TIKTOK_ACCESS_TOKEN")
    if not token:
        raise ToolError(
            "TikTok credentials missing.",
            "Set TIKTOK_ACCESS_TOKEN from an app that passed the Content Posting "
            "audit. Before the audit, posts are forced to SELF_ONLY.",
        )
    return token


async def _publish_tiktok(
    client: httpx.AsyncClient, asset: dict[str, Any], caption: str, ai_disclosure: bool
) -> dict[str, Any]:
    token = _tiktok_token()
    if asset.get("type") != "video":
        raise ToolError(
            "TikTok only accepts video.",
            "Enable make_video for this persona / publish a video asset.",
        )
    url = asset.get("url")
    if not url:
        raise ToolError("Asset has no public URL to publish.", "Generate it first.")

    # SELF_ONLY is mandatory before the app's Content Posting audit passes.
    privacy = os.environ.get("TIKTOK_PRIVACY_LEVEL", "SELF_ONLY")
    payload = {
        "post_info": {
            "title": caption,
            "privacy_level": privacy,
            "disable_comment": False,
            "disable_duet": False,
            "disable_stitch": False,
        },
        "source_info": {"source": "PULL_FROM_URL", "video_url": url},
    }
    if ai_disclosure:
        # TikTok AIGC label.
        payload["post_info"]["brand_content_toggle"] = False
        payload["post_info"]["is_aigc"] = True

    resp = await client.post(
        f"{TIKTOK_BASE}/post/publish/video/init/",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=UTF-8",
        },
        json=payload,
    )
    resp.raise_for_status()
    data = resp.json().get("data", {})
    return {
        "ok": True,
        "platform": "tiktok",
        "publish_id": data.get("publish_id"),
        "privacy_level": privacy,
    }


# ---------------------------------------------------------------------------
# X / Twitter -- v1.1 media/upload (chunked for video) + v2 tweet
# ---------------------------------------------------------------------------
def _x_credentials() -> dict[str, str]:
    creds = {
        "consumer_key": os.environ.get("X_API_KEY", ""),
        "consumer_secret": os.environ.get("X_API_SECRET", ""),
        "token": os.environ.get("X_ACCESS_TOKEN", ""),
        "token_secret": os.environ.get("X_ACCESS_TOKEN_SECRET", ""),
    }
    missing = [k for k, v in creds.items() if not v]
    if missing:
        raise ToolError(
            f"X credentials missing: {', '.join(missing)}.",
            "Set X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_TOKEN_SECRET "
            "(OAuth 1.0a user context, needed for media upload).",
        )
    return creds


def _x_auth_header(method: str, url: str, signed_params: dict[str, Any] | None, creds: dict[str, str]) -> str:
    return oauth1.build_authorization_header(
        method=method,
        url=url,
        consumer_key=creds["consumer_key"],
        consumer_secret=creds["consumer_secret"],
        token=creds["token"],
        token_secret=creds["token_secret"],
        signed_params=signed_params,
    )


async def _x_download(client: httpx.AsyncClient, url: str) -> bytes:
    resp = await client.get(url)
    resp.raise_for_status()
    return resp.content


async def _x_upload_media(
    client: httpx.AsyncClient, asset: dict[str, Any], creds: dict[str, str]
) -> str:
    """Upload media to X and return a media_id string.

    Implements the real flow: simple upload for images, chunked
    INIT/APPEND/FINALIZE (+STATUS poll) for video. Media MUST be uploaded before
    the tweet so we have a media_id to attach.
    """
    url = asset.get("url")
    if not url:
        raise ToolError("Asset has no public URL to upload to X.", "Generate it first.")
    data = await _x_download(client, url)
    is_video = asset.get("type") == "video"
    media_type = "video/mp4" if is_video else "image/jpeg"
    media_category = "tweet_video" if is_video else "tweet_image"

    if not is_video:
        # Simple upload: multipart body -> body NOT signed.
        header = _x_auth_header("POST", X_UPLOAD_URL, None, creds)
        resp = await client.post(
            X_UPLOAD_URL,
            headers={"Authorization": header},
            files={"media": ("media", data, media_type)},
        )
        resp.raise_for_status()
        return str(resp.json()["media_id_string"])

    # --- chunked video upload ---
    # INIT (form-urlencoded -> params ARE signed)
    init_params = {
        "command": "INIT",
        "total_bytes": str(len(data)),
        "media_type": media_type,
        "media_category": media_category,
    }
    header = _x_auth_header("POST", X_UPLOAD_URL, init_params, creds)
    init = await client.post(
        X_UPLOAD_URL,
        headers={
            "Authorization": header,
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data=init_params,
    )
    init.raise_for_status()
    media_id = str(init.json()["media_id_string"])

    # APPEND (multipart -> body NOT signed)
    segment = 0
    for offset in range(0, len(data), _X_CHUNK):
        chunk = data[offset : offset + _X_CHUNK]
        header = _x_auth_header("POST", X_UPLOAD_URL, None, creds)
        append = await client.post(
            X_UPLOAD_URL,
            headers={"Authorization": header},
            data={
                "command": "APPEND",
                "media_id": media_id,
                "segment_index": str(segment),
            },
            files={"media": ("chunk", chunk, "application/octet-stream")},
        )
        append.raise_for_status()
        segment += 1

    # FINALIZE (form-urlencoded -> params ARE signed)
    fin_params = {"command": "FINALIZE", "media_id": media_id}
    header = _x_auth_header("POST", X_UPLOAD_URL, fin_params, creds)
    finalize = await client.post(
        X_UPLOAD_URL,
        headers={
            "Authorization": header,
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data=fin_params,
    )
    finalize.raise_for_status()
    await _x_wait_processing(client, media_id, finalize.json(), creds)
    return media_id


async def _x_wait_processing(
    client: httpx.AsyncClient,
    media_id: str,
    finalize_body: dict[str, Any],
    creds: dict[str, str],
    *,
    attempts: int = 60,
) -> None:
    info = finalize_body.get("processing_info")
    for _ in range(attempts):
        if not info:
            return
        state = info.get("state")
        if state == "succeeded":
            return
        if state == "failed":
            raise ToolError(
                "X media processing failed.",
                f"Details: {info.get('error')}",
            )
        await asyncio.sleep(int(info.get("check_after_secs", 5)))
        status_params = {"command": "STATUS", "media_id": media_id}
        header = _x_auth_header("GET", X_UPLOAD_URL, status_params, creds)
        resp = await client.get(
            X_UPLOAD_URL, headers={"Authorization": header}, params=status_params
        )
        resp.raise_for_status()
        info = resp.json().get("processing_info")
    raise ToolError("X media processing did not finish in time.", "Retry later.")


async def _publish_x(
    client: httpx.AsyncClient, asset: dict[str, Any], caption: str, ai_disclosure: bool
) -> dict[str, Any]:
    creds = _x_credentials()
    media_id = await _x_upload_media(client, asset, creds)

    # v2 tweet: JSON body -> body NOT signed.
    header = _x_auth_header("POST", X_TWEET_URL, None, creds)
    resp = await client.post(
        X_TWEET_URL,
        headers={"Authorization": header, "Content-Type": "application/json"},
        json={"text": caption, "media": {"media_ids": [media_id]}},
    )
    resp.raise_for_status()
    tweet_id = resp.json().get("data", {}).get("id")
    return {"ok": True, "platform": "x", "tweet_id": tweet_id, "media_id": media_id}


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
_ADAPTERS = {
    "instagram": _publish_instagram,
    "tiktok": _publish_tiktok,
    "x": _publish_x,
}


async def _publish_native(
    asset: dict[str, Any], caption: str, platforms: list[str], ai_disclosure: bool
) -> dict[str, Any]:
    """Native provider: one isolated call per platform."""
    results: dict[str, Any] = {}
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        for platform in platforms:
            try:
                results[platform] = await _ADAPTERS[platform](
                    client, asset, caption, ai_disclosure
                )
            except Exception as exc:  # noqa: BLE001 - isolate per platform
                results[platform] = fail_from(exc, context=f"publish:{platform}")
    return results


def _dry_run_results(
    provider: str,
    asset: dict[str, Any],
    asset_id: str,
    caption: str,
    platforms: list[str],
    ai_disclosure: bool,
    profile_id: str | None,
) -> dict[str, Any]:
    """Offline echo of the intended request (no network), per platform."""
    would_post = {
        "asset_id": asset_id,
        "type": asset.get("type"),
        "url": asset.get("url"),
        "caption": caption,
        "ai_disclosure": ai_disclosure,
        "provider": provider,
    }
    if provider == "zernio":
        would_post["zernio_platforms"] = [zernio.normalize_platform(p) for p in platforms]
        would_post["zernio_profile_id"] = profile_id or os.environ.get("ZERNIO_PROFILE_ID")
    return {
        p: {"ok": True, "dry_run": True, "platform": p, "would_post": would_post}
        for p in platforms
    }


async def publish(
    *,
    asset_id: str,
    caption: str,
    platforms: list[str],
    ai_disclosure: bool = True,
    dry_run: bool = False,
    persona_id: str | None = None,
    profile_id: str | None = None,
    store: StateStore | None = None,
) -> dict[str, Any]:
    """Publish an asset to multiple platforms via the selected provider.

    Provider comes from ``PUBLISH_PROVIDER`` (default ``zernio``). Each platform
    is isolated: one failing never blocks the others. With ``dry_run=True``
    nothing is sent; the intended request is validated and recorded.
    ``profile_id`` is the per-persona Zernio profile (falls back to env).

    Returns ``{ok, post, any_published}`` where ``post.results`` has a
    per-platform entry (``ok`` true/false).
    """
    store = store or get_store()
    provider = _provider()

    asset = store.get("assets", asset_id)
    if not asset:
        return fail(
            f"No asset with id '{asset_id}'.",
            "Generate an image/video first, then publish its asset_id.",
        )
    persona_id = persona_id or asset.get("persona_id")

    allowed = supported_platforms(provider)
    unknown = [p for p in platforms if zernio.normalize_platform(p) not in allowed]
    if unknown:
        return fail(
            f"Unknown platform(s) for provider '{provider}': {unknown}.",
            f"Supported: {list(allowed)}.",
        )
    if not asset.get("url") and not dry_run:
        return fail(
            "Asset has no public URL yet.",
            "Wait for generation to finish (check_job) before publishing.",
        )

    post_meta: dict[str, Any] = {"provider": provider}
    if dry_run:
        results = _dry_run_results(
            provider, asset, asset_id, caption, platforms, ai_disclosure, profile_id
        )
    elif provider == "native":
        results = await _publish_native(asset, caption, platforms, ai_disclosure)
    else:  # zernio
        results, zmeta = await zernio.publish_via_zernio(
            asset=asset,
            caption=caption,
            platforms=platforms,
            ai_disclosure=ai_disclosure,
            profile_id=profile_id,
        )
        post_meta.update(zmeta)

    any_ok = any(r.get("ok") for r in results.values())
    post = {
        "id": new_id("post"),
        "persona_id": persona_id,
        "asset_id": asset_id,
        "caption": caption,
        "platforms": platforms,
        "ai_disclosure": ai_disclosure,
        "dry_run": dry_run,
        "provider": provider,
        "post_id": post_meta.get("post_id"),
        "results": results,
        "created_at": now_iso(),
    }
    store.put("posts", post["id"], post)
    return ok(post=post, any_published=any_ok)
