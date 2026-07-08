"""Live smoke test against the real `claude` CLI.

Costs money and requires `claude auth` to already be configured, so it is
skipped by default. Run explicitly with:

    FRONTFORGE_LIVE=1 pytest tests/integration/test_claude_cli_smoke.py
"""

from __future__ import annotations

import os

import pytest

from frontforge.core.orchestrator import Orchestrator
from frontforge.core.session import RunSession
from frontforge.providers.claude_cli import ClaudeCliProvider
from frontforge.shared.types import StageStatus
from frontforge.shared.utils import write_json

pytestmark = pytest.mark.skipif(
    os.environ.get("FRONTFORGE_LIVE") != "1",
    reason="set FRONTFORGE_LIVE=1 to run the live Claude CLI smoke test",
)


@pytest.mark.asyncio
async def test_clarification_stage_runs_against_real_claude_cli(tmp_path):
    session = RunSession.at(tmp_path / "live-demo")
    session.scaffold()
    write_json(session.seed_file, {"raw_requirement": "A simple todo list app for one user."})

    provider = ClaudeCliProvider()
    orchestrator = Orchestrator(session, provider)

    states = await orchestrator.run_all(only="clarification")

    assert states["clarification"].status == StageStatus.DONE
    output = orchestrator.state.load_output("clarification")
    assert output["project_name"]
