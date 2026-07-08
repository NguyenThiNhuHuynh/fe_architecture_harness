from frontforge.agents.base import StageAgent
from frontforge.shared.types import PagePlan


class PagePlanningAgent(StageAgent):
    stage_id = "page_planning"
    output_model = PagePlan
