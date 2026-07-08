from frontforge.agents.base import StageAgent
from frontforge.shared.types import RequirementSpec


class RequirementAgent(StageAgent):
    stage_id = "requirement"
    output_model = RequirementSpec
