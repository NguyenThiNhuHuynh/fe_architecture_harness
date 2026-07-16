"""Pydantic models shared across the harness.

Domain models (one per stage output) double as the JSON Schema source for
``--json-schema`` passed to the Claude CLI — see providers/claude_cli.py.
Infra models (StageStatus, ProviderResult, ...) are the plumbing types used
by core/*.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Infra types
# ---------------------------------------------------------------------------


class StageStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    DIRTY = "dirty"


class StageState(BaseModel):
    stage_id: str
    status: StageStatus = StageStatus.PENDING
    input_hash: str | None = None
    updated_at: datetime | None = None
    attempts: int = 0
    error: str | None = None
    duration_ms: int | None = None
    cost_usd: float | None = None


class ProviderResult(BaseModel):
    raw_text: str
    data: dict[str, Any] | None = None
    model: str
    duration_ms: int
    cost_usd: float | None = None
    # Populated only when the provider's own envelope reports them (the
    # `claude` CLI's `--output-format json` doesn't always include a `usage`
    # block) — left as None rather than guessed, since gen_ai.usage.*
    # tracing attributes should reflect real counts or be omitted entirely.
    input_tokens: int | None = None
    output_tokens: int | None = None


class ImageAttachment(BaseModel):
    """A single image sent alongside a prompt turn — e.g. a rendered Figma
    frame used as a visual reference. `label` is shown to the model so it
    can refer back to "the Login screenshot" etc.
    """

    label: str
    media_type: str
    base64_data: str


class PromptSpec(BaseModel):
    system_prompt: str
    user_prompt: str
    images: list[ImageAttachment] = Field(default_factory=list)


class VerificationIssue(BaseModel):
    verifier: str
    message: str
    severity: Literal["error", "warning"] = "error"


class VerificationResult(BaseModel):
    passed: bool
    issues: list[VerificationIssue] = Field(default_factory=list)


class AgentResult(BaseModel):
    stage_id: str
    output: dict[str, Any]
    provider_result: ProviderResult
    # Carried back up so the orchestrator can log the *actual* prompt this
    # attempt used without rebuilding it — tracing needs "what was sent",
    # not just "what came back".
    system_prompt: str = ""
    user_prompt: str = ""


# ---------------------------------------------------------------------------
# Domain models — one per stage output
# ---------------------------------------------------------------------------


class ProjectBrief(BaseModel):
    """Output of the `clarification` stage — the single source of truth seed."""

    project_name: str
    project_type: str
    summary: str
    target_users: list[str] = Field(default_factory=list)
    modules: list[str] = Field(default_factory=list)
    tech_preferences: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(
        default_factory=list,
        description="Ambiguities the agent resolved by assumption rather than asking the user again.",
    )


class Role(BaseModel):
    name: str
    description: str
    permissions: list[str] = Field(default_factory=list)


class Entity(BaseModel):
    name: str
    description: str
    fields: list[str] = Field(default_factory=list)


class Feature(BaseModel):
    name: str
    description: str
    priority: Literal["must", "should", "could"] = "must"


class RequirementSpec(BaseModel):
    """Output of the `requirement` stage."""

    functional_requirements: list[str] = Field(default_factory=list)
    non_functional_requirements: list[str] = Field(default_factory=list)
    roles: list[Role] = Field(default_factory=list)
    entities: list[Entity] = Field(default_factory=list)
    features: list[Feature] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


class Persona(BaseModel):
    name: str
    role: str
    goals: list[str] = Field(default_factory=list)
    pain_points: list[str] = Field(default_factory=list)


class UserJourney(BaseModel):
    name: str
    persona: str
    steps: list[str] = Field(default_factory=list)


class BusinessAnalysisResult(BaseModel):
    """Output of the `business_analysis` stage."""

    personas: list[Persona] = Field(default_factory=list)
    user_journeys: list[UserJourney] = Field(default_factory=list)
    business_rules: list[str] = Field(default_factory=list)
    kpis: list[str] = Field(default_factory=list)


class ColorToken(BaseModel):
    name: str
    value: str
    usage: str = ""
    inferred: bool = Field(
        default=False,
        description="True when read off a screenshot rather than a published Figma style.",
    )


class TypographySpec(BaseModel):
    font_family: str
    scale: list[str] = Field(default_factory=list)


class FigmaFrameInfo(BaseModel):
    id: str = ""
    name: str
    image_path: str = Field(
        default="",
        description="Path (relative to .harness/figma_assets/) to this frame's rendered screenshot, if fetched.",
    )


class FigmaPageInfo(BaseModel):
    name: str
    frames: list[FigmaFrameInfo] = Field(default_factory=list, description="Top-level frames on this Figma page.")


class FigmaTypographyStyle(BaseModel):
    name: str
    font_family: str = ""
    font_size: str = ""
    inferred: bool = Field(
        default=False,
        description="True when read off a screenshot rather than a published Figma style.",
    )


class FigmaComponentInfo(BaseModel):
    name: str
    variants: list[str] = Field(default_factory=list)
    inferred: bool = Field(
        default=False,
        description="True when read off a screenshot rather than a published Figma component.",
    )


class DesignAnalysisResult(BaseModel):
    """Output of the `design_analysis` stage. `source` is "none" whenever no
    Figma URL was supplied — this stage always runs (cheap no-op in that
    case) rather than being conditionally skipped, so the DAG stays simple.
    """

    source: Literal["figma", "none"] = "none"
    pages: list[FigmaPageInfo] = Field(default_factory=list)
    color_tokens: list[ColorToken] = Field(default_factory=list)
    typography: list[FigmaTypographyStyle] = Field(default_factory=list)
    components: list[FigmaComponentInfo] = Field(default_factory=list)
    notes: list[str] = Field(
        default_factory=list,
        description="Anything ambiguous or lost while interpreting the Figma file.",
    )


class DesignSystemSpec(BaseModel):
    """Output of the `design_system` stage."""

    design_principles: list[str] = Field(default_factory=list)
    color_tokens: list[ColorToken] = Field(default_factory=list)
    typography: TypographySpec
    spacing_scale: list[str] = Field(default_factory=list)
    base_components: list[str] = Field(default_factory=list)
    themes: list[str] = Field(default_factory=list)


class SitemapNode(BaseModel):
    path: str
    name: str
    children: list[str] = Field(default_factory=list, description="Child paths.")


class NavigationItem(BaseModel):
    label: str
    path: str
    roles: list[str] = Field(default_factory=list)


class IAResult(BaseModel):
    """Output of the `information_architecture` stage."""

    sitemap: list[SitemapNode] = Field(default_factory=list)
    navigation: list[NavigationItem] = Field(default_factory=list)
    content_types: list[str] = Field(default_factory=list)


class FrontendArchitectureSpec(BaseModel):
    """Output of the `frontend_architecture` stage."""

    framework: str
    rendering_strategy: str
    routing_strategy: str
    state_management: str
    data_fetching_strategy: str
    folder_structure: list[str] = Field(default_factory=list)
    key_libraries: list[str] = Field(default_factory=list)


class PageSpec(BaseModel):
    path: str
    name: str
    purpose: str
    layout: str
    sections: list[str] = Field(default_factory=list)
    data_requirements: list[str] = Field(default_factory=list)
    roles_allowed: list[str] = Field(default_factory=list)
    figma_frame_ref: str = Field(
        default="",
        description="Name of the matching Figma frame (from design_analysis.pages), if this page corresponds to one.",
    )


class PagePlan(BaseModel):
    """Output of the `page_planning` stage."""

    pages: list[PageSpec] = Field(default_factory=list)


class ComponentSpec(BaseModel):
    name: str
    kind: Literal["layout", "ui", "feature"] = "ui"
    props: list[str] = Field(default_factory=list)
    used_in_pages: list[str] = Field(default_factory=list)
    depends_on_components: list[str] = Field(default_factory=list)


class ComponentPlan(BaseModel):
    """Output of the `component_planning` stage."""

    components: list[ComponentSpec] = Field(default_factory=list)


class GeneratedFile(BaseModel):
    path: str = Field(description="Relative path within the generated project output dir.")
    content: str
    language: str = ""


class CodegenResult(BaseModel):
    """Output of the `codegen` stage.

    Files are returned as structured data — the agent never writes to disk
    itself. FilesystemTool is the only writer, invoked by the orchestrator
    after verification passes.
    """

    files: list[GeneratedFile] = Field(default_factory=list)
    setup_instructions: list[str] = Field(default_factory=list)


class PreviewResult(BaseModel):
    """Output of the `preview` stage."""

    notes: list[str] = Field(default_factory=list)
    issues_found: list[str] = Field(default_factory=list)


class QualityIssue(BaseModel):
    severity: Literal["blocker", "major", "minor"] = "minor"
    description: str
    location: str = ""


class QualityReviewResult(BaseModel):
    """Output of the `quality_review` stage."""

    score: float = Field(ge=0, le=100)
    passed: bool
    issues: list[QualityIssue] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)


STAGE_OUTPUT_MODELS: dict[str, type[BaseModel]] = {
    "clarification": ProjectBrief,
    "requirement": RequirementSpec,
    "design_analysis": DesignAnalysisResult,
    "business_analysis": BusinessAnalysisResult,
    "design_system": DesignSystemSpec,
    "information_architecture": IAResult,
    "frontend_architecture": FrontendArchitectureSpec,
    "page_planning": PagePlan,
    "component_planning": ComponentPlan,
    "codegen": CodegenResult,
    "preview": PreviewResult,
    "quality_review": QualityReviewResult,
}
