from frontforge.prompts.builder import PromptBuilder

from fixtures_data import VALID_OUTPUTS


def test_clarification_prompt_includes_raw_requirement():
    builder = PromptBuilder()
    spec = builder.build("clarification", seed={"raw_requirement": "Build a recruitment site."})
    assert "Build a recruitment site." in spec.user_prompt
    assert spec.system_prompt  # non-empty


def test_downstream_prompt_includes_ancestor_output():
    builder = PromptBuilder()
    spec = builder.build(
        "requirement",
        ancestors={"clarification": VALID_OUTPUTS["clarification"]},
    )
    assert "Demo" in spec.user_prompt  # project_name from the ancestor output


def test_verification_errors_are_surfaced_on_retry():
    builder = PromptBuilder()
    spec = builder.build(
        "requirement",
        ancestors={"clarification": VALID_OUTPUTS["clarification"]},
        verification_errors=["[json_schema] roles: field required"],
    )
    assert "roles: field required" in spec.user_prompt


def test_unknown_stage_raises():
    builder = PromptBuilder()
    try:
        builder.build("not_a_stage")
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("expected FileNotFoundError")
