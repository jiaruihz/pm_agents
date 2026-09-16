from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT
    / "scripts/analysis/wallet_weather/persist_external_wallet_weather_jrs_v1.py"
)
SPEC = importlib.util.spec_from_file_location("persist_external_wallet_weather_jrs_v1", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
persist = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(persist)


def test_exact_destination_does_not_repeat_copy_checksum(tmp_path, monkeypatch):
    artifact = tmp_path / "checkpoint.json"
    artifact.write_text('{"complete": true}\n', encoding="utf-8")

    def fail_if_hashed(_path: Path) -> str:
        raise AssertionError("same-path direct-to-JRS registration must not rehash")

    monkeypatch.setattr(persist, "file_sha256", fail_if_hashed)
    result = persist.copy_verified(artifact, artifact)

    assert result == {
        "path": str(artifact),
        "bytes": 0,
        "sha256": None,
        "copied": False,
        "verification": "source_is_exact_destination_no_copy",
        "size_accounting": "not_recounted; collector manifest/checkpoint owns size",
    }


def test_direct_destination_registers_checkpoint_directories_without_enumerating_files(
    tmp_path, monkeypatch
):
    for name in persist.REQUIRED_FILES:
        (tmp_path / name).write_text("{}\n", encoding="utf-8")
    daily = tmp_path / "daily_activity"
    metadata = tmp_path / "event_metadata_by_slug"
    daily.mkdir()
    metadata.mkdir()
    (daily / "day.jsonl.gz").write_bytes(b"daily")
    (metadata / "event.json").write_text("{}\n", encoding="utf-8")

    monkeypatch.setattr(
        persist,
        "checkpoint_files",
        lambda _source: (_ for _ in ()).throw(
            AssertionError("direct destination must not enumerate every checkpoint")
        ),
    )
    rows = persist.persist_snapshot(tmp_path, tmp_path)

    assert len(rows) == len(persist.REQUIRED_FILES) + 2
    assert rows[-2]["path"] == str(daily)
    assert rows[-1]["path"] == str(metadata)
