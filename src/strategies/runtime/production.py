"""Git-authored desired production topology for the weather stack."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[3]
PRODUCTION_PATH = ROOT / "src/strategies/runtime/production.yaml"


@dataclass(frozen=True)
class WeatherProductionSpec:
    version: str
    host_role: str
    operational_repo_root: Path
    canonical_db_path: Path
    compatibility_db_paths: tuple[Path, ...]
    data_feed_runtime_root: Path
    pm_runtime_root: Path
    canonical_tmux_socket: str
    canonical_tmux_binary: Path

    def resolved_compatibility_db_paths(
        self, repo_root: Path | None = None
    ) -> tuple[Path, ...]:
        repo_root = repo_root or self.operational_repo_root
        return tuple(
            path if path.is_absolute() else repo_root / path
            for path in self.compatibility_db_paths
        )


def load_production_spec(path: Path | None = None) -> WeatherProductionSpec:
    source = path or PRODUCTION_PATH
    raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"production spec must be a mapping: {source}")
    compatibility = raw.get("compatibility_db_paths")
    if not isinstance(compatibility, list) or not compatibility:
        raise ValueError("production spec requires compatibility_db_paths")
    spec = WeatherProductionSpec(
        version=str(raw["version"]),
        host_role=str(raw["host_role"]),
        operational_repo_root=Path(raw["operational_repo_root"]),
        canonical_db_path=Path(raw["canonical_db_path"]),
        compatibility_db_paths=tuple(Path(item) for item in compatibility),
        data_feed_runtime_root=Path(raw["data_feed_runtime_root"]),
        pm_runtime_root=Path(raw["pm_runtime_root"]),
        canonical_tmux_socket=str(raw["canonical_tmux_socket"]),
        canonical_tmux_binary=Path(raw["canonical_tmux_binary"]),
    )
    if not spec.canonical_db_path.is_absolute():
        raise ValueError("canonical_db_path must be absolute")
    if not spec.operational_repo_root.is_absolute():
        raise ValueError("operational_repo_root must be absolute")
    if spec.canonical_db_path.parent != spec.pm_runtime_root:
        raise ValueError("canonical_db_path must live directly under pm_runtime_root")
    return spec
