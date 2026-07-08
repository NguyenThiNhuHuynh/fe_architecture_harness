from frontforge.agents.base import StageAgent
from frontforge.shared.types import ProjectBrief


class ClarificationAgent(StageAgent):
    stage_id = "clarification"
    output_model = ProjectBrief
