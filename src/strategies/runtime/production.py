"""Git-authored desired production topology for the weather stack."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import yaml
import re


ROOT = Path(__file__).resolve().parents[3]
PRODUCTION_PATH = ROOT / "src/strategies/runtime/production.yaml"


@dataclass(frozen=True)
class WeatherProductionReleaseSpec:
    release_id: str
    checkout_root: Path
    expected_repo_sha: str


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
    uses_market_proxy: bool = False
    release_id: str | None = None

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
class WeatherMarketProxyFailoverSpec:
    enabled: bool
    controller_url: str
    group: str
    controller_secret_env: str
    controller_secret_keychain_service: str
    controller_secret_keychain_account: str
    state_path: Path
    audit_path: Path
    lock_path: Path
    probe_timeout_sec: float = 5.0
    failure_confirmations: int = 2
    settle_sec: float = 0.4


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
    market_proxy_state_path: Path
    market_proxy_default_url: str
    market_proxy_failover: WeatherMarketProxyFailoverSpec | None = None
    market_books_root: Path | None = None
    strategy_snapshot_root: Path | None = None
    market_ladder_snapshot_root: Path | None = None
    forecast_output_root: Path | None = None
    production_storage_root: Path = Path("/Volumes/jrs")
    production_storage_volume_uuid: str | None = None
    archive_storage_root: Path = Path("/Volumes/jrs-archive")
    archive_storage_volume_uuid: str | None = None
    historical_data_feed_runtime_root: Path | None = None
    historical_full_ladder_data_root: Path | None = None
    historical_targeted_data_root: Path | None = None
    historical_paper_snapshot_root: Path | None = None
    canonical_refresh_checkout_root: Path | None = None
    research_artifact_root: Path = Path(
        "/Volumes/jrs-archive/pm_agents/research/artifact_store"
    )
    managed_runtimes: tuple[WeatherManagedRuntimeSpec, ...] = field(default_factory=tuple)
    releases: tuple[WeatherProductionReleaseSpec, ...] = field(default_factory=tuple)
    allowed_unmanaged_sessions: tuple[str, ...] = field(default_factory=tuple)

    def release(self, release_id: str) -> WeatherProductionReleaseSpec:
        for release in self.releases:
            if release.release_id == release_id:
                return release
        raise KeyError(release_id)

    def resolved_market_books_root(self) -> Path:
        return self.market_books_root or self.data_feed_runtime_root / "market_books"

    def resolved_strategy_snapshot_root(self) -> Path:
        return self.strategy_snapshot_root or self.data_feed_runtime_root / "strategy_snapshots"

    def resolved_market_ladder_snapshot_root(self) -> Path:
        return self.market_ladder_snapshot_root or self.data_feed_runtime_root / "market_ladder_snapshots"

    def resolved_forecast_output_root(self) -> Path:
        return self.forecast_output_root or self.data_feed_runtime_root / "forecast"

    def strategy_paper_snapshot_dir(self) -> Path:
        return self.resolved_strategy_snapshot_root() / "paper_snapshots"

    def forecast_hourly_curve_dir(self) -> Path:
        return self.resolved_forecast_output_root() / "forecast_hourly_curves"

    def resolved_historical_paper_snapshot_root(self) -> Path:
        if self.historical_paper_snapshot_root is None:
            raise ValueError("production spec requires historical_paper_snapshot_root")
        return self.historical_paper_snapshot_root

    def resolved_historical_data_feed_runtime_root(self) -> Path:
        if self.historical_data_feed_runtime_root is None:
            raise ValueError("production spec requires historical_data_feed_runtime_root")
        return self.historical_data_feed_runtime_root

    def data_feed_output_root(self) -> Path:
        return self.data_feed_runtime_root / "output"

    def source_events_root(self) -> Path:
        return self.data_feed_output_root() / "source_events"

    def forecast_enrichment_root(self) -> Path:
        return self.data_feed_output_root() / "forecast_enrichment"

    def live_cross_observations_root(self) -> Path:
        return self.data_feed_output_root() / "live_cross_observations"

    def historical_full_ladder_root(self) -> Path:
        if self.historical_full_ladder_data_root is None:
            raise ValueError("production spec requires historical_full_ladder_data_root")
        return self.historical_full_ladder_data_root

    def historical_targeted_root(self) -> Path:
        if self.historical_targeted_data_root is None:
            raise ValueError("production spec requires historical_targeted_data_root")
        return self.historical_targeted_data_root

    def observation_cache_path(self) -> Path:
        return self.data_feed_runtime_root / "output/observations/latest.json"

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
    configured = os.getenv("WEATHER_PRODUCTION_CONFIG", "").strip()
    source = path or (Path(configured) if configured else PRODUCTION_PATH)
    if not source.is_absolute():
        raise ValueError("production spec path must be absolute")
    raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"production spec must be a mapping: {source}")
    compatibility = raw.get("compatibility_db_paths")
    if not isinstance(compatibility, list) or not compatibility:
        raise ValueError("production spec requires compatibility_db_paths")
    managed_raw = raw.get("managed_runtimes") or []
    if not isinstance(managed_raw, list):
        raise ValueError("production spec managed_runtimes must be a list")
    releases_raw = raw.get("production_releases") or []
    if not isinstance(releases_raw, list):
        raise ValueError("production spec production_releases must be a list")
    releases: list[WeatherProductionReleaseSpec] = []
    release_by_id: dict[str, WeatherProductionReleaseSpec] = {}
    for item in releases_raw:
        if not isinstance(item, dict):
            raise ValueError("each production release must be a mapping")
        release = WeatherProductionReleaseSpec(
            release_id=str(item["release_id"]),
            checkout_root=Path(item["checkout_root"]),
            expected_repo_sha=str(item["expected_repo_sha"]).lower(),
        )
        if release.release_id in release_by_id:
            raise ValueError(f"duplicate production release_id: {release.release_id}")
        if not release.checkout_root.is_absolute():
            raise ValueError(f"release checkout_root must be absolute: {release.release_id}")
        if not re.fullmatch(r"[0-9a-f]{40}", release.expected_repo_sha):
            raise ValueError(f"release expected_repo_sha must be a full SHA: {release.release_id}")
        releases.append(release)
        release_by_id[release.release_id] = release
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
        release_id = str(item.get("release_id") or "") or None
        if release_id and item.get("checkout_root"):
            raise ValueError(
                f"managed runtime cannot declare both release_id and checkout_root: {instance_id}"
            )
        if release_id and release_id not in release_by_id:
            raise ValueError(f"unknown production release_id for {instance_id}: {release_id}")
        checkout_root = (
            release_by_id[release_id].checkout_root
            if release_id
            else Path(item["checkout_root"])
            if item.get("checkout_root")
            else None
        )
        managed.append(
            WeatherManagedRuntimeSpec(
                instance_id=instance_id,
                tmux_session=tmux_session,
                role=str(item.get("role") or "strategy"),
                execution_mode=str(item.get("execution_mode") or "unknown"),
                desired_state=str(item.get("desired_state") or "running"),
                checkout_root=checkout_root,
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
                uses_market_proxy=bool(item.get("uses_market_proxy", False)),
                release_id=release_id,
            )
        )
    allowed_unmanaged = raw.get("allowed_unmanaged_sessions") or []
    if not isinstance(allowed_unmanaged, list):
        raise ValueError("production spec allowed_unmanaged_sessions must be a list")
    canonical_refresh_release_id = str(raw.get("canonical_refresh_release_id") or "") or None
    if canonical_refresh_release_id and canonical_refresh_release_id not in release_by_id:
        raise ValueError(
            f"unknown canonical_refresh_release_id: {canonical_refresh_release_id}"
        )
    failover_raw = raw.get("market_proxy_node_failover")
    if failover_raw is not None and not isinstance(failover_raw, dict):
        raise ValueError("market_proxy_node_failover must be a mapping")
    failover = None
    if failover_raw:
        failover = WeatherMarketProxyFailoverSpec(
            enabled=bool(failover_raw.get("enabled", False)),
            controller_url=str(failover_raw["controller_url"]).rstrip("/"),
            group=str(failover_raw["group"]),
            controller_secret_env=str(
                failover_raw.get("controller_secret_env")
                or "WEATHER_MARKET_PROXY_CONTROLLER_SECRET"
            ),
            controller_secret_keychain_service=str(
                failover_raw.get("controller_secret_keychain_service")
                or "pm_agents.weather_market_proxy_controller"
            ),
            controller_secret_keychain_account=str(
                failover_raw.get("controller_secret_keychain_account")
                or "weather-controller"
            ),
            state_path=Path(failover_raw["state_path"]),
            audit_path=Path(failover_raw["audit_path"]),
            lock_path=Path(failover_raw["lock_path"]),
            probe_timeout_sec=float(failover_raw.get("probe_timeout_sec", 5.0)),
            failure_confirmations=int(failover_raw.get("failure_confirmations", 2)),
            settle_sec=float(failover_raw.get("settle_sec", 0.4)),
        )
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
        market_proxy_state_path=Path(raw["market_proxy_state_path"]),
        market_proxy_default_url=str(raw["market_proxy_default_url"]),
        market_proxy_failover=failover,
        market_books_root=(
            Path(raw["market_books_root"]) if raw.get("market_books_root") else None
        ),
        strategy_snapshot_root=(
            Path(raw["strategy_snapshot_root"])
            if raw.get("strategy_snapshot_root")
            else None
        ),
        market_ladder_snapshot_root=(
            Path(raw["market_ladder_snapshot_root"])
            if raw.get("market_ladder_snapshot_root")
            else None
        ),
        forecast_output_root=(
            Path(raw["forecast_output_root"])
            if raw.get("forecast_output_root")
            else None
        ),
        production_storage_root=Path(
            raw.get("production_storage_root") or "/Volumes/jrs"
        ),
        production_storage_volume_uuid=(
            str(raw["production_storage_volume_uuid"]).upper()
            if raw.get("production_storage_volume_uuid")
            else None
        ),
        archive_storage_root=Path(
            raw.get("archive_storage_root") or "/Volumes/jrs-archive"
        ),
        archive_storage_volume_uuid=(
            str(raw["archive_storage_volume_uuid"]).upper()
            if raw.get("archive_storage_volume_uuid")
            else None
        ),
        historical_data_feed_runtime_root=(
            Path(raw["historical_data_feed_runtime_root"])
            if raw.get("historical_data_feed_runtime_root")
            else None
        ),
        historical_full_ladder_data_root=(
            Path(raw["historical_full_ladder_data_root"])
            if raw.get("historical_full_ladder_data_root")
            else None
        ),
        historical_targeted_data_root=(
            Path(raw["historical_targeted_data_root"])
            if raw.get("historical_targeted_data_root")
            else None
        ),
        historical_paper_snapshot_root=(
            Path(raw["historical_paper_snapshot_root"])
            if raw.get("historical_paper_snapshot_root")
            else None
        ),
        canonical_refresh_checkout_root=(
            release_by_id[canonical_refresh_release_id].checkout_root
            if canonical_refresh_release_id
            else Path(raw["canonical_refresh_checkout_root"])
            if raw.get("canonical_refresh_checkout_root")
            else None
        ),
        research_artifact_root=Path(raw["research_artifact_root"]),
        managed_runtimes=tuple(managed),
        releases=tuple(releases),
        allowed_unmanaged_sessions=tuple(str(item) for item in allowed_unmanaged),
    )
    if not spec.canonical_db_path.is_absolute():
        raise ValueError("canonical_db_path must be absolute")
    if not spec.operational_repo_root.is_absolute():
        raise ValueError("operational_repo_root must be absolute")
    if not spec.production_storage_root.is_absolute():
        raise ValueError("production_storage_root must be absolute")
    if not spec.archive_storage_root.is_absolute():
        raise ValueError("archive_storage_root must be absolute")
    if spec.production_storage_root == spec.archive_storage_root:
        raise ValueError("production and archive storage roots must differ")
    historical_snapshot_root = spec.resolved_historical_paper_snapshot_root()
    historical_data_feed_root = spec.resolved_historical_data_feed_runtime_root()
    resolved_archive_root = spec.archive_storage_root.resolve(strict=False)
    resolved_historical_snapshot_root = historical_snapshot_root.resolve(strict=False)
    if (
        not historical_snapshot_root.is_absolute()
        or not resolved_historical_snapshot_root.is_relative_to(resolved_archive_root)
    ):
        raise ValueError(
            "historical_paper_snapshot_root must live under archive_storage_root"
        )
    if (
        not historical_data_feed_root.is_absolute()
        or not historical_data_feed_root.resolve(strict=False).is_relative_to(
            resolved_archive_root
        )
    ):
        raise ValueError(
            "historical_data_feed_runtime_root must live under archive_storage_root"
        )
    for name, path in {
        "historical_full_ladder_data_root": spec.historical_full_ladder_root(),
        "historical_targeted_data_root": spec.historical_targeted_root(),
    }.items():
        if not path.is_absolute() or not path.resolve(strict=False).is_relative_to(
            historical_data_feed_root.resolve(strict=False)
        ):
            raise ValueError(f"{name} must live under historical_data_feed_runtime_root")
    if not spec.canonical_db_path.is_relative_to(spec.production_storage_root):
        raise ValueError("canonical_db_path must live under production_storage_root")
    if not spec.data_feed_runtime_root.is_relative_to(spec.production_storage_root):
        raise ValueError("data_feed_runtime_root must live under production_storage_root")
    data_roots = {
        "market_books_root": spec.resolved_market_books_root(),
        "strategy_snapshot_root": spec.resolved_strategy_snapshot_root(),
        "market_ladder_snapshot_root": spec.resolved_market_ladder_snapshot_root(),
        "forecast_output_root": spec.resolved_forecast_output_root(),
    }
    for name, data_root in data_roots.items():
        if not data_root.is_absolute() or not data_root.is_relative_to(spec.data_feed_runtime_root):
            raise ValueError(f"{name} must live under data_feed_runtime_root")
    if len({str(path) for path in data_roots.values()}) != len(data_roots):
        raise ValueError("weather production data roots must be distinct")
    if (
        spec.canonical_refresh_checkout_root is not None
        and not spec.canonical_refresh_checkout_root.is_absolute()
    ):
        raise ValueError("canonical_refresh_checkout_root must be absolute")
    if spec.canonical_db_path.parent != spec.pm_runtime_root:
        raise ValueError("canonical_db_path must live directly under pm_runtime_root")
    if spec.market_proxy_failover is not None:
        failover = spec.market_proxy_failover
        controller_url = urlparse(failover.controller_url)
        if (
            controller_url.scheme != "http"
            or controller_url.hostname != "127.0.0.1"
            or controller_url.port is None
            or controller_url.username is not None
            or controller_url.password is not None
            or controller_url.path not in {"", "/"}
            or controller_url.query
            or controller_url.fragment
        ):
            raise ValueError("market proxy node controller must be loopback HTTP")
        if not failover.group.strip():
            raise ValueError("market proxy node failover group is required")
        if (
            not failover.controller_secret_env.strip()
            or not failover.controller_secret_keychain_service.strip()
            or not failover.controller_secret_keychain_account.strip()
        ):
            raise ValueError("market proxy node controller secret identity is required")
        if failover.failure_confirmations < 1:
            raise ValueError("market proxy failure_confirmations must be >= 1")
        if failover.probe_timeout_sec <= 0 or failover.settle_sec < 0:
            raise ValueError("market proxy node failover timings must be non-negative")
        for name, path in {
            "state_path": failover.state_path,
            "audit_path": failover.audit_path,
            "lock_path": failover.lock_path,
        }.items():
            if not path.is_absolute() or not path.is_relative_to(spec.data_feed_runtime_root):
                raise ValueError(
                    f"market proxy node failover {name} must live under data_feed_runtime_root"
                )
    if not spec.research_artifact_root.is_absolute():
        raise ValueError("research_artifact_root must be absolute")
    research_owner = spec.archive_storage_root / "pm_agents/research"
    if not spec.research_artifact_root.is_relative_to(research_owner):
        raise ValueError(
            "research_artifact_root must live under the archive research root"
        )
    managed_ids = {item.instance_id for item in spec.managed_runtimes}
    if spec.releases:
        missing_release_ids = [
            item.instance_id
            for item in spec.managed_runtimes
            if item.instance_id != "weather_jrs_context_keeper" and not item.release_id
        ]
        if missing_release_ids:
            raise ValueError(
                "managed business runtimes must declare release_id: "
                f"{missing_release_ids}"
            )
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
        if item.health_path:
            if not item.health_path.is_absolute():
                raise ValueError(
                    f"runtime health_path must be absolute: {item.instance_id}"
                )
            if not (
                item.health_path.is_relative_to(spec.data_feed_runtime_root)
                or item.health_path.is_relative_to(spec.pm_runtime_root)
            ):
                raise ValueError(
                    "runtime health_path must live under data_feed_runtime_root "
                    f"or pm_runtime_root: {item.instance_id}"
                )
        if item.live_order_path:
            if not item.live_order_path.is_absolute() or not (
                item.live_order_path.is_relative_to(spec.pm_runtime_root)
                or item.live_order_path.is_relative_to(spec.data_feed_runtime_root)
            ):
                raise ValueError(
                    "live_order_path must live under pm_runtime_root or "
                    f"data_feed_runtime_root: {item.instance_id}"
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
