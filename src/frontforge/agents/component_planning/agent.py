from frontforge.agents.base import StageAgent
from frontforge.shared.types import ComponentPlan


class ComponentPlanningAgent(StageAgent):
    stage_id = "component_planning"
    output_model = ComponentPlan
