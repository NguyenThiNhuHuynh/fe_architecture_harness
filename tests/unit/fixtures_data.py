from __future__ import annotations

import json
from typing import Any

from frontforge.providers.base import Provider
from frontforge.shared.types import STAGE_OUTPUT_MODELS, ProviderResult

VALID_OUTPUTS: dict[str, dict[str, Any]] = {
    "clarification": {
        "project_name": "Demo",
        "project_type": "Web App",
        "summary": "A demo app.",
        "target_users": ["User"],
        "modules": ["Home"],
        "tech_preferences": ["Next.js"],
        "constraints": [],
        "assumptions": [],
    },
    "requirement": {
        "functional_requirements": ["Show home page"],
        "non_functional_requirements": ["Must be responsive"],
        "roles": [{"name": "User", "description": "A user", "permissions": []}],
        "entities": [{"name": "Item", "description": "An item", "fields": ["id", "name"]}],
        "features": [{"name": "Home", "description": "Home page", "priority": "must"}],
        "assumptions": [],
    },
    "business_analysis": {
        "personas": [{"name": "Alice", "role": "User", "goals": ["browse"], "pain_points": []}],
        "user_journeys": [{"name": "Browse", "persona": "Alice", "steps": ["Visit home"]}],
        "business_rules": [],
        "kpis": [],
    },
    "design_system": {
        "design_principles": ["Simplicity"],
        "color_tokens": [{"name": "primary", "value": "#000", "usage": "buttons"}],
        "typography": {"font_family": "Inter", "scale": ["14px", "16px"]},
        "spacing_scale": ["4px", "8px"],
        "base_components": ["Button"],
        "themes": ["light"],
    },
    "information_architecture": {
        "sitemap": [{"path": "/", "name": "Home", "children": []}],
        "navigation": [{"label": "Home", "path": "/", "roles": ["User"]}],
        "content_types": ["Page"],
    },
    "frontend_architecture": {
        "framework": "Next.js",
        "rendering_strategy": "SSR",
        "routing_strategy": "App Router",
        "state_management": "React Context",
        "data_fetching_strategy": "Server Components",
        "folder_structure": ["app/", "components/"],
        "key_libraries": ["tailwindcss"],
    },
    "page_planning": {
        "pages": [
            {
                "path": "/",
                "name": "Home",
                "purpose": "Landing",
                "layout": "Default",
                "sections": ["Hero"],
                "data_requirements": [],
                "roles_allowed": ["User"],
            }
        ]
    },
    "component_planning": {
        "components": [
            {
                "name": "Hero",
                "kind": "ui",
                "props": ["title"],
                "used_in_pages": ["/"],
                "depends_on_components": [],
            }
        ]
    },
    "codegen": {
        "files": [{"path": "package.json", "content": "{}", "language": "json"}],
        "setup_instructions": ["npm install"],
    },
    "preview": {"notes": ["Looks fine"], "issues_found": []},
    "quality_review": {"score": 90, "passed": True, "issues": [], "recommendations": []},
}


class ScriptedProvider(Provider):
    """Test double for Provider — keyed by the JSON schema's `title`
    (== the Pydantic model's class name) so it works no matter which
    stage/agent is asking, without ever shelling out to `claude`."""

    def __init__(self, outputs_by_title: dict[str, Any] | None = None):
        self.outputs_by_title = outputs_by_title or {
            model.__name__: VALID_OUTPUTS[stage_id] for stage_id, model in STAGE_OUTPUT_MODELS.items()
        }
        self._counters: dict[str, int] = {}
        self.calls: list[dict[str, Any]] = []

    async def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        json_schema: dict[str, Any] | None = None,
        model: str | None = None,
        timeout: int | None = None,
    ) -> ProviderResult:
        self.calls.append(
            {"system_prompt": system_prompt, "user_prompt": user_prompt, "json_schema": json_schema}
        )
        title = (json_schema or {}).get("title", "")
        value = self.outputs_by_title.get(title, {})
        if isinstance(value, list):
            index = min(self._counters.get(title, 0), len(value) - 1)
            self._counters[title] = self._counters.get(title, 0) + 1
            data = value[index]
        else:
            data = value
        return ProviderResult(
            raw_text=json.dumps(data), data=data, model=model or "mock", duration_ms=1, cost_usd=0.0
        )
