import pytest

from frontforge.agents.design_analysis.agent import DesignAnalysisAgent
from frontforge.tools.figma_tool import FigmaDesignData


class FakeFigmaTool:
    def __init__(self, data: FigmaDesignData):
        self.data = data
        self.fetched_urls: list[str] = []

    async def fetch(self, figma_url: str) -> FigmaDesignData:
        self.fetched_urls.append(figma_url)
        return self.data


@pytest.mark.asyncio
async def test_prepare_context_is_noop_without_figma_url():
    agent = DesignAnalysisAgent(figma_tool=FakeFigmaTool(FigmaDesignData()))
    seed = {"raw_requirement": "Build something."}

    enriched = await agent.prepare_context(seed)

    assert enriched == seed
    assert "figma_design_data" not in enriched


@pytest.mark.asyncio
async def test_prepare_context_caches_figma_fetch_across_retries():
    """The same agent instance is reused across every retry of one stage
    attempt (Orchestrator._run_stage), and figma_url doesn't change between
    them — a retry unrelated to Figma data shouldn't refetch it."""
    fake_tool = FakeFigmaTool(FigmaDesignData(pages=[{"name": "Page 1", "frames": []}]))
    agent = DesignAnalysisAgent(figma_tool=fake_tool)
    seed = {"raw_requirement": "Build something.", "figma_url": "https://figma.com/file/ABC123/x"}

    first = await agent.prepare_context(seed)
    second = await agent.prepare_context(seed)

    assert fake_tool.fetched_urls == ["https://figma.com/file/ABC123/x"]  # fetched only once
    assert second["figma_design_data"] == first["figma_design_data"]


@pytest.mark.asyncio
async def test_prepare_context_fetches_and_enriches_seed_when_figma_url_present():
    fake_data = FigmaDesignData(
        pages=[{"name": "Page 1", "frames": ["Login"]}],
        styles=[{"name": "Primary", "styleType": "FILL"}],
        components=[{"name": "Button/Primary"}],
    )
    fake_tool = FakeFigmaTool(fake_data)
    agent = DesignAnalysisAgent(figma_tool=fake_tool)
    seed = {"raw_requirement": "Build something.", "figma_url": "https://figma.com/file/ABC123/x"}

    enriched = await agent.prepare_context(seed)

    assert fake_tool.fetched_urls == ["https://figma.com/file/ABC123/x"]
    assert enriched["figma_design_data"]["pages"] == fake_data.pages
    assert enriched["figma_design_data"]["styles"] == fake_data.styles
    assert enriched["figma_design_data"]["components"] == fake_data.components
    # original seed dict must not be mutated in place
    assert "figma_design_data" not in seed
