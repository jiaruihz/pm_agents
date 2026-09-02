from pathlib import Path

import pytest

from scripts.ops.weather_canonical_refresh_bindings import (
    ADJUSTMENT_JOURNAL_NAMES,
    FILL_CACHE_NAME,
    RUNTIME_RELATIVE_ROOT,
    provision_canonical_refresh_bindings,
)


def _runtime(project: Path) -> Path:
    return project / RUNTIME_RELATIVE_ROOT


def test_provision_binds_all_operational_journals_and_repairs_dangling_link(
    tmp_path: Path,
):
    operational = tmp_path / "operational"
    refresh = tmp_path / "release"
    operational_runtime = _runtime(operational)
    refresh_runtime = _runtime(refresh)
    operational_runtime.mkdir(parents=True)
    (operational_runtime / FILL_CACHE_NAME).write_text("{}\n", encoding="utf-8")
    (operational_runtime / ADJUSTMENT_JOURNAL_NAMES[2]).write_text(
        "{}\n", encoding="utf-8"
    )
    refresh_runtime.mkdir(parents=True)
    stale = refresh_runtime / ADJUSTMENT_JOURNAL_NAMES[2]
    stale.symlink_to(tmp_path / "missing-old-journal.jsonl")

    rows = provision_canonical_refresh_bindings(operational, refresh)

    assert [row["name"] for row in rows] == [
        FILL_CACHE_NAME,
        *ADJUSTMENT_JOURNAL_NAMES,
    ]
    for name in (FILL_CACHE_NAME, *ADJUSTMENT_JOURNAL_NAMES):
        target = refresh_runtime / name
        assert target.is_symlink()
        assert target.readlink() == operational_runtime / name
    assert stale.resolve() == (operational_runtime / ADJUSTMENT_JOURNAL_NAMES[2]).resolve()


def test_provision_refuses_to_overwrite_release_runtime_entity(tmp_path: Path):
    operational = tmp_path / "operational"
    refresh = tmp_path / "release"
    operational_runtime = _runtime(operational)
    refresh_runtime = _runtime(refresh)
    operational_runtime.mkdir(parents=True)
    (operational_runtime / FILL_CACHE_NAME).write_text("{}\n", encoding="utf-8")
    refresh_runtime.mkdir(parents=True)
    entity = refresh_runtime / ADJUSTMENT_JOURNAL_NAMES[0]
    entity.write_text("preserve-me", encoding="utf-8")

    with pytest.raises(RuntimeError, match="is not a symlink"):
        provision_canonical_refresh_bindings(operational, refresh)

    assert entity.read_text(encoding="utf-8") == "preserve-me"


def test_provision_requires_operational_fill_cache(tmp_path: Path):
    with pytest.raises(FileNotFoundError, match="canonical fill cache missing"):
        provision_canonical_refresh_bindings(
            tmp_path / "operational", tmp_path / "release"
        )
