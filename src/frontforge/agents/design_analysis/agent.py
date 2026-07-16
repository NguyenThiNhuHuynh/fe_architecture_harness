"""Interprets design data already fetched from Figma by FigmaTool — this
agent never calls Figma itself (see providers/claude_cli.py: `--tools ""`
on every call, no agent ever gets tool access). When `seed.figma_url` is
absent, prepare_context() is a no-op and the prompt asks for (and gets) an
empty `source: "none"` result — cheap, so this stage can always run
unconditionally instead of needing a "skip this stage" concept in the DAG.
"""

from __future__ import annotations

import base64
from typing import Any

from frontforge.agents.base import StageAgent
from frontforge.core.session import RunSession
from frontforge.shared.types import DesignAnalysisResult, ImageAttachment
from frontforge.shared.utils import ensure_dir
from frontforge.tools.figma_tool import FigmaDesignData, FigmaTool, extract_file_key


class DesignAnalysisAgent(StageAgent):
    stage_id = "design_analysis"
    output_model = DesignAnalysisResult

    # Screenshots are only attached as a last-resort fallback (no published
    # styles/components to work from) — capped well below MAX_IMAGE_FRAMES
    # to keep that one call's token cost bounded even on a large file.
    MAX_INFERENCE_IMAGES = 8

    def __init__(self, prompt_builder=None, figma_tool: FigmaTool | None = None):
        super().__init__(prompt_builder)
        self.figma_tool = figma_tool or FigmaTool()
        # One agent instance is reused across every retry of a stage attempt
        # (see Orchestrator._run_stage), and figma_url never changes between
        # those retries — so a retry caused by e.g. a JSON-schema nit doesn't
        # need to re-fetch identical data from Figma again. Only successful
        # fetches are cached: a failed fetch must still be retried for
        # transient errors (network blips, 5xx) rather than getting stuck.
        # Frame screenshots (if any) are fetched once and written directly
        # onto this cached `data.pages`, so a retry sees them too instead of
        # re-downloading.
        self._figma_cache: dict[str, FigmaDesignData] = {}

    async def prepare_context(
        self, seed: dict[str, Any], *, session: RunSession | None = None
    ) -> dict[str, Any]:
        figma_url = seed.get("figma_url")
        if not figma_url:
            return seed

        data = self._figma_cache.get(figma_url)
        if data is None:
            data = await self.figma_tool.fetch(figma_url)
            if session is not None:
                await self._fetch_frame_screenshots(figma_url, data, session)
            self._figma_cache[figma_url] = data

        seed = dict(seed)
        seed["figma_design_data"] = {
            "pages": data.pages,
            "styles": data.styles,
            "components": data.components,
        }
        return seed

    async def _fetch_frame_screenshots(
        self, figma_url: str, data: FigmaDesignData, session: RunSession
    ) -> None:
        """Best-effort: renders each frame to a PNG under
        `.harness/figma_assets/` and records its path back onto `data.pages`
        so later stages (design_analysis's own visual-inference fallback,
        and codegen via page_planning's `figma_frame_ref`) can load it.
        A frame that fails to render is simply left without an image_path —
        never fatal to the stage.
        """
        node_ids = [
            frame["id"]
            for page in data.pages
            for frame in page.get("frames", [])
            if frame.get("id")
        ]
        if not node_ids:
            return
        file_key = extract_file_key(figma_url)
        images = await self.figma_tool.fetch_frame_images(file_key, node_ids)
        if not images:
            return
        ensure_dir(session.figma_assets_dir)
        for page in data.pages:
            for frame in page.get("frames", []):
                image_bytes = images.get(frame.get("id"))
                if image_bytes is None:
                    continue
                file_name = f"{frame['id'].replace(':', '_')}.png"
                (session.figma_assets_dir / file_name).write_bytes(image_bytes)
                frame["image_path"] = file_name

    def image_attachments(
        self,
        seed: dict[str, Any],
        *,
        ancestors: dict[str, dict[str, Any]],
        session: RunSession | None = None,
    ) -> list[ImageAttachment]:
        """Fallback only: when Figma had no published styles/components to
        interpret, attach screenshots so the prompt's visual-inference rule
        (see system.md) has something to work from."""
        figma_data = seed.get("figma_design_data")
        if not figma_data or session is None:
            return []
        if figma_data.get("styles") or figma_data.get("components"):
            return []

        images: list[ImageAttachment] = []
        for page in figma_data.get("pages", []):
            for frame in page.get("frames", []):
                if len(images) >= self.MAX_INFERENCE_IMAGES:
                    return images
                image_path = frame.get("image_path")
                if not image_path:
                    continue
                image_file = session.figma_assets_dir / image_path
                if not image_file.exists():
                    continue
                images.append(
                    ImageAttachment(
                        label=frame.get("name", image_path),
                        media_type="image/png",
                        base64_data=base64.b64encode(image_file.read_bytes()).decode("ascii"),
                    )
                )
        return images
