"""Higgsfield SDK binding.

The three public coroutines -- :func:`generate_image`, :func:`generate_video`,
:func:`job_status` -- are the *seam*: the rest of the system only ever calls
these, and tests monkeypatch them with fakes. The real SDK is lazy-imported so
the package can be imported (and the smoke test can run) without the SDK or any
credentials present.

No silent fake success: if credentials are missing we raise a clear, actionable
:class:`ToolError`.

Model ids are overridable via env:
    HF_IMAGE_MODEL  (default: "seedream")
    HF_VIDEO_MODEL  (default: "higgsfield-ai/standard"  -- DoP / standard motion)
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
def _require_credentials() -> dict[str, str | None]:
    """Return credentials or raise an actionable error. Never fake success."""
    key = os.environ.get("HF_KEY")
    api_key = os.environ.get("HF_API_KEY")
    api_secret = os.environ.get("HF_API_SECRET")
    if key or (api_key and api_secret):
        return {"key": key, "api_key": api_key, "api_secret": api_secret}
    raise ToolError(
        "Higgsfield credentials are missing -- nothing can be generated.",
        "Set HF_KEY, or set BOTH HF_API_KEY and HF_API_SECRET in the environment.",
    )


def _load_sdk() -> Any:
    """Lazy-import the higgsfield SDK with an actionable error if absent."""
    try:
        import higgsfield  # type: ignore  # noqa: F401

        return higgsfield
    except ImportError as exc:  # pragma: no cover - depends on real install
        raise ToolError(
            "The higgsfield SDK is not installed.",
            "Install it with: pip install higgsfield-client",
        ) from exc


def _make_client() -> Any:  # pragma: no cover - requires real SDK + creds
    creds = _require_credentials()
    sdk = _load_sdk()
    # The SDK exposes a client factory; we pass whichever credential form is set.
    client_cls = getattr(sdk, "Higgsfield", None) or getattr(sdk, "Client", None)
    if client_cls is None:
        raise ToolError(
            "Could not locate a client class in the higgsfield SDK.",
            "Check the installed higgsfield-client version / API surface.",
        )
    if creds["key"]:
        return client_cls(api_key=creds["key"])
    return client_cls(api_key=creds["api_key"], api_secret=creds["api_secret"])


# ---------------------------------------------------------------------------
# Response normalisation (tolerant of the response shape)
# ---------------------------------------------------------------------------
def _as_dict(obj: Any) -> dict[str, Any]:
    if isinstance(obj, dict):
        return obj
    for attr in ("model_dump", "dict", "to_dict", "_asdict"):
        fn = getattr(obj, attr, None)
        if callable(fn):
            try:
                return fn()
            except Exception:  # noqa: BLE001
                pass
    return {k: getattr(obj, k) for k in dir(obj) if not k.startswith("_")}


def _deep_find_url(data: Any, _depth: int = 0) -> str | None:
    """Pull the first media URL out of an arbitrarily shaped response."""
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


def _is_nsfw(data: dict[str, Any]) -> bool:
    for key in ("nsfw", "is_nsfw", "flagged"):
        if bool(data.get(key)):
            return True
    status = str(data.get("status", "")).lower()
    return "nsfw" in status


def normalize_result(raw: Any) -> dict[str, Any]:
    """Normalise any SDK result into a stable shape.

    Returns ``{status, url, job_id, nsfw, raw}``.
    """
    data = _as_dict(raw)
    job_id = (
        data.get("id")
        or data.get("request_id")
        or data.get("job_id")
        or data.get("requestId")
    )
    url = _deep_find_url(data)
    status = str(data.get("status") or ("completed" if url else "pending")).lower()
    return {
        "status": status,
        "url": url,
        "job_id": job_id,
        "nsfw": _is_nsfw(data),
        "raw": data,
    }


# ---------------------------------------------------------------------------
# Public seam -- these are what the rest of the system (and tests) call
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
    """
    return await _run_image(
        prompt=prompt,
        reference_url=reference_url,
        ratio=ratio,
        resolution=resolution,
        model=model or DEFAULT_IMAGE_MODEL,
        wait=wait,
    )


async def generate_video(
    *,
    image_url: str,
    motion_prompt: str,
    ratio: str = "9:16",
    model: str | None = None,
    wait: bool = True,
) -> dict[str, Any]:
    """Animate an image into a video (image -> video)."""
    return await _run_video(
        image_url=image_url,
        motion_prompt=motion_prompt,
        ratio=ratio,
        model=model or DEFAULT_VIDEO_MODEL,
        wait=wait,
    )


async def job_status(job_id: str) -> dict[str, Any]:
    """Poll the status of an async job."""
    return await _run_status(job_id)


# ---------------------------------------------------------------------------
# Real SDK interaction (private; speculative + tolerant; monkeypatched in tests)
# ---------------------------------------------------------------------------
async def _run_image(
    *, prompt, reference_url, ratio, resolution, model, wait
):  # pragma: no cover - requires real SDK
    client = _make_client()
    params: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "aspect_ratio": ratio,
        "resolution": resolution,
    }
    if reference_url:
        # Image-edit path -> face/look consistency from the reference.
        params["reference_image"] = reference_url
        params["mode"] = "edit"
    return await _submit(client, params, wait=wait)


async def _run_video(
    *, image_url, motion_prompt, ratio, model, wait
):  # pragma: no cover - requires real SDK
    client = _make_client()
    params = {
        "model": model,
        "input_image": image_url,
        "prompt": motion_prompt,
        "aspect_ratio": ratio,
    }
    return await _submit(client, params, wait=wait)


async def _submit(client: Any, params: dict[str, Any], *, wait: bool):  # pragma: no cover
    """Dispatch via subscribe() (submit+wait) or submit()+poll."""
    import inspect

    if wait and hasattr(client, "subscribe"):
        result = client.subscribe(**params)
        if inspect.isawaitable(result):
            result = await result
        return normalize_result(result)

    submit = getattr(client, "submit", None)
    if submit is None:
        raise ToolError(
            "The higgsfield client exposes neither subscribe() nor submit().",
            "Check the higgsfield-client version against this adapter.",
        )
    handle = submit(**params)
    if inspect.isawaitable(handle):
        handle = await handle
    return normalize_result(handle)


async def _run_status(job_id: str):  # pragma: no cover - requires real SDK
    import inspect

    client = _make_client()
    poll = getattr(client, "poll_request_status", None) or getattr(
        client, "status", None
    )
    if poll is None:
        raise ToolError(
            "The higgsfield client has no poll_request_status()/status().",
            "Check the higgsfield-client version against this adapter.",
        )
    result = poll(job_id)
    if inspect.isawaitable(result):
        result = await result
    return normalize_result(result)


async def upload_reference(path: str) -> str:  # pragma: no cover - requires real SDK
    """Upload a local reference image and return its hosted URL."""
    import inspect

    client = _make_client()
    upload = getattr(client, "upload_file", None)
    if upload is None:
        raise ToolError(
            "The higgsfield client has no upload_file().",
            "Host the reference image yourself and pass its URL instead.",
        )
    result = upload(path)
    if inspect.isawaitable(result):
        result = await result
    url = _deep_find_url(_as_dict(result)) or (result if isinstance(result, str) else None)
    if not url:
        raise ToolError("upload_file() did not return a usable URL.", None)
    return url
