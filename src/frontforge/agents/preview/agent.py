from frontforge.agents.base import StageAgent
from frontforge.shared.types import PreviewResult


class PreviewAgent(StageAgent):
    stage_id = "preview"
    output_model = PreviewResult
