"""StageAgent — the only thing a concrete agent implements is *what* stage it
is and *which* Pydantic model its output must satisfy. Prompting, calling
the provider and validating the result are handled once, here, so no agent
ever hand-rolls business logic like "if project_type == 'ecommerce' ...".
"""

from __future__ import annotations

from abc import ABC
from typing import Any, ClassVar

from pydantic import BaseModel

from frontforge.prompts.builder import PromptBuilder
from frontforge.providers.base import Provider
from frontforge.shared.types import AgentResult, PromptSpec


class StageAgent(ABC):
    stage_id: ClassVar[str]
    output_model: ClassVar[type[BaseModel]]

    def __init__(self, prompt_builder: PromptBuilder | None = None):
        self.prompt_builder = prompt_builder or PromptBuilder()

    def build_prompt(
        self,
        *,
        seed: dict[str, Any],
        ancestors: dict[str, dict[str, Any]],
        verification_errors: list[str] | None = None,
    ) -> PromptSpec:
        return self.prompt_builder.build(
            self.stage_id,
            seed=seed,
            ancestors=ancestors,
            verification_errors=verification_errors,
        )

    def map_output(self, raw: dict[str, Any]) -> BaseModel:
        """Default mapper: validate the raw JSON straight against the output
        model. Override only if a stage's raw LLM output needs reshaping
        before it fits the schema."""
        return self.output_model.model_validate(raw)

    async def run(
        self,
        provider: Provider,
        *,
        seed: dict[str, Any],
        ancestors: dict[str, dict[str, Any]],
        model: str | None = None,
        verification_errors: list[str] | None = None,
    ) -> AgentResult:
        prompt = self.build_prompt(seed=seed, ancestors=ancestors, verification_errors=verification_errors)
        schema = self.output_model.model_json_schema()
        provider_result = await provider.generate(
            system_prompt=prompt.system_prompt,
            user_prompt=prompt.user_prompt,
            json_schema=schema,
            model=model,
        )
        if provider_result.data is None:
            raise ValueError(
                f"stage {self.stage_id!r}: provider did not return structured JSON data"
            )
        # Deliberately NOT validated/mapped here — VerificationEngine decides
        # pass/fail on the raw output; the orchestrator calls map_output()
        # only once verification has passed.
        return AgentResult(
            stage_id=self.stage_id,
            output=provider_result.data,
            provider_result=provider_result,
        )
