import pytest

from fixtures_data import ScriptedProvider
from frontforge.agents.codegen.agent import CodegenAgent
from frontforge.providers.base import Provider
from frontforge.shared.types import ProviderResult


def _page(path: str) -> dict:
    return {
        "path": path,
        "name": path,
        "purpose": "test",
        "layout": "Default",
        "sections": [],
        "data_requirements": [],
        "roles_allowed": [],
    }


@pytest.mark.asyncio
async def test_small_project_uses_a_single_call():
    agent = CodegenAgent()
    provider = ScriptedProvider(
        {"CodegenResult": {"files": [{"path": "package.json", "content": "{}", "language": "json"}], "setup_instructions": []}}
    )
    ancestors = {"page_planning": {"pages": [_page("/")]}, "component_planning": {"components": []}}

    result = await agent.run(provider, seed={}, ancestors=ancestors, model="test")

    assert len(provider.calls) == 1
    assert result.output["files"][0]["path"] == "package.json"


@pytest.mark.asyncio
async def test_large_project_splits_into_batches_and_merges_files():
    agent = CodegenAgent()
    pages = [_page(f"/page-{i}") for i in range(6)]  # > BATCH_SIZE(4) -> foundation + 2 page batches
    components = [
        {"name": "Layout", "kind": "layout", "props": [], "used_in_pages": [], "depends_on_components": []},
        {"name": "PageOneWidget", "kind": "feature", "props": [], "used_in_pages": ["/page-0"], "depends_on_components": []},
    ]
    ancestors = {"page_planning": {"pages": pages}, "component_planning": {"components": components}}

    batch_responses = [
        {"files": [{"path": "package.json", "content": "{}", "language": "json"}], "setup_instructions": ["npm install"]},
        {"files": [{"path": f"app/page-{i}.tsx", "content": "x", "language": "tsx"} for i in range(4)], "setup_instructions": []},
        {"files": [{"path": f"app/page-{i}.tsx", "content": "x", "language": "tsx"} for i in range(4, 6)], "setup_instructions": []},
    ]
    provider = ScriptedProvider({"CodegenResult": batch_responses})

    result = await agent.run(provider, seed={}, ancestors=ancestors, model="test")

    assert len(provider.calls) == 3  # foundation + 2 page batches
    all_paths = {f["path"] for f in result.output["files"]}
    assert all_paths == {"package.json"} | {f"app/page-{i}.tsx" for i in range(6)}
    assert result.output["setup_instructions"] == ["npm install"]

    # cost/duration are summed across all 3 internal calls, not just the last
    assert result.provider_result.cost_usd == pytest.approx(0.02 * 3)
    assert result.provider_result.duration_ms == 3  # 1ms per ScriptedProvider call x 3


@pytest.mark.asyncio
async def test_shared_feature_component_is_generated_by_only_one_batch():
    """A feature component used by pages split across two different batches
    used to be included in both batches' prompts, so it got independently
    regenerated twice and one copy silently overwrote the other on merge."""
    agent = CodegenAgent()
    pages = [_page(f"/page-{i}") for i in range(6)]  # foundation + batch-1 (0-3) + batch-2 (4-5)
    components = [
        {
            "name": "Shared",
            "kind": "feature",
            "props": [],
            "used_in_pages": ["/page-0", "/page-4"],  # spans both page batches
            "depends_on_components": [],
        },
    ]
    ancestors = {"page_planning": {"pages": pages}, "component_planning": {"components": components}}

    batches = agent._plan_batches(ancestors)

    batches_with_shared = [b for b in batches if any(c["name"] == "Shared" for c in b["components"])]
    assert len(batches_with_shared) == 1


@pytest.mark.asyncio
async def test_retry_feedback_reaches_every_batch_not_just_the_first():
    """Verification only runs once per whole-stage attempt, after all
    batches have merged — so a retry's feedback could be about any batch's
    files, not only the foundation batch. It must reach every batch."""
    agent = CodegenAgent()
    pages = [_page(f"/page-{i}") for i in range(6)]  # foundation + 2 page batches
    ancestors = {"page_planning": {"pages": pages}, "component_planning": {"components": []}}
    provider = ScriptedProvider({"CodegenResult": [{"files": [], "setup_instructions": []}] * 3})

    await agent.run(
        provider,
        seed={},
        ancestors=ancestors,
        model="test",
        verification_errors=["tsc: broken import in app/page-4.tsx"],
    )

    assert len(provider.calls) == 3
    for call in provider.calls:
        assert "broken import in app/page-4.tsx" in call["user_prompt"]


class _TokenReportingProvider(Provider):
    """Like ScriptedProvider, but each call reports real input/output token
    usage — needed to verify the batched merge sums them across batches."""

    def __init__(self, per_call_tokens: list[tuple[int, int]]):
        self.per_call_tokens = per_call_tokens
        self.calls: list[dict] = []

    async def generate(self, *, system_prompt, user_prompt, json_schema=None, model=None, timeout=None):
        index = len(self.calls)
        self.calls.append({"system_prompt": system_prompt, "user_prompt": user_prompt})
        input_tokens, output_tokens = self.per_call_tokens[index]
        data = {"files": [], "setup_instructions": []}
        return ProviderResult(
            raw_text="{}",
            data=data,
            model=model or "test",
            duration_ms=1,
            cost_usd=0.02,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )


@pytest.mark.asyncio
async def test_batched_merge_sums_token_usage_across_batches():
    agent = CodegenAgent()
    pages = [_page(f"/page-{i}") for i in range(6)]  # foundation + 2 page batches = 3 calls
    ancestors = {"page_planning": {"pages": pages}, "component_planning": {"components": []}}
    provider = _TokenReportingProvider([(100, 50), (200, 80), (150, 60)])

    result = await agent.run(provider, seed={}, ancestors=ancestors, model="test")

    assert result.provider_result.input_tokens == 100 + 200 + 150
    assert result.provider_result.output_tokens == 50 + 80 + 60


@pytest.mark.asyncio
async def test_batched_merge_gives_up_on_token_usage_if_any_batch_is_missing_it():
    agent = CodegenAgent()
    pages = [_page(f"/page-{i}") for i in range(6)]
    ancestors = {"page_planning": {"pages": pages}, "component_planning": {"components": []}}
    provider = ScriptedProvider(
        {"CodegenResult": [{"files": [], "setup_instructions": []}] * 3}
    )  # ScriptedProvider never sets input_tokens/output_tokens

    result = await agent.run(provider, seed={}, ancestors=ancestors, model="test")

    assert result.provider_result.input_tokens is None
    assert result.provider_result.output_tokens is None


@pytest.mark.asyncio
async def test_batches_are_scoped_and_know_about_earlier_files():
    agent = CodegenAgent()
    pages = [_page(f"/page-{i}") for i in range(5)]  # foundation + 2 page batches (4, 1)
    ancestors = {"page_planning": {"pages": pages}, "component_planning": {"components": []}}
    provider = ScriptedProvider(
        {"CodegenResult": [{"files": [], "setup_instructions": []}] * 3}
    )

    await agent.run(provider, seed={}, ancestors=ancestors, model="test")

    # batch 2's prompt should only mention its own 1 remaining page, not all 5
    second_batch_prompt = provider.calls[1]["user_prompt"]
    assert "/page-0" in second_batch_prompt
    assert "/page-4" not in second_batch_prompt

    third_batch_prompt = provider.calls[2]["user_prompt"]
    assert "/page-4" in third_batch_prompt
    assert "/page-0" not in third_batch_prompt
