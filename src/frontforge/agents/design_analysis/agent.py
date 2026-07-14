"""Interprets design data already fetched from Figma by FigmaTool — this
agent never calls Figma itself (see providers/claude_cli.py: `--tools ""`
on every call, no agent ever gets tool access). When `seed.figma_url` is
absent, prepare_context() is a no-op and the prompt asks for (and gets) an
empty `source: "none"` result — cheap, so this stage can always run
unconditionally instead of needing a "skip this stage" concept in the DAG.
"""

from __future__ import annotations

from typing import Any

from frontforge.agents.base import StageAgent
from frontforge.shared.types import DesignAnalysisResult
from frontforge.tools.figma_tool import FigmaDesignData, FigmaTool


class DesignAnalysisAgent(StageAgent):
    stage_id = "design_analysis"
    output_model = DesignAnalysisResult

    def __init__(self, prompt_builder=None, figma_tool: FigmaTool | None = None):
        super().__init__(prompt_builder)
        self.figma_tool = figma_tool or FigmaTool()
        # One agent instance is reused across every retry of a stage attempt
        # (see Orchestrator._run_stage), and figma_url never changes between
        # those retries — so a retry caused by e.g. a JSON-schema nit doesn't
        # need to re-fetch identical data from Figma again. Only successful
        # fetches are cached: a failed fetch must still be retried for
        # transient errors (network blips, 5xx) rather than getting stuck.
        self._figma_cache: dict[str, FigmaDesignData] = {}

    async def prepare_context(self, seed: dict[str, Any]) -> dict[str, Any]:
        figma_url = seed.get("figma_url")
        if not figma_url:
            return seed

        data = self._figma_cache.get(figma_url)
        if data is None:
            data = await self.figma_tool.fetch(figma_url)
            self._figma_cache[figma_url] = data

        seed = dict(seed)
        seed["figma_design_data"] = {
            "pages": data.pages,
            "styles": data.styles,
            "components": data.components,
        }
        return seed
