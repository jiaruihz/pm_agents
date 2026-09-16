import json

import pytest

from src.platform.storage.jsonl import (
    append_jsonl_row,
    rewrite_jsonl_atomic,
    write_json_atomic,
)


def test_append_jsonl_row_writes_complete_records(tmp_path) -> None:
    path = tmp_path / "rows.jsonl"
    append_jsonl_row(path, {"id": 1})
    append_jsonl_row(path, {"id": 2})
    assert [json.loads(line) for line in path.read_text().splitlines()] == [
        {"id": 1},
        {"id": 2},
    ]


def test_append_jsonl_row_refuses_partial_tail(tmp_path) -> None:
    path = tmp_path / "rows.jsonl"
    path.write_bytes(b'{"id":')
    with pytest.raises(RuntimeError, match="partial trailing row"):
        append_jsonl_row(path, {"id": 2})
    assert path.read_bytes() == b'{"id":'


def test_atomic_mutable_json_and_jsonl_views(tmp_path) -> None:
    json_path = tmp_path / "latest.json"
    jsonl_path = tmp_path / "derived.jsonl"
    write_json_atomic(json_path, {"status": "ok", "n": 2})
    rewrite_jsonl_atomic(jsonl_path, [{"id": 1}, {"id": 2}])
    assert json.loads(json_path.read_text()) == {"status": "ok", "n": 2}
    assert [json.loads(line) for line in jsonl_path.read_text().splitlines()] == [
        {"id": 1},
        {"id": 2},
    ]
    assert not list(tmp_path.glob("*.tmp"))
