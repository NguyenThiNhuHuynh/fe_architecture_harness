from frontforge.agents.base import StageAgent
from frontforge.shared.types import CodegenResult


class CodegenAgent(StageAgent):
    stage_id = "codegen"
    output_model = CodegenResult
