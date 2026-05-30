"""Higgsfield SDK binding (matches higgsfield-client >= 0.1).

The three public coroutines -- :func:`generate_image`, :func:`generate_video`,
:func:`job_status` -- are the *seam*: the rest of the system only ever calls
these, and tests monkeypatch them with fakes. The real SDK is lazy-imported so
the package can be imported (and the smoke test can run) without the SDK or any
credentials present.

How the real SDK works (verified against higgsfield_client 0.1):
  * Module is ``higgsfield_client`` (NOT ``higgsfield``).
  * ``AsyncClient`` talks to ``https://platform.higgsfield.ai`` with a
    ``Key <credential>`` header. Credentials come from HF_KEY, or
    HF_API_KEY + HF_API_SECRET (same env vars we check here).
  * ``await client.submit(application, arguments)`` -> a request controller.
    ``application`` is the endpoint path of a Higgsfield *application*;
    ``arguments`` is the application's JSON input.
  * Poll ``controller.poll_request_status()`` -> ``Status`` objects
    (Queued / InProgress / Completed / Failed / NSFW / Cancelled).
  * ``await controller.get()`` returns the final result JSON.

What still must be supplied per account (see SETUP.md): the exact *application*
endpoint paths and their argument schema. Those are set via env and the argument
mapping below is the single place to adjust if your application expects
different keys.

No silent fake success: if credentials are missing we raise a clear, actionable
:class:`ToolError`.

Env overrides (defaults shown):
    HF_IMAGE_MODEL  -> image application endpoint   (default: "seedream")
    HF_VIDEO_MODEL  -> video application endpoint    (default: "higgsfield-ai/standard")
"""

from __future__ import annotations

import os
from typing import Any

from .helpers import ToolError

DEFAULT_IMAGE_MODEL = os.environ.get("HF_IMAGE_MODEL", "seedream")
DEFAULT_VIDEO_MODEL = os.environ.get("HF_VIDEO_MODEL", "higgsfield-ai/standard")


# ---------------------------------------------------------------------------
# Credentials & client
# ---------------------------------------------------------------------------
def _require_credentials() -> None:
    """Raise an actionable error if no credentials are present. Never fake success."""
    if os.environ.get("HF_KEY") or (
        os.environ.get("HF_API_KEY") and os.environ.get("HF_API_SECRET")
    ):
        return
    raise ToolError(
        "Higgsfield credentials are missing -- nothing can be generated.",
        "Set HF_KEY, or set BOTH HF_API_KEY and HF_API_SECRET in the environment.",
    )


def _load_sdk() -> Any:
    """Lazy-import the higgsfield_client SDK with an actionable error if absent."""
    try:
        import higgsfield_client  # type: ignore  # noqa: F401

        return higgsfield_client
    except ImportError as exc:  # pragma: no cover - depends on real install
        raise ToolError(
            "The higgsfield-client SDK is not installed.",
            "Install it with: pip install higgsfield-client",
        ) from exc


def _client() -> Any:  # pragma: no cover - requires real SDK + creds
    """Create an AsyncClient. Credentials are read lazily by the SDK from env."""
    _require_credentials()
    sdk = _load_sdk()
    return sdk.AsyncClient()


# ---------------------------------------------------------------------------
# Response normalisation (tolerant of the application's result shape)
# ---------------------------------------------------------------------------
def _deep_find_url(data: Any, _depth: int = 0) -> str | None:
    """Pull the first media URL out of an arbitrarily shaped result."""
    if _depth > 6:
        return None
    if isinstance(data, str):
        return data if data.startswith("http") else None
    if isinstance(data, dict):
        for prefer in ("url", "image_url", "video_url", "result_url", "output_url"):
            val = data.get(prefer)
            if isinstance(val, str) and val.startswith("http"):
                return val
        for val in data.values():
            found = _deep_find_url(val, _depth + 1)
            if found:
                return found
    if isinstance(data, (list, tuple)):
        for val in data:
            found = _deep_find_url(val, _depth + 1)
            if found:
                return found
    return None


def _status_name(status: Any) -> str:
    """Map a Status dataclass instance to a lowercase status string."""
    return type(status).__name__.lower()


