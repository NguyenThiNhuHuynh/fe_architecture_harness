import pytest

from frontforge.config.verification import build_stage_verifiers
from frontforge.core.verification.engine import VerificationEngine
from frontforge.core.verification.json_schema_verifier import JsonSchemaVerifier
from frontforge.shared.types import ProjectBrief

from fixtures_data import VALID_OUTPUTS


@pytest.mark.asyncio
async def test_json_schema_verifier_passes_on_valid_output(session):
    verifier = JsonSchemaVerifier(ProjectBrief)
    issues = await verifier.verify(stage_id="clarification", output=VALID_OUTPUTS["clarification"], session=session)
    assert issues == []


@pytest.mark.asyncio
async def test_json_schema_verifier_fails_on_missing_field(session):
    verifier = JsonSchemaVerifier(ProjectBrief)
    bad_output = {k: v for k, v in VALID_OUTPUTS["clarification"].items() if k != "project_name"}
    issues = await verifier.verify(stage_id="clarification", output=bad_output, session=session)
    assert len(issues) == 1
    assert issues[0].severity == "error"


@pytest.mark.asyncio
async def test_engine_passes_when_all_verifiers_pass(session):
    engine = VerificationEngine(build_stage_verifiers())
    result = await engine.run("clarification", VALID_OUTPUTS["clarification"], session)
    assert result.passed
    assert result.issues == []


@pytest.mark.asyncio
async def test_engine_fails_when_a_verifier_fails(session):
    engine = VerificationEngine(build_stage_verifiers())
    result = await engine.run("requirement", {"functional_requirements": "not-a-list"}, session)
    assert not result.passed
    assert any(issue.severity == "error" for issue in result.issues)
