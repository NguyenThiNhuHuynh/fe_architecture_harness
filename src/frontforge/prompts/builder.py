"""Assembles system/user prompts for a stage from small markdown/Jinja files
instead of long inline string literals — see prompts/<stage_id>/{system.md,
user.md.j2,examples.md}.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from frontforge.shared.types import PromptSpec

PROMPTS_ROOT = Path(__file__).parent


def _pretty_json(obj: Any) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False, default=str)


class PromptBuilder:
    def __init__(self, prompts_root: Path | None = None):
        self.prompts_root = prompts_root or PROMPTS_ROOT

    def build(
        self,
        stage_id: str,
        *,
        seed: dict[str, Any] | None = None,
        ancestors: dict[str, dict[str, Any]] | None = None,
        verification_errors: list[str] | None = None,
    ) -> PromptSpec:
        stage_dir = self.prompts_root / stage_id
        if not stage_dir.is_dir():
            raise FileNotFoundError(f"No prompt directory for stage {stage_id!r}: {stage_dir}")

        system_path = stage_dir / "system.md"
        examples_path = stage_dir / "examples.md"

        system_prompt = system_path.read_text(encoding="utf-8") if system_path.exists() else ""
        if examples_path.exists():
            examples = examples_path.read_text(encoding="utf-8").strip()
            if examples:
                system_prompt = f"{system_prompt.strip()}\n\n## Examples\n\n{examples}"

        env = Environment(
            loader=FileSystemLoader([str(stage_dir), str(self.prompts_root / "_shared")]),
            autoescape=False,
            undefined=StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
        )
        env.filters["pretty_json"] = _pretty_json
        template = env.get_template("user.md.j2")
        user_prompt = template.render(
            seed=seed or {},
            ancestors=ancestors or {},
            verification_errors=verification_errors or [],
        )

        return PromptSpec(system_prompt=system_prompt.strip(), user_prompt=user_prompt.strip())
