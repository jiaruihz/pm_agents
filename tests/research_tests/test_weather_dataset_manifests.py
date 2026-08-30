from pathlib import Path

import pytest

from weather_model_evaluation import DataSourceSnapshot, DatasetManifest


def _rows():
    return [
        {"row_id": "one", "target_date": "2026-01-01", "label": 1, "label_available_at_utc": "2026-01-02T00:00:00Z", "available_at_utc": "2026-01-01T01:00:00Z", "measurement_namespace": "tmax"},
        {"row_id": "two", "target_date": "2026-01-02", "label": 0, "label_available_at_utc": "2026-01-03T00:00:00Z", "available_at_utc": "2026-01-02T01:00:00Z", "measurement_namespace": "tmax"},
    ]


def test_manifest_is_content_addressed_sealed_and_refuses_future_labels(tmp_path: Path):
    db = tmp_path / "weather.db"
    db.write_bytes(b"sqlite bytes")
    rows = _rows()
    snapshot = DataSourceSnapshot.from_rows("source", rows, available_at_field="available_at_utc", cutoff_utc="2026-01-03T00:00:00Z")
    manifest = DatasetManifest.build(
        rows=rows, db_path=db, db_schema_version="v1", catalog_build_id="catalog-1", code_sha="a" * 40,
        source_snapshots=[snapshot], measurement_namespace="tmax", label_value_field="label", label_available_at_field="label_available_at_utc",
        cutoff_utc="2026-01-03T00:00:00Z", splits={"train": [rows[0]], "frozen_forward": [rows[1]]},
    )
    path = manifest.write_atomic(tmp_path / "manifest.json")
    assert path.exists() and manifest.sealed and manifest.immutable
    assert len(manifest.split_manifest_hash) == 64
    assert manifest.db_identity["realpath"] == str(db.resolve())
    with pytest.raises(FileExistsError):
        manifest.write_atomic(path)
    rows[1]["label_available_at_utc"] = "2026-01-04T00:00:00Z"
    with pytest.raises(ValueError, match="future"):
        DatasetManifest.build(rows=rows, db_path=db, db_schema_version="v1", catalog_build_id="catalog-1", code_sha="a" * 40, source_snapshots=[snapshot], measurement_namespace="tmax", label_value_field="label", label_available_at_field="label_available_at_utc", cutoff_utc="2026-01-03T00:00:00Z", splits={"train": [rows[0]], "test": [rows[1]]})


def test_target_dates_cannot_cross_splits(tmp_path: Path):
    db = tmp_path / "weather.db"; db.write_bytes(b"x")
    rows = _rows(); rows[1]["target_date"] = rows[0]["target_date"]
    source = DataSourceSnapshot.from_rows("source", rows, available_at_field="available_at_utc", cutoff_utc="2026-01-03T00:00:00Z")
    with pytest.raises(ValueError, match="both"):
        DatasetManifest.build(rows=rows, db_path=db, db_schema_version="v1", catalog_build_id="c", code_sha="a", source_snapshots=[source], measurement_namespace="tmax", label_value_field="label", label_available_at_field="label_available_at_utc", cutoff_utc="2026-01-03T00:00:00Z", splits={"train": [rows[0]], "test": [rows[1]]})


def test_every_row_must_be_assigned_once_and_blocks_are_chronological(tmp_path: Path):
    db = tmp_path / "weather.db"
    db.write_bytes(b"x")
    rows = _rows()
    source = DataSourceSnapshot.from_rows(
        "source",
        rows,
        available_at_field="available_at_utc",
        cutoff_utc="2026-01-03T00:00:00Z",
    )
    kwargs = dict(
        rows=rows,
        db_path=db,
        db_schema_version="v1",
        catalog_build_id="c",
        code_sha="a",
        source_snapshots=[source],
        measurement_namespace="tmax",
        label_value_field="label",
        label_available_at_field="label_available_at_utc",
        cutoff_utc="2026-01-03T00:00:00Z",
    )
    with pytest.raises(ValueError, match="missing split membership"):
        DatasetManifest.build(**kwargs, splits={"train": [rows[0]]})
    with pytest.raises(ValueError, match="belongs to both|multiple splits"):
        DatasetManifest.build(
            **kwargs,
            splits={"train": rows, "test": [rows[1]]},
        )
    with pytest.raises(ValueError, match="not chronological"):
        DatasetManifest.build(
            **kwargs,
            splits={"train": [rows[1]], "test": [rows[0]]},
        )
