from frontforge.agents.base import StageAgent
from frontforge.shared.types import IAResult


class InformationArchitectureAgent(StageAgent):
    stage_id = "information_architecture"
    output_model = IAResult
