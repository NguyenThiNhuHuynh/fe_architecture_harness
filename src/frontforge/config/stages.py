"""The DAG: which stage depends on which. This is the only hard-coded
"workflow" — no stage knows about any other stage, and no stage's *content*
(business rules, page/component choices) is hard-coded here."""

from __future__ import annotations

from dataclasses import dataclass

from frontforge.agents.base import StageAgent
from frontforge.agents.business_analysis.agent import BusinessAnalysisAgent
from frontforge.agents.clarification.agent import ClarificationAgent
from frontforge.agents.codegen.agent import CodegenAgent
from frontforge.agents.component_planning.agent import ComponentPlanningAgent
from frontforge.agents.design_system.agent import DesignSystemAgent
from frontforge.agents.frontend_architecture.agent import FrontendArchitectureAgent
from frontforge.agents.information_architecture.agent import InformationArchitectureAgent
from frontforge.agents.page_planning.agent import PagePlanningAgent
from frontforge.agents.preview.agent import PreviewAgent
from frontforge.agents.quality_review.agent import QualityReviewAgent
from frontforge.agents.requirement.agent import RequirementAgent


@dataclass(frozen=True)
class StageDefinition:
    stage_id: str
    agent_cls: type[StageAgent]
    depends_on: tuple[str, ...] = ()


STAGES: list[StageDefinition] = [
    StageDefinition("clarification", ClarificationAgent, ()),
    StageDefinition("requirement", RequirementAgent, ("clarification",)),
    StageDefinition("business_analysis", BusinessAnalysisAgent, ("requirement",)),
    StageDefinition("design_system", DesignSystemAgent, ("requirement",)),
    StageDefinition("information_architecture", InformationArchitectureAgent, ("business_analysis",)),
    StageDefinition("frontend_architecture", FrontendArchitectureAgent, ("information_architecture",)),
    StageDefinition("page_planning", PagePlanningAgent, ("frontend_architecture",)),
    StageDefinition("component_planning", ComponentPlanningAgent, ("page_planning",)),
    StageDefinition(
        "codegen",
        CodegenAgent,
        ("component_planning", "design_system", "frontend_architecture"),
    ),
    StageDefinition("preview", PreviewAgent, ("codegen",)),
    StageDefinition("quality_review", QualityReviewAgent, ("preview",)),
]


class CycleError(ValueError):
    pass


class StageRegistry:
    def __init__(self, stages: list[StageDefinition] | None = None):
        stages = stages if stages is not None else STAGES
        self._by_id: dict[str, StageDefinition] = {s.stage_id: s for s in stages}
        for stage in stages:
            for dep in stage.depends_on:
                if dep not in self._by_id:
                    raise ValueError(f"stage {stage.stage_id!r} depends on unknown stage {dep!r}")
        self._order: list[str] = self._topo_sort()

    def _topo_sort(self) -> list[str]:
        in_degree = {stage_id: 0 for stage_id in self._by_id}
        for stage in self._by_id.values():
            in_degree[stage.stage_id] = len(stage.depends_on)

        ready = [stage_id for stage_id, degree in in_degree.items() if degree == 0]
        order: list[str] = []
        remaining_deps = {sid: set(s.depends_on) for sid, s in self._by_id.items()}

        while ready:
            ready.sort()  # deterministic order for equally-ready stages
            current = ready.pop(0)
            order.append(current)
            for stage in self._by_id.values():
                if current in remaining_deps[stage.stage_id]:
                    remaining_deps[stage.stage_id].remove(current)
                    if not remaining_deps[stage.stage_id]:
                        ready.append(stage.stage_id)

        if len(order) != len(self._by_id):
            missing = set(self._by_id) - set(order)
            raise CycleError(f"cycle detected in stage graph, involving: {missing}")
        return order

    def get(self, stage_id: str) -> StageDefinition:
        return self._by_id[stage_id]

    def all_ids(self) -> list[str]:
        return list(self._order)

    def create_agent(self, stage_id: str) -> StageAgent:
        return self._by_id[stage_id].agent_cls()

    def dependents_of(self, stage_id: str) -> list[str]:
        return [s.stage_id for s in self._by_id.values() if stage_id in s.depends_on]

    def ancestors_of(self, stage_id: str) -> set[str]:
        seen: set[str] = set()
        queue = list(self._by_id[stage_id].depends_on)
        while queue:
            current = queue.pop()
            if current in seen:
                continue
            seen.add(current)
            queue.extend(self._by_id[current].depends_on)
        return seen

    def ready_stages(self, done_ids: set[str]) -> list[str]:
        """Stages whose dependencies are all done, in deterministic order."""
        return [
            stage_id
            for stage_id in self._order
            if stage_id not in done_ids
            and set(self._by_id[stage_id].depends_on) <= done_ids
        ]
