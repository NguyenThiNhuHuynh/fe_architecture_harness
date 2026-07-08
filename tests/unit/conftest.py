from __future__ import annotations

import pytest

from frontforge.core.session import RunSession

from fixtures_data import ScriptedProvider


@pytest.fixture
def session(tmp_path) -> RunSession:
    s = RunSession.at(tmp_path / "demo-project")
    s.scaffold()
    return s


@pytest.fixture
def scripted_provider() -> ScriptedProvider:
    return ScriptedProvider()
