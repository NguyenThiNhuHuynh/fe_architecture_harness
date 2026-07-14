"""Which --model each stage uses. Reasoning-heavy stages (architecture,
codegen, review) default to a stronger model; everything else uses the
harness-wide default. Changing a stage's model is a one-line config edit,
never a code change to that agent."""

from __future__ import annotations

from frontforge.shared.constants import DEFAULT_MODEL

STAGE_MODELS: dict[str, str] = {
    "clarification": DEFAULT_MODEL,
    "requirement": DEFAULT_MODEL,
    "design_analysis": DEFAULT_MODEL,
    "business_analysis": DEFAULT_MODEL,
    "design_system": DEFAULT_MODEL,
    "information_architecture": DEFAULT_MODEL,
    "frontend_architecture": "opus",
    "page_planning": DEFAULT_MODEL,
    "component_planning": DEFAULT_MODEL,
    "codegen": "opus",
    "preview": DEFAULT_MODEL,
    "quality_review": "opus",
}


def model_for_stage(stage_id: str) -> str:
    return STAGE_MODELS.get(stage_id, DEFAULT_MODEL)
