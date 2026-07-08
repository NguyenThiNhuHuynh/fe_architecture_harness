from frontforge.agents.base import StageAgent
from frontforge.shared.types import QualityReviewResult


class QualityReviewAgent(StageAgent):
    stage_id = "quality_review"
    output_model = QualityReviewResult
