"""Constants shared across the harness."""

HARNESS_DIR_NAME = ".harness"
STATE_FILE_NAME = "state.json"
OUTPUTS_DIR_NAME = "outputs"
LOGS_DIR_NAME = "logs"
GENERATED_DIR_NAME = "generated"
SEED_FILE_NAME = "seed.json"
FIGMA_ASSETS_DIR_NAME = "figma_assets"

STAGE_ORDER: list[str] = [
    "clarification",
    "requirement",
    "business_analysis",
    "design_system",
    "information_architecture",
    "frontend_architecture",
    "page_planning",
    "component_planning",
    "codegen",
    "preview",
    "quality_review",
]

DEFAULT_MODEL = "sonnet"
DEFAULT_MAX_RETRIES = 2
DEFAULT_TIMEOUT_SECONDS = 600
