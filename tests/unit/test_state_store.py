from frontforge.core.state_store import StateStore
from frontforge.shared.types import StageStatus


def test_update_and_get_roundtrip(session):
    store = StateStore(session)
    store.update("requirement", status=StageStatus.DONE, input_hash="abc")

    reloaded = StateStore(session)  # forces a re-read from disk
    state = reloaded.get("requirement")
    assert state.status == StageStatus.DONE
    assert state.input_hash == "abc"


def test_get_unknown_stage_defaults_to_pending(session):
    store = StateStore(session)
    state = store.get("never_run")
    assert state.status == StageStatus.PENDING


def test_save_and_load_output(session):
    store = StateStore(session)
    store.save_output("requirement", {"functional_requirements": ["a"]})

    assert store.load_output("requirement") == {"functional_requirements": ["a"]}
    assert store.load_output("missing") is None


def test_outputs_for_only_returns_existing(session):
    store = StateStore(session)
    store.save_output("requirement", {"x": 1})
    outputs = store.outputs_for(["requirement", "business_analysis"])
    assert outputs == {"requirement": {"x": 1}}


def test_mark_dirty_cascade(session):
    store = StateStore(session)
    for stage_id in ("a", "b", "c", "d"):
        store.update(stage_id, status=StageStatus.DONE)

    dependents = {"a": ["b"], "b": ["c"], "c": ["d"], "d": []}
    affected = store.mark_dirty_cascade("a", lambda sid: dependents[sid])

    assert set(affected) == {"a", "b", "c", "d"}
    for stage_id in affected:
        assert store.get(stage_id).status == StageStatus.DIRTY
