"""Instance spec: the git-authored source of truth for strategy instances.

StrategySpec used to live inline in
scripts/ops/refresh_weather_strategy_runtime_registry.py as a hardcoded list.
It now lives here and is loaded from instances.yaml so metadata is committed
data, not code buried in the scan/reflection layer.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
INSTANCES_PATH = ROOT / "src/strategies/runtime/instances.yaml"


@dataclass
class StrategySpec:
    strategy_instance: str
    display_name: str
    family: str
    lifecycle_status: str
    execution_mode: str
    source_layer: str
    config_id: str | None = None
    runtime_dir: str | None = None
    summary_file: str | None = None
    primary_journal: str | None = None
    live_order_file: str | None = None
    paper_order_file: str | None = None
    telemetry_file: str | None = None
    start_script: str | None = None
    tmux_session: str | None = None
    notes: str | None = None
    default_health: str = "unknown"
    expected_live: bool | None = None
    artifact_files: list[tuple[str, str]] = field(default_factory=list)


def load_instance_specs(path: Path | None = None) -> list[StrategySpec]:
    path = path or INSTANCES_PATH
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"instances spec must be a list, got {type(raw)}: {path}")
    specs: list[StrategySpec] = []
    for item in raw:
        data = dict(item)
        data["artifact_files"] = [tuple(pair) for pair in (data.get("artifact_files") or [])]
        specs.append(StrategySpec(**data))
    return specs


def spec_commit(path: Path | None = None) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, check=True,
        )
        return out.stdout.strip() or None
    except Exception:
        return None


def params_hash(spec: StrategySpec) -> str:
    payload = json.dumps(asdict(spec), sort_keys=True, ensure_ascii=False, default=list)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