# ---------------------------------------------------------------------------
# Argument mapping (the single place to adjust per application schema)
# ---------------------------------------------------------------------------
def _image_arguments(prompt, reference_url, ratio, resolution) -> dict[str, Any]:
    arguments: dict[str, Any] = {
        "prompt": prompt,
        "aspect_ratio": ratio,
        "resolution": resolution,
    }
    if reference_url:
        # Image-edit path -> face/look consistency from the reference.
        arguments["reference_image"] = reference_url
        arguments["mode"] = "edit"
    return arguments


def _video_arguments(image_url, motion_prompt, ratio) -> dict[str, Any]:
    return {
        "input_image": image_url,
        "prompt": motion_prompt,
        "aspect_ratio": ratio,
    }


# ---------------------------------------------------------------------------
# Public seam -- this is what the rest of the system (and tests) call
# ---------------------------------------------------------------------------
async def generate_image(
    *,
    prompt: str,
    reference_url: str | None = None,
    ratio: str = "3:4",
    resolution: str = "1080p",
    model: str | None = None,
    wait: bool = True,
) -> dict[str, Any]:
    """Generate an image.

    Uses the image-edit path when ``reference_url`` is given (face consistency).
    ``wait=True`` submits and blocks for the result; ``wait=False`` submits and
    returns a ``job_id`` to poll with :func:`job_status`.

    Returns ``{status, url, job_id, nsfw, raw}``.
    """
    arguments = _image_arguments(prompt, reference_url, ratio, resolution)
    return await _dispatch(model or DEFAULT_IMAGE_MODEL, arguments, wait=wait)


async def generate_video(
    *,
    image_url: str,
    motion_prompt: str,
    ratio: str = "9:16",
    model: str | None = None,
    wait: bool = True,
) -> dict[str, Any]:
    """Animate an image into a video (image -> video).

    Returns ``{status, url, job_id, nsfw, raw}``.
    """
    arguments = _video_arguments(image_url, motion_prompt, ratio)
    return await _dispatch(model or DEFAULT_VIDEO_MODEL, arguments, wait=wait)


async def job_status(job_id: str) -> dict[str, Any]:
    """Poll the status of an async job. Returns ``{status, url, job_id, nsfw, raw}``."""
    return await _poll_once(job_id)


async def upload_reference(path: str) -> str:  # pragma: no cover - requires real SDK
    """Upload a local reference image and return its hosted URL."""
    client = _client()
    return await client.upload_file(path)


# ---------------------------------------------------------------------------
# Real SDK interaction (private; monkeypatched in tests via the seam above)
# ---------------------------------------------------------------------------
async def _dispatch(
    application: str, arguments: dict[str, Any], *, wait: bool
):  # pragma: no cover - requires real SDK
    client = _client()
    controller = await client.submit(application=application, arguments=arguments)
    request_id = controller.request_id

    if not wait:
        return {
            "status": "queued",
            "url": None,
            "job_id": request_id,
            "nsfw": False,
            "raw": {"request_id": request_id},
        }

    final_status: Any = None
    async for status in controller.poll_request_status():
        final_status = status

    name = _status_name(final_status) if final_status is not None else "completed"
    if name == "nsfw":
        return {"status": "nsfw", "url": None, "job_id": request_id, "nsfw": True, "raw": {}}
    if name in ("failed", "cancelled", "canceled"):
        raise ToolError(
            f"Higgsfield generation {name} for request {request_id}.",
            "Inspect the application/arguments and retry.",
        )

    result = await controller.get()
    return {
        "status": "completed",
        "url": _deep_find_url(result),
        "job_id": request_id,
        "nsfw": False,
        "raw": result,
    }


async def _poll_once(request_id: str):  # pragma: no cover - requires real SDK
    client = _client()
    controller = client.get_request_controller(request_id)
    status = await controller.status()
    name = _status_name(status)
    url = None
    if name == "completed":
        try:
            result = await controller.get()
            url = _deep_find_url(result)
        except Exception:  # noqa: BLE001 - status still useful even if fetch fails
            result = {}
    return {
        "status": name,
        "url": url,
        "job_id": request_id,
        "nsfw": name == "nsfw",
        "raw": {},
    }
