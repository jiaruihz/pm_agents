from __future__ import annotations

import json
import os
from pathlib import Path

from scripts.ops.audit_weather_city_runtime_phase7 import build_audit


ROOT = Path(__file__).resolve().parents[2]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def runtime_fixture(tmp_path: Path) -> tuple[Path, Path]:
    runtime = tmp_path / "active"
    legacy = tmp_path / "legacy"
    runtime.mkdir()
    legacy.mkdir()
    write_jsonl(
        runtime / "decision_bundles.jsonl",
        [{
            "signal_candidate": {
                "candidate_id": "candidate-1",
                "city": "Helsinki",
                "candidate_status": "scored",
            }
        }],
    )
    write_jsonl(
        runtime / "checkpoint_blockers.jsonl",
        [{"checkpoint_id": "ams-1", "city": "Amsterdam"}],
    )
    write_jsonl(
        runtime / "trade_intents.jsonl",
        [{"intent_id": "intent-1", "mode": "zero_notional", "requested_size": 0.0}],
    )
    write_jsonl(
        runtime / "execution_handoffs.jsonl",
        [{
            "status": "record_only",
            "execution_intent": None,
            "reason": "zero_notional_has_no_execution_side_effect",
        }],
    )
    (runtime / "latest_summary.json").write_text(
        json.dumps({
            "execution_mode": "zero_notional_shadow",
            "orders_submitted": 0,
            "generated_at_utc": "2026-08-03T00:00:00Z",
        }),
        encoding="utf-8",
    )
    for name in ("evaluations.jsonl", "paper_intents.jsonl", "checkpoints.jsonl", "errors.jsonl"):
        write_jsonl(legacy / name, [{"legacy": True}])
        os.utime(legacy / name, (1, 1))
    return runtime, legacy


def config_with_legacy(tmp_path: Path, legacy: Path) -> Path:
    config = json.loads(
        (ROOT / "configs/weather/city_probability_runtime_v3.json").read_text()
    )
    config["legacy_runtime"]["runtime_dir"] = str(legacy)
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


def manifest_fixture(tmp_path: Path) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps({
            "status": "healthy",
            "generated_at_utc": "2026-08-03T00:00:00Z",
            "db_route": {"status": "healthy"},
            "processes": [{
                "pid": 123,
                "command": "python scripts/ops/weather_city_probability_runtime_v3.py loop",
                "checkout": {"head": "test"},
            }],
        }),
        encoding="utf-8",
    )
    return path


def test_phase7_audit_accepts_five_city_shadow_without_execution(tmp_path: Path) -> None:
    runtime, legacy = runtime_fixture(tmp_path)
    report = build_audit(
        repo_root=ROOT,
        config_path=config_with_legacy(tmp_path, legacy),
        production_path=ROOT / "src/strategies/runtime/production.yaml",
        runtime_dir=runtime,
        manifest_path=manifest_fixture(tmp_path),
    )

    assert report["pass"] is True
    assert report["sections"]["config"]["declared_cities"] == [
        "Amsterdam", "Busan", "Helsinki", "Seoul", "Tokyo"
    ]
    assert report["sections"]["raw_runtime"]["intent_rows"] == 1
    assert report["execution_impact"]["orders_created"] == 0


def test_phase7_audit_rejects_nonzero_intent(tmp_path: Path) -> None:
    runtime, legacy = runtime_fixture(tmp_path)
    write_jsonl(
        runtime / "trade_intents.jsonl",
        [{"intent_id": "bad", "mode": "shadow", "requested_size": 1.0}],
    )
    report = build_audit(
        repo_root=ROOT,
        config_path=config_with_legacy(tmp_path, legacy),
        production_path=ROOT / "src/strategies/runtime/production.yaml",
        runtime_dir=runtime,
        manifest_path=manifest_fixture(tmp_path),
    )

    assert report["pass"] is False
    assert report["sections"]["raw_runtime"]["invalid_intent_ids"] == ["bad"]
