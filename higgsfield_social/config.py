"""PersonaConfig -- the single schema that makes a niche pure configuration.

Everything the deterministic cycle needs lives here: identity, content
templates, format rules, target platforms, and the autonomy switches.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .publishing import SUPPORTED_PLATFORMS


class FormatRules(BaseModel):
    """Rendering rules shared by every cycle of a persona."""

    model_config = ConfigDict(extra="forbid")

    aspect_ratio: str = Field(
        "9:16", description="Aspect ratio for renders, e.g. '9:16', '3:4', '1:1'."
    )
    resolution: str = Field("1080p", description="Target resolution, e.g. '1080p'.")
    motion_style: str = Field(
        "subtle cinematic motion",
        description="Motion description prepended to the video prompt.",
    )
    make_video: bool = Field(
        True, description="If true, animate the image into a video before publishing."
    )


class PersonaConfig(BaseModel):
    """Full persona definition; a niche is expressed entirely through this."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, description="Human-readable persona name.")
    active: bool = Field(True, description="If false, run_all skips this persona.")

    look: str = Field(
        ..., min_length=1, description="Fixed visual identity kept across all renders."
    )
    reference_image_url: str | None = Field(
        None, description="Optional hosted reference image for face consistency."
    )

    bio: str = Field(..., description="Short persona biography / backstory.")
    niche: str = Field(..., description="The niche, as a plain string (config, not code).")
    style_keywords: list[str] = Field(
        default_factory=list, description="Tone/aesthetic keywords; also seed hashtags."
    )

    scene_pool: list[str] = Field(
        ..., min_length=1, description="Rotating scene templates (rotated before repeat)."
    )
    hook_patterns: list[str] = Field(
        ...,
        min_length=1,
        description="Caption hook patterns; may contain a {topic} placeholder.",
    )

    format_rules: FormatRules = Field(default_factory=FormatRules)
    platforms: list[str] = Field(
        ..., min_length=1, description="Target platforms: instagram, tiktok, x."
    )
    ai_disclosure: bool = Field(
        True, description="Send AI-generated disclosure where supported. Keep true."
    )

    autonomous: bool = Field(
        False,
        description="If false, the cycle stops after captioning and waits for approval.",
    )
    dry_run: bool = Field(
        True, description="If true, generate + validate but never actually post."
    )

    @field_validator("platforms")
    @classmethod
    def _validate_platforms(cls, value: list[str]) -> list[str]:
        unknown = [p for p in value if p not in SUPPORTED_PLATFORMS]
        if unknown:
            raise ValueError(
                f"Unsupported platform(s) {unknown}; supported: {list(SUPPORTED_PLATFORMS)}"
            )
        return value
