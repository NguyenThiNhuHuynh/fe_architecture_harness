from frontforge.agents.base import StageAgent
from frontforge.shared.types import BusinessAnalysisResult


class BusinessAnalysisAgent(StageAgent):
    stage_id = "business_analysis"
    output_model = BusinessAnalysisResult
