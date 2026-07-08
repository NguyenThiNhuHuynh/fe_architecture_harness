from frontforge.agents.base import StageAgent
from frontforge.shared.types import FrontendArchitectureSpec


class FrontendArchitectureAgent(StageAgent):
    stage_id = "frontend_architecture"
    output_model = FrontendArchitectureSpec
