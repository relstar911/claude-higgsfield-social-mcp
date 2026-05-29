"""higgsfield_social_mcp -- the unified FastMCP server.

Registers every tool (base layer + unification layer) on one server. Each tool
takes a Pydantic input model (``extra="forbid"``, field descriptions/constraints)
and carries MCP annotations. Thin wrappers delegate to the core logic modules so
the same logic is reused by the cycle and the smoke test.

Run:  python -m higgsfield_social.server
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from . import captions, cycle, logs, media, personas, publishing
from .config import PersonaConfig

mcp = FastMCP("higgsfield_social_mcp")


# ---------------------------------------------------------------------------
# Input models
# ---------------------------------------------------------------------------
class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreatePersonaIn(_Base):
    name: str = Field(..., min_length=1, description="Human-readable persona name (its id is the slug).")
    look: str = Field(..., min_length=1, description="Fixed visual identity kept across all renders.")
    bio: str = Field(..., description="Short persona biography / backstory.")
    niche: str = Field(..., description="Persona niche as a plain string.")
    style_keywords: list[str] = Field(default_factory=list, description="Tone/aesthetic keywords.")
    reference_image_url: str | None = Field(None, description="Optional hosted reference image (face consistency).")


class GetPersonaIn(_Base):
    persona_id: str = Field(..., description="Persona id (slug of the name).")


class GenerateImageIn(_Base):
    persona_id: str = Field(..., description="Persona id to render.")
    scene: str = Field(..., min_length=1, description="Scene/setting for this image.")
    ratio: str = Field("3:4", description="Aspect ratio, e.g. '3:4', '9:16', '1:1'.")
    resolution: str = Field("1080p", description="Target resolution.")
    wait: bool = Field(True, description="True = submit and wait; False = async, returns job_id.")


class GenerateVideoIn(_Base):
    motion_prompt: str = Field(..., min_length=1, description="Motion description for image->video.")
    asset_id: str | None = Field(None, description="Source image asset id (preferred).")
    image_url: str | None = Field(None, description="Source image URL (alternative to asset_id).")
    ratio: str = Field("9:16", description="Aspect ratio for the video.")
    wait: bool = Field(True, description="True = submit and wait; False = async, returns job_id.")
    persona_id: str | None = Field(None, description="Owning persona id (inferred from asset if omitted).")


class CheckJobIn(_Base):
    job_id: str = Field(..., description="Async job id returned by a wait=False generation.")


class CaptionBriefIn(_Base):
    persona_id: str = Field(..., description="Persona id.")
    scene: str = Field(..., description="Scene used for this post.")
    hook: str = Field(..., description="The hook line to open the caption with.")
    topic: str = Field(..., description="Topic the hook is about.")
    platforms: list[str] = Field(..., min_length=1, description="Target platforms.")
    ai_disclosure: bool = Field(True, description="Whether AI disclosure is required.")


class PublishIn(_Base):
    asset_id: str = Field(..., description="Asset id (image or video) to publish.")
    caption: str = Field(..., description="Final caption text.")
    platforms: list[str] = Field(..., min_length=1, description="Platforms: instagram, tiktok, x.")
    ai_disclosure: bool = Field(True, description="Forward AI-generated disclosure where supported.")
    dry_run: bool = Field(False, description="True = validate + record intent, never post.")
    persona_id: str | None = Field(None, description="Owning persona id (inferred from asset if omitted).")


class PostLogIn(_Base):
    persona_id: str | None = Field(None, description="Filter to one persona (optional).")
    limit: int = Field(20, ge=1, le=200, description="Max number of posts to return.")


class SavePersonaConfigIn(_Base):
    config: PersonaConfig = Field(..., description="The full PersonaConfig to persist.")


class RunCycleIn(_Base):
    persona_id: str = Field(..., description="Persona id whose config to run.")


class RunAllIn(_Base):
    pass


# ---------------------------------------------------------------------------
# Annotation presets
# ---------------------------------------------------------------------------
_READ = ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False)
_READ_REMOTE = ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=True)
_WRITE_LOCAL = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)
_GENERATE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True)
_PUBLISH = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True)


# ---------------------------------------------------------------------------
# Base-layer tools
# ---------------------------------------------------------------------------
@mcp.tool(annotations=_WRITE_LOCAL)
def create_persona(params: CreatePersonaIn) -> dict[str, Any]:
    """Create or update a persona (look, bio, niche, style, optional reference).

    Return: {ok: bool, persona: {...}, created: bool} | {ok: false, error, hint}
    """
    return personas.create_persona(**params.model_dump())


@mcp.tool(annotations=_READ)
def get_persona(params: GetPersonaIn) -> dict[str, Any]:
    """Read a persona by id.

    Return: {ok: bool, persona: {...}} | {ok: false, error, hint}
    """
    return personas.get_persona(persona_id=params.persona_id)


@mcp.tool(annotations=_GENERATE)
async def generate_image(params: GenerateImageIn) -> dict[str, Any]:
    """Generate an image from the persona's fixed look + style + scene.

    Uses the image-edit path automatically when the persona has a reference
    image. Return: {ok: bool, asset: {...}} | {ok: false, error, hint}
    """
    return await media.generate_image(**params.model_dump())


@mcp.tool(annotations=_GENERATE)
async def generate_video(params: GenerateVideoIn) -> dict[str, Any]:
    """Animate an image (asset or URL) into a video with a motion prompt.

    Return: {ok: bool, asset: {...}} | {ok: false, error, hint}
    """
    return await media.generate_video(**params.model_dump())


@mcp.tool(annotations=_READ_REMOTE)
async def check_job(params: CheckJobIn) -> dict[str, Any]:
    """Poll the status of an async generation job.

    Return: {ok: bool, status: {status, url, job_id, nsfw, raw}} | {ok: false, ...}
    """
    return await media.check_job(job_id=params.job_id)


@mcp.tool(annotations=_READ)
def caption_brief(params: CaptionBriefIn) -> dict[str, Any]:
    """Return a brief the agent uses to write the caption itself (no 2nd LLM).

    Return: {ok: bool, brief: {...}} | {ok: false, error, hint}
    """
    return captions.caption_brief(**params.model_dump())


@mcp.tool(annotations=_PUBLISH)
async def publish(params: PublishIn) -> dict[str, Any]:
    """Publish an asset to multiple platforms; each platform isolated.

    With dry_run=true nothing is posted. Return:
    {ok: bool, post: {results: {<platform>: {ok, ...}}}, any_published} | {ok: false, ...}
    """
    return await publishing.publish(**params.model_dump())


@mcp.tool(annotations=_READ)
def post_log(params: PostLogIn) -> dict[str, Any]:
    """Read posting history (newest first), to avoid repetition.

    Return: {ok: bool, posts: [...], count: int}
    """
    return logs.post_log(**params.model_dump())


# ---------------------------------------------------------------------------
# Unification-layer tools
# ---------------------------------------------------------------------------
@mcp.tool(annotations=_WRITE_LOCAL)
def save_persona_config(params: SavePersonaConfigIn) -> dict[str, Any]:
    """Persist a full PersonaConfig (a niche is pure configuration).

    Return: {ok: bool, persona_id: str, config: {...}}
    """
    return cycle.save_persona_config(config=params.config)


@mcp.tool(annotations=_GENERATE)
async def run_cycle(params: RunCycleIn) -> dict[str, Any]:
    """Run one deterministic cycle (plan->image->video->caption->publish).

    Respects autonomy/dry_run flags. With autonomous=false it stops after
    captioning. Return: {ok: bool, cycle: {status, steps, ...}} | {ok: false, ...}
    """
    return await cycle.run_cycle(persona_id=params.persona_id)


@mcp.tool(annotations=_GENERATE)
async def run_all(params: RunAllIn) -> dict[str, Any]:
    """Run one cycle for every active persona, isolated per persona.

    Return: {ok: bool, ran: int, cycles: [{persona_id, ok, status, cycle_id}]}
    """
    return await cycle.run_all()


def main() -> None:
    """Entry point: run the server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
