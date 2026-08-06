"""Git-authored desired production topology for the weather stack."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[3]
PRODUCTION_PATH = ROOT / "src/strategies/runtime/production.yaml"


@dataclass(frozen=True)
class WeatherManagedRuntimeSpec:
    instance_id: str
    tmux_session: str
    role: str
    execution_mode: str
    desired_state: str = "running"
    checkout_root: Path | None = None
    start_script: Path | None = None
    restart_script: Path | None = None
    health_path: Path | None = None
    health_url: str | None = None
    health_format: str = "json"
    max_health_age_sec: float | None = None
    accepted_health_statuses: tuple[str, ...] = ()
    expected_live: bool = False
    live_order_path: Path | None = None
    dependencies: tuple[str, ...] = ()
    recovery_policy: str = "manual"

    def resolved_start_script(self) -> Path | None:
        if self.start_script is None:
            return None
        if self.start_script.is_absolute():
            return self.start_script
        if self.checkout_root is None:
            return None
        return self.checkout_root / self.start_script

    def resolved_restart_script(self) -> Path | None:
        if self.restart_script is None:
            return None
        if self.restart_script.is_absolute():
            return self.restart_script
        if self.checkout_root is None:
            return None
        return self.checkout_root / self.restart_script


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
    canonical_refresh_checkout_root: Path | None = None
    research_artifact_root: Path = Path(
        "/Volumes/jrs/pm_agents/research/artifact_store"
    )
    managed_runtimes: tuple[WeatherManagedRuntimeSpec, ...] = field(default_factory=tuple)
    allowed_unmanaged_sessions: tuple[str, ...] = field(default_factory=tuple)

    def resolved_compatibility_db_paths(
        self, repo_root: Path | None = None
    ) -> tuple[Path, ...]:
        repo_root = repo_root or self.operational_repo_root
        return tuple(
            path if path.is_absolute() else repo_root / path
            for path in self.compatibility_db_paths
        )

    def active_live_order_paths(self) -> tuple[Path, ...]:
        """Return the unique journals for desired live runtimes.

        The production manifest is the only authority for this set.  Callers
        must not keep a second hard-coded list of strategy names or paths.
        """

        paths: list[Path] = []
        seen: set[str] = set()
        for runtime in self.managed_runtimes:
            if not runtime.expected_live or runtime.live_order_path is None:
                continue
            path = runtime.live_order_path
            if not path.is_absolute():
                base = runtime.checkout_root or self.operational_repo_root
                path = base / path
            key = str(path)
            if key not in seen:
                paths.append(path)
                seen.add(key)
        return tuple(paths)


def load_production_spec(path: Path | None = None) -> WeatherProductionSpec:
    source = path or PRODUCTION_PATH
    raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"production spec must be a mapping: {source}")
    compatibility = raw.get("compatibility_db_paths")
    if not isinstance(compatibility, list) or not compatibility:
        raise ValueError("production spec requires compatibility_db_paths")
    managed_raw = raw.get("managed_runtimes") or []
    if not isinstance(managed_raw, list):
        raise ValueError("production spec managed_runtimes must be a list")
    managed: list[WeatherManagedRuntimeSpec] = []
    seen_instances: set[str] = set()
    seen_sessions: set[str] = set()
    for item in managed_raw:
        if not isinstance(item, dict):
            raise ValueError("each managed runtime must be a mapping")
        instance_id = str(item["instance_id"])
        tmux_session = str(item["tmux_session"])
        if instance_id in seen_instances:
            raise ValueError(f"duplicate managed runtime instance_id: {instance_id}")
        if tmux_session in seen_sessions:
            raise ValueError(f"duplicate managed runtime tmux_session: {tmux_session}")
        seen_instances.add(instance_id)
        seen_sessions.add(tmux_session)
        managed.append(
            WeatherManagedRuntimeSpec(
                instance_id=instance_id,
                tmux_session=tmux_session,
                role=str(item.get("role") or "strategy"),
                execution_mode=str(item.get("execution_mode") or "unknown"),
                desired_state=str(item.get("desired_state") or "running"),
                checkout_root=(
                    Path(item["checkout_root"])
                    if item.get("checkout_root")
                    else None
                ),
                start_script=(
                    Path(item["start_script"])
                    if item.get("start_script")
                    else None
                ),
                restart_script=(
                    Path(item["restart_script"])
                    if item.get("restart_script")
                    else None
                ),
                health_path=(
                    Path(item["health_path"])
                    if item.get("health_path")
                    else None
                ),
                health_url=(str(item["health_url"]) if item.get("health_url") else None),
                health_format=str(item.get("health_format") or "json"),
                max_health_age_sec=(
                    float(item["max_health_age_sec"])
                    if item.get("max_health_age_sec") is not None
                    else None
                ),
                accepted_health_statuses=tuple(
                    str(value) for value in (item.get("accepted_health_statuses") or [])
                ),
                expected_live=bool(item.get("expected_live", False)),
                live_order_path=(
                    Path(item["live_order_path"])
                    if item.get("live_order_path")
                    else None
                ),
                dependencies=tuple(
                    str(value) for value in (item.get("dependencies") or [])
                ),
                recovery_policy=str(item.get("recovery_policy") or "manual"),
            )
        )
    allowed_unmanaged = raw.get("allowed_unmanaged_sessions") or []
    if not isinstance(allowed_unmanaged, list):
        raise ValueError("production spec allowed_unmanaged_sessions must be a list")
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
        canonical_refresh_checkout_root=(
            Path(raw["canonical_refresh_checkout_root"])
            if raw.get("canonical_refresh_checkout_root")
            else None
        ),
        research_artifact_root=Path(raw["research_artifact_root"]),
        managed_runtimes=tuple(managed),
        allowed_unmanaged_sessions=tuple(str(item) for item in allowed_unmanaged),
    )
    if not spec.canonical_db_path.is_absolute():
        raise ValueError("canonical_db_path must be absolute")
    if not spec.operational_repo_root.is_absolute():
        raise ValueError("operational_repo_root must be absolute")
    if (
        spec.canonical_refresh_checkout_root is not None
        and not spec.canonical_refresh_checkout_root.is_absolute()
    ):
        raise ValueError("canonical_refresh_checkout_root must be absolute")
    if spec.canonical_db_path.parent != spec.pm_runtime_root:
        raise ValueError("canonical_db_path must live directly under pm_runtime_root")
    if not spec.research_artifact_root.is_absolute():
        raise ValueError("research_artifact_root must be absolute")
    research_owner = spec.pm_runtime_root.parent / "research"
    if not spec.research_artifact_root.is_relative_to(research_owner):
        raise ValueError(
            "research_artifact_root must live under the canonical JRS research root"
        )
    managed_ids = {item.instance_id for item in spec.managed_runtimes}
    for item in spec.managed_runtimes:
        if item.desired_state != "running":
            raise ValueError(
                f"unsupported managed runtime desired_state for {item.instance_id}: "
                f"{item.desired_state}"
            )
        if item.recovery_policy not in {"safe", "guarded_live", "manual"}:
            raise ValueError(
                f"unsupported recovery_policy for {item.instance_id}: "
                f"{item.recovery_policy}"
            )
        if item.health_format not in {"json", "mtime", "http_json"}:
            raise ValueError(
                f"unsupported health_format for {item.instance_id}: "
                f"{item.health_format}"
            )
        if item.health_format == "mtime" and (
            item.accepted_health_statuses or item.expected_live
        ):
            raise ValueError(
                f"mtime health cannot validate status/live fields: {item.instance_id}"
            )
        if item.health_format == "http_json" and not item.health_url:
            raise ValueError(
                f"http_json health requires health_url: {item.instance_id}"
            )
        if item.health_format != "http_json" and item.health_url:
            raise ValueError(
                f"health_url requires http_json health: {item.instance_id}"
            )
        if item.health_path and item.health_url:
            raise ValueError(
                f"runtime cannot declare both health_path and health_url: {item.instance_id}"
            )
        if item.expected_live and item.live_order_path is None:
            raise ValueError(
                f"live runtime must declare live_order_path: {item.instance_id}"
            )
        if item.recovery_policy != "manual" and item.resolved_start_script() is None:
            raise ValueError(
                f"automatic recovery requires checkout_root and start_script: "
                f"{item.instance_id}"
            )
        if item.expected_live and item.recovery_policy != "guarded_live":
            raise ValueError(
                f"live runtime must use guarded_live recovery: {item.instance_id}"
            )
        if item.recovery_policy == "guarded_live" and not item.expected_live:
            raise ValueError(
                f"guarded_live runtime must declare expected_live: {item.instance_id}"
            )
        missing_dependencies = set(item.dependencies) - managed_ids
        if missing_dependencies:
            raise ValueError(
                f"unknown dependencies for {item.instance_id}: "
                f"{sorted(missing_dependencies)}"
            )
    return spec
