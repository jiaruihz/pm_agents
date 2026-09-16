from pathlib import Path

import pytest

from scripts.analysis.versioned_artifact_output import (
    prepare_new_run_output,
    resolve_content_addressed_artifact,
    resolve_run_output,
)


def test_run_id_resolves_under_family_root(tmp_path: Path) -> None:
    result = resolve_run_output(
        "family_v1",
        run_id="frozen_20260807",
        explicit_output=None,
        artifact_root=tmp_path,
    )
    assert result == tmp_path / "active/family_v1/frozen_20260807"


@pytest.mark.parametrize("run_id", [None, "", "../escape", "bad id"])
def test_invalid_run_id_is_rejected(tmp_path: Path, run_id: str | None) -> None:
    with pytest.raises(ValueError, match="stable --run-id is required"):
        resolve_run_output(
            "family_v1",
            run_id=run_id,
            explicit_output=None,
            artifact_root=tmp_path,
        )


def test_prepare_refuses_non_empty_existing_directory(tmp_path: Path) -> None:
    output = tmp_path / "run"
    output.mkdir()
    (output / "result.csv").write_text("x\n", encoding="utf-8")
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        prepare_new_run_output(output)


def test_content_addressed_artifact_is_verified(tmp_path: Path) -> None:
    sha256 = "2d711642b726b04401627ca9fbac32f5c8530fb1903cc4db02258717921a4881"
    artifact = tmp_path / "objects" / sha256[:2] / sha256
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"x")

    assert resolve_content_addressed_artifact(
        sha256, artifact_root=tmp_path
    ) == artifact


def test_content_addressed_artifact_rejects_corruption(tmp_path: Path) -> None:
    sha256 = "2d711642b726b04401627ca9fbac32f5c8530fb1903cc4db02258717921a4881"
    artifact = tmp_path / "objects" / sha256[:2] / sha256
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"wrong")

    with pytest.raises(ValueError, match="hash mismatch"):
        resolve_content_addressed_artifact(sha256, artifact_root=tmp_path)
