"""StageAgent — the only thing a concrete agent implements is *what* stage it
is and *which* Pydantic model its output must satisfy. Prompting, calling
the provider and validating the result are handled once, here, so no agent
ever hand-rolls business logic like "if project_type == 'ecommerce' ...".
"""

from __future__ import annotations

from abc import ABC
from collections.abc import Callable
from typing import Any, ClassVar

from pydantic import BaseModel

from frontforge.core.session import RunSession
from frontforge.prompts.builder import PromptBuilder
from frontforge.providers.base import Provider
from frontforge.shared.types import AgentResult, ImageAttachment, PromptSpec


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

    async def prepare_context(
        self, seed: dict[str, Any], *, session: RunSession | None = None
    ) -> dict[str, Any]:
        """Override to enrich `seed` with data fetched from outside the
        harness (e.g. a Figma file, via a Tool — never fetched by the model
        itself) before the prompt is built. `session` is provided so an
        agent can persist fetched assets (e.g. screenshots) under
        `.harness/` for later stages to read back. Default: no-op, seed
        unchanged."""
        return seed

    def image_attachments(
        self,
        seed: dict[str, Any],
        *,
        ancestors: dict[str, dict[str, Any]],
        session: RunSession | None = None,
    ) -> list[ImageAttachment]:
        """Override to attach reference images (e.g. Figma screenshots
        already fetched onto disk by prepare_context, or resolved from an
        ancestor stage's output) to this stage's LLM call. Default: none —
        the overwhelming majority of stages are text-only."""
        return []

    async def run(
        self,
        provider: Provider,
        *,
        seed: dict[str, Any],
        ancestors: dict[str, dict[str, Any]],
        model: str | None = None,
        verification_errors: list[str] | None = None,
        # Accepted for signature parity with CodegenAgent.run() — the
        # orchestrator passes it to every stage uniformly. Unused here since
        # this default implementation makes exactly one provider call, whose
        # cost the orchestrator already accounts for once run() returns.
        on_batch_cost: Callable[[float], None] | None = None,
        session: RunSession | None = None,
    ) -> AgentResult:
        seed = await self.prepare_context(seed, session=session)
        return await self._generate_once(
            provider,
            seed=seed,
            ancestors=ancestors,
            model=model,
            verification_errors=verification_errors,
            session=session,
        )

    async def _generate_once(
        self,
        provider: Provider,
        *,
        seed: dict[str, Any],
        ancestors: dict[str, dict[str, Any]],
        model: str | None,
        verification_errors: list[str] | None,
        session: RunSession | None = None,
    ) -> AgentResult:
        """The one build-prompt -> call-provider -> construct-AgentResult
        round trip. Exists separately from run() so an agent that needs
        several such round trips per run() (e.g. CodegenAgent splitting a
        large project into batches) can reuse it instead of re-implementing
        it — seed here is assumed already passed through prepare_context."""
        prompt = self.build_prompt(seed=seed, ancestors=ancestors, verification_errors=verification_errors)
        schema = self.output_model.model_json_schema()
        provider_result = await provider.generate(
            system_prompt=prompt.system_prompt,
            user_prompt=prompt.user_prompt,
            json_schema=schema,
            model=model,
            images=self.image_attachments(seed, ancestors=ancestors, session=session),
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
            system_prompt=prompt.system_prompt,
            user_prompt=prompt.user_prompt,
        )
