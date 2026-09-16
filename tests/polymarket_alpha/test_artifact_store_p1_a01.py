"""P1-A01 public immutable artifact-store boundary tests."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from src.polymarket_alpha.artifacts import (
    ArtifactConflictError,
    ArtifactPathError,
    ArtifactStore,
    normalize_locator,
)
from src.polymarket_alpha.contracts import bytes_sha256


def test_normalizes_and_rejects_traversal(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)

    assert normalize_locator("sealed/result.json") == "sealed/result.json"
    for locator in ("", "/outside", "../outside", "sealed/../result", "sealed\\result"):
        with pytest.raises(ArtifactPathError):
            store.normalize_locator(locator)


def test_immutable_write_is_idempotent_and_conflicts_on_different_bytes(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)

    store.write_immutable("sealed/result.json", b"first")
    store.write_immutable("sealed/result.json", b"first")

    assert store.read("sealed/result.json") == b"first"
    with pytest.raises(ArtifactConflictError, match="immutable locator conflict"):
        store.write_immutable("sealed/result.json", b"different")


def test_symlink_and_replacement_are_never_followed(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    outside = tmp_path.parent / "outside-p1-a01.txt"
    outside.write_bytes(b"outside")
    os.symlink(outside, tmp_path / "linked.json")
    os.symlink(outside.parent, tmp_path / "linked-dir")

    with pytest.raises(ArtifactPathError):
        store.read("linked.json")
    with pytest.raises(ArtifactConflictError):
        store.write_immutable("linked.json", b"inside")
    with pytest.raises(ArtifactPathError):
        store.write_immutable("linked-dir/new.json", b"inside")

    store.write_immutable("replaceable.json", b"original")
    (tmp_path / "replaceable.json").unlink()
    os.symlink(outside, tmp_path / "replaceable.json")
    with pytest.raises(ArtifactConflictError):
        store.write_immutable("replaceable.json", b"original")


def test_verified_regular_file_read_binds_hash(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    data = b"hash-bound"
    store.write_immutable("sealed/hash.bin", data)

    assert store.read_verified("sealed/hash.bin", expected_sha256=bytes_sha256(data)) == data
    with pytest.raises(ArtifactConflictError, match="artifact hash conflict"):
        store.read_verified("sealed/hash.bin", expected_sha256="0" * 64)
