"""codegen overrides run() to split large projects into multiple smaller
generation calls instead of one giant one-shot response — this is a direct
fix for a real defect seen in testing: a 27-page project asked to generate
in a single call came back with 17 of those pages simply missing, because
one response can't reliably hold an entire large project's worth of files.

Small projects are unaffected: with `len(pages) <= BATCH_SIZE`, this takes
exactly the same single-call path as every other agent (see _run_single,
which delegates to StageAgent._generate_once()).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from frontforge.agents.base import StageAgent
from frontforge.providers.base import Provider
from frontforge.shared.types import AgentResult, CodegenResult, ProviderResult


class CodegenAgent(StageAgent):
    stage_id = "codegen"
    output_model = CodegenResult

    # Pages per generation call. Kept small enough that one batch's response
    # (files for ~4 pages plus their feature components) comfortably fits
    # one turn, without needing to tune this per-project.
    BATCH_SIZE = 4

    async def run(
        self,
        provider: Provider,
        *,
        seed: dict[str, Any],
        ancestors: dict[str, dict[str, Any]],
        model: str | None = None,
        verification_errors: list[str] | None = None,
        on_batch_cost: Callable[[float], None] | None = None,
    ) -> AgentResult:
        seed = await self.prepare_context(seed)
        pages = ancestors.get("page_planning", {}).get("pages", [])

        if len(pages) <= self.BATCH_SIZE:
            return await self._run_single(
                provider, seed=seed, ancestors=ancestors, model=model,
                verification_errors=verification_errors,
            )
        return await self._run_batched(
            provider, seed=seed, ancestors=ancestors, model=model,
            verification_errors=verification_errors,
            on_batch_cost=on_batch_cost,
        )

    async def _run_single(
        self,
        provider: Provider,
        *,
        seed: dict[str, Any],
        ancestors: dict[str, dict[str, Any]],
        model: str | None,
        verification_errors: list[str] | None,
    ) -> AgentResult:
        """One call, one response, used whenever the project is small enough
        to fit — reuses the same build-prompt -> call-provider ->
        construct-AgentResult logic as every other agent (StageAgent's
        default path) instead of duplicating it. `seed` was already passed
        through prepare_context() by run() before reaching here."""
        return await self._generate_once(
            provider, seed=seed, ancestors=ancestors, model=model, verification_errors=verification_errors
        )

    def _plan_batches(self, ancestors: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        pages = ancestors.get("page_planning", {}).get("pages", [])
        components = ancestors.get("component_planning", {}).get("components", [])

        foundation_components = [c for c in components if c.get("kind") in ("layout", "ui")]
        batches: list[dict[str, Any]] = [
            {"label": "foundation", "pages": [], "components": foundation_components}
        ]

        # A feature component used across pages in different batches must be
        # generated exactly once — assigning it to every batch that touches
        # it would have each batch's independent LLM call regenerate the same
        # file, with whichever batch merges last silently overwriting the rest.
        assigned_feature_components: set[str] = set()
        for i in range(0, len(pages), self.BATCH_SIZE):
            group = pages[i : i + self.BATCH_SIZE]
            group_paths = {p.get("path") for p in group}
            feature_components = [
                c
                for c in components
                if c.get("kind") == "feature"
                and c.get("name") not in assigned_feature_components
                and group_paths & set(c.get("used_in_pages", []))
            ]
            assigned_feature_components.update(c.get("name") for c in feature_components)
            batches.append(
                {
                    "label": f"pages-batch-{i // self.BATCH_SIZE + 1}",
                    "pages": group,
                    "components": feature_components,
                }
            )
        return batches

    async def _run_batched(
        self,
        provider: Provider,
        *,
        seed: dict[str, Any],
        ancestors: dict[str, dict[str, Any]],
        model: str | None,
        verification_errors: list[str] | None,
        on_batch_cost: Callable[[float], None] | None = None,
    ) -> AgentResult:
        batches = self._plan_batches(ancestors)
        schema = self.output_model.model_json_schema()

        all_files: list[dict[str, Any]] = []
        setup_instructions: list[str] = []
        total_cost_usd = 0.0
        total_duration_ms = 0
        # Summed only while every batch so far reported real usage — the
        # moment one batch's envelope lacks a `usage` block, the running
        # total would understate the true count, so it's abandoned to None
        # rather than silently reporting a partial/wrong number.
        total_input_tokens: int | None = 0
        total_output_tokens: int | None = 0
        last_raw_text = ""
        last_model = model or ""
        batch_system_prompts: list[str] = []
        batch_user_prompts: list[str] = []

        for index, batch in enumerate(batches):
            batch_seed = dict(seed)
            batch_seed["_codegen_batch"] = {
                "label": batch["label"],
                "pages": batch["pages"],
                "components": batch["components"],
                "already_generated_paths": [f["path"] for f in all_files],
                "batch_number": index + 1,
                "total_batches": len(batches),
            }
            prompt = self.build_prompt(
                seed=batch_seed,
                ancestors=ancestors,
                # Verification only runs once per whole-stage attempt, after
                # every batch has merged — so the previous attempt's errors
                # could belong to any batch's files, not just the first.
                # Broadcast to all of them rather than guessing which one.
                verification_errors=verification_errors,
            )
            result = await provider.generate(
                system_prompt=prompt.system_prompt,
                user_prompt=prompt.user_prompt,
                json_schema=schema,
                model=model,
            )
            # Record spend/duration before the data-shape check below — the
            # call already happened and was billed even if its response
            # didn't parse, and the orchestrator's cap must see it in real
            # time rather than losing it if this or a later batch raises.
            batch_cost = result.cost_usd or 0.0
            total_duration_ms += result.duration_ms
            if on_batch_cost is not None:
                on_batch_cost(batch_cost)

            if result.data is None:
                raise ValueError(
                    f"stage {self.stage_id!r}: batch {batch['label']!r} did not return structured JSON data"
                )

            all_files.extend(result.data.get("files", []))
            setup_instructions.extend(result.data.get("setup_instructions", []))
            total_cost_usd += batch_cost
            if total_input_tokens is None or result.input_tokens is None:
                total_input_tokens = None
            else:
                total_input_tokens += result.input_tokens
            if total_output_tokens is None or result.output_tokens is None:
                total_output_tokens = None
            else:
                total_output_tokens += result.output_tokens
            last_raw_text = result.raw_text
            last_model = result.model
            batch_system_prompts.append(f"### Batch {index + 1} ({batch['label']})\n{prompt.system_prompt}")
            batch_user_prompts.append(f"### Batch {index + 1} ({batch['label']})\n{prompt.user_prompt}")

        merged_data = {"files": all_files, "setup_instructions": setup_instructions}
        merged_provider_result = ProviderResult(
            raw_text=last_raw_text,
            data=merged_data,
            model=last_model,
            duration_ms=total_duration_ms,
            cost_usd=total_cost_usd,
            input_tokens=total_input_tokens,
            output_tokens=total_output_tokens,
        )
        return AgentResult(
            stage_id=self.stage_id,
            output=merged_data,
            provider_result=merged_provider_result,
            system_prompt="\n\n".join(batch_system_prompts),
            user_prompt="\n\n".join(batch_user_prompts),
        )
