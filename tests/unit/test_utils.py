from frontforge.shared.utils import read_json, write_json


def test_write_json_then_read_json_roundtrip(tmp_path):
    path = tmp_path / "nested" / "state.json"
    write_json(path, {"a": 1, "b": [1, 2, 3]})

    assert read_json(path) == {"a": 1, "b": [1, 2, 3]}


def test_write_json_leaves_no_leftover_temp_file(tmp_path):
    path = tmp_path / "state.json"
    write_json(path, {"x": 1})

    leftovers = [p for p in tmp_path.iterdir() if p.name != "state.json"]
    assert leftovers == []


def test_write_json_overwrites_atomically(tmp_path):
    path = tmp_path / "state.json"
    write_json(path, {"version": 1})
    write_json(path, {"version": 2})

    assert read_json(path) == {"version": 2}
