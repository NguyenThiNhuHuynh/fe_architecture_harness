"""Maps each stage to the verifiers that decide its pass/fail — the
VerificationEngine itself has no idea what "codegen" or "requirement" mean."""

from __future__ import annotations

from frontforge.core.verification.build_verifier import BuildVerifier
from frontforge.core.verification.engine import Verifier
from frontforge.core.verification.eslint_verifier import ESLintVerifier
from frontforge.core.verification.json_schema_verifier import JsonSchemaVerifier
from frontforge.core.verification.security_verifier import SecurityScanVerifier
from frontforge.core.verification.typescript_verifier import TypeScriptVerifier
from frontforge.shared.types import STAGE_OUTPUT_MODELS


def build_stage_verifiers() -> dict[str, list[Verifier]]:
    verifiers: dict[str, list[Verifier]] = {
        stage_id: [JsonSchemaVerifier(model)] for stage_id, model in STAGE_OUTPUT_MODELS.items()
    }
    # SecurityScanVerifier runs first so a hit is reported before the (much
    # slower) tsc/eslint/build passes even start.
    verifiers["codegen"].extend(
        [SecurityScanVerifier(), TypeScriptVerifier(), ESLintVerifier(), BuildVerifier()]
    )
    return verifiers
