from frontforge.agents.base import StageAgent
from frontforge.shared.types import DesignSystemSpec


class DesignSystemAgent(StageAgent):
    stage_id = "design_system"
    output_model = DesignSystemSpec
