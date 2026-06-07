#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ops.weather_live_cycle import _load_json_from_output, _run, _slug
from src.strategies.weather_edge_v1.tools.execution_pipeline import stable_hash
from weather_dashboard.blend import blend_probability, load_default_config


TRADING_T1_CITIES = (
    "Boston,Chengdu,Guangzhou,Istanbul,LA,London,Lucknow,Madrid,Manila,"
    "Miami,NYC,Phoenix,Seattle,Shanghai,Singapore,Tokyo,Warsaw"
)
LEGACY_CORE_CITIES = "Boston,LA,London,Miami,NYC,Phoenix,Shanghai,Tokyo,Warsaw"


@dataclass(frozen=True)
class BlendPaperProfile:
    profile_id: str
    source_live_instance: str
    strategy_instance: str
    allowed_cities: str
    min_entry_price: float
    max_entry_price: float
    min_edge: float
    yes_min_entry_price: float
    yes_max_entry_price: float
    yes_min_edge: float
    no_min_entry_price: float
    no_max_entry_price: float
    no_min_edge: float
    raw_min_edge: float = 0.0
    raw_yes_min_edge: float = 0.0
    raw_no_min_edge: float = 0.0
    max_order_notional: float = 1.0
    min_order_shares: float = 1.0
    strategy_id: str = "weather_edge_engine_blended_single_v0"
    decision_mode: str = "single_leg_paper"


PROFILES: dict[str, BlendPaperProfile] = {
    "blended_25_75_e05": BlendPaperProfile(
        profile_id="blended_25_75_e05",
        source_live_instance="mid_price_core_v1_25_75",
        strategy_instance="weather_edge_engine_blended_25_75_e05_paper",
        allowed_cities=TRADING_T1_CITIES,
        min_entry_price=0.25,
        max_entry_price=0.75,
        min_edge=0.05,
        yes_min_entry_price=0.25,
        yes_max_entry_price=0.75,
        yes_min_edge=0.05,
        no_min_entry_price=0.25,
        no_max_entry_price=0.75,
        no_min_edge=0.05,
    ),
    "blended_side_band_e05": BlendPaperProfile(
        profile_id="blended_side_band_e05",
        source_live_instance="mid_price_core_v1_side_band",
        strategy_instance="weather_edge_engine_blended_side_band_e05_paper",
        allowed_cities=LEGACY_CORE_CITIES,
        min_entry_price=0.20,
        max_entry_price=0.65,
        min_edge=0.05,
        yes_min_entry_price=0.20,
        yes_max_entry_price=0.45,
        yes_min_edge=0.05,
        no_min_entry_price=0.35,
        no_max_entry_price=0.65,
        no_min_edge=0.05,
    ),
    "blended_filter_25_75_v0": BlendPaperProfile(
        profile_id="blended_filter_25_75_v0",
        source_live_instance="mid_price_core_v1_25_75",
        strategy_instance="weather_edge_engine_blended_filter_25_75_v0_paper",
        allowed_cities=TRADING_T1_CITIES,
        min_entry_price=0.25,
        max_entry_price=0.75,
        min_edge=0.10,
        yes_min_entry_price=0.25,
        yes_max_entry_price=0.75,
        yes_min_edge=0.10,
        no_min_entry_price=0.25,
        no_max_entry_price=0.75,
        no_min_edge=0.10,
        raw_min_edge=0.10,
        raw_yes_min_edge=0.10,
        raw_no_min_edge=0.10,
        strategy_id="weather_edge_engine_blended_filter_v0",
        decision_mode="single_leg_blended_filter_paper",
    ),
}


def _utc_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _market_yes_from_signal(row: dict[str, Any]) -> float | None:
    side = str(row.get("signal_side") or "").upper()
    entry = _to_float(row.get("market_price"), 0.0)
    if entry <= 0:
        return None
    if side == "BUY_YES":
        return entry
    if side == "BUY_NO":
        return 1.0 - entry
    return None


def _side_edge(side: str, p_yes: float, entry_price: float) -> float:
    if side.upper() == "BUY_YES":
        return p_yes - entry_price
    return (1.0 - p_yes) - entry_price


def _build_candidate_signals(
    *,
    profile: BlendPaperProfile,
    run_id: str,
    out_path: Path,
    snapshot_lookback_minutes: float,
    min_hours_to_settle: float,
    max_hours_to_settle: float,
) -> dict[str, Any]:
    cmd = [
        sys.executable,
        "scripts/ops/weather_snapshot_signal_builder.py",
        "--out",
        str(out_path),
        "--strategy-instance",
        f"{profile.strategy_instance}_raw_candidate",
        "--city-pool",
        "t1_trading",
        "--allowed-cities",
        profile.allowed_cities,
        "--min-edge",
        str(profile.raw_min_edge),
        "--min-entry-price",
        str(profile.min_entry_price),
        "--max-entry-price",
        str(profile.max_entry_price),
        "--yes-min-entry-price",
        str(profile.yes_min_entry_price),
        "--yes-max-entry-price",
        str(profile.yes_max_entry_price),
        "--yes-min-edge",
        str(profile.raw_yes_min_edge),
        "--no-min-entry-price",
        str(profile.no_min_entry_price),
        "--no-max-entry-price",
        str(profile.no_max_entry_price),
        "--no-min-edge",
        str(profile.raw_no_min_edge),
        "--snapshot-lookback-minutes",
        str(snapshot_lookback_minutes),
        "--min-hours-to-settle",
        str(min_hours_to_settle),
        "--max-hours-to-settle",
        str(max_hours_to_settle),
    ]
    result = _run(cmd, timeout=180)
    parsed = _load_json_from_output(result["output"])
    return {"cmd": cmd, "returncode": result["returncode"], "output": result["output"], "summary": parsed}


def _blend_signals(*, profile: BlendPaperProfile, raw_path: Path, blend_path: Path) -> dict[str, Any]:
    blend_cfg = load_default_config()
    rows = _read_jsonl(raw_path)
    blended_rows: list[dict[str, Any]] = []
    skipped: dict[str, int] = {}
    for row in rows:
        side = str(row.get("signal_side") or "").upper()
        raw_p_yes = _to_float(row.get("model_probability_yes"), -1.0)
        entry = _to_float(row.get("market_price"), 0.0)
        market_yes = _market_yes_from_signal(row)
        if raw_p_yes < 0 or market_yes is None or entry <= 0:
            skipped["missing_probability_or_price"] = skipped.get("missing_probability_or_price", 0) + 1
            continue
        result = blend_probability(
            city=str(row.get("city") or ""),
            model_p_yes_raw=raw_p_yes,
            market_implied_p_yes=market_yes,
            config=blend_cfg,
        )
        p_used = result.p_yes_used
        edge_raw_yes = raw_p_yes - market_yes
        edge_raw_no = (1.0 - raw_p_yes) - entry if side == "BUY_NO" else (1.0 - raw_p_yes) - (1.0 - entry)
        edge_used_yes = p_used - market_yes
        edge_used_no = (1.0 - p_used) - entry if side == "BUY_NO" else (1.0 - p_used) - (1.0 - entry)
        used_edge = _side_edge(side, p_used, entry)
        raw_edge = _side_edge(side, raw_p_yes, entry)
        base = {
            **row,
            "strategy_instance": profile.strategy_instance,
            "source_strategy_instance": profile.source_live_instance,
            "strategy_id": profile.strategy_id,
            "strategy_family": "weather_edge_engine",
            "probability_source": "blended",
            "decision_mode": profile.decision_mode,
            "execution_mode": "paper",
            "model_p_yes_raw": round(raw_p_yes, 6),
            "market_implied_p_yes": round(market_yes, 6),
            "model_p_yes_used": round(p_used, 6),
            "model_probability_yes": round(p_used, 6),
            "blend_alpha": round(result.blend_alpha, 6),
            "blend_beta": round(result.blend_beta, 6),
            "blend_mode": result.blend_mode,
            "blend_reason": result.reason,
            "edge_raw_yes": round(edge_raw_yes, 6),
            "edge_raw_no": round(edge_raw_no, 6),
            "edge_used_yes": round(edge_used_yes, 6),
            "edge_used_no": round(edge_used_no, 6),
            "edge_raw_side": round(raw_edge, 6),
            "edge": round(used_edge, 6),
            "min_edge": round(profile.min_edge, 6),
            "shadow_decision": "candidate",
            "shadow_reason": "blended_probability_recomputed_from_raw_snapshot_signal",
        }
        identity = {
            "source_signal_id": row.get("signal_id"),
            "strategy_instance": profile.strategy_instance,
            "model_p_yes_used": base["model_p_yes_used"],
            "edge": base["edge"],
        }
        blended_rows.append({**base, "signal_id": stable_hash(identity)})
    _write_jsonl(blend_path, blended_rows)
    return {
        "raw_signals": len(rows),
        "blended_signals": len(blended_rows),
        "skipped": dict(sorted(skipped.items())),
        "out": str(blend_path),
    }


def _run_planner(
    *,
    profile: BlendPaperProfile,
    signal_path: Path,
    plan_path: Path,
    dry_run: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    cmd = [
        sys.executable,
        "scripts/ops/weather_trade_planner.py",
        "--signals",
        str(signal_path),
        "--out",
        str(plan_path),
        "--strategy-instance",
        profile.strategy_instance,
        "--max-order-notional",
        str(profile.max_order_notional),
        "--sizing-mode",
        "notional",
        "--max-order-shares",
        "25.0",
        "--min-order-shares",
        str(profile.min_order_shares),
        "--min-edge",
        str(profile.min_edge),
        "--min-entry-price",
        str(profile.min_entry_price),
        "--max-entry-price",
        str(profile.max_entry_price),
        "--yes-min-entry-price",
        str(profile.yes_min_entry_price),
        "--yes-max-entry-price",
        str(profile.yes_max_entry_price),
        "--yes-min-edge",
        str(profile.yes_min_edge),
        "--no-min-entry-price",
        str(profile.no_min_entry_price),
        "--no-max-entry-price",
        str(profile.no_max_entry_price),
        "--no-min-edge",
        str(profile.no_min_edge),
        "--execution-policy",
        "mid_price_core_v1",
        "--accepted-only",
    ]
    if dry_run:
        cmd.append("--dry-run")
    result = _run(cmd, timeout=90)
    return result, _load_json_from_output(result["output"])


def _run_paper_executor(*, plan_path: Path, paper_path: Path, live_path: Path, dry_run: bool) -> tuple[dict[str, Any], dict[str, Any]]:
    if dry_run:
        return {"cmd": ["dry_run_no_executor"], "returncode": 0, "output": ""}, {
            "paper_written": 0,
            "live_requested": False,
            "skipped": "dry_run",
        }
    cmd = [
        sys.executable,
        "scripts/ops/weather_order_executor.py",
        "--plans",
        str(plan_path),
        "--paper-out",
        str(paper_path),
        "--live-out",
        str(live_path),
        "--no-telegram",
    ]
    result = _run(cmd, timeout=90)
    return result, _load_json_from_output(result["output"])


def _run_profile(
    *,
    profile: BlendPaperProfile,
    run_id: str,
    runtime: Path,
    snapshot_lookback_minutes: float,
    min_hours_to_settle: float,
    max_hours_to_settle: float,
    dry_run: bool,
) -> dict[str, Any]:
    slug = _slug(profile.strategy_instance)
    raw_signal_path = runtime / "signals" / f"shadow_{slug}_{run_id}_raw_candidates.jsonl"
    signal_path = runtime / "signals" / f"shadow_{slug}_{run_id}_signals.jsonl"
    plan_path = runtime / "plans" / f"shadow_{slug}_{run_id}_trade_plans.jsonl"
    paper_path = runtime / "paper" / f"shadow_{slug}_{run_id}_paper_orders.jsonl"
    live_path = runtime / "live" / f"shadow_{slug}_{run_id}_orders.jsonl"
    signal_run = _build_candidate_signals(
        profile=profile,
        run_id=run_id,
        out_path=raw_signal_path,
        snapshot_lookback_minutes=snapshot_lookback_minutes,
        min_hours_to_settle=min_hours_to_settle,
        max_hours_to_settle=max_hours_to_settle,
    )
    blend_summary = _blend_signals(profile=profile, raw_path=raw_signal_path, blend_path=signal_path)
    planner_run, planner = _run_planner(profile=profile, signal_path=signal_path, plan_path=plan_path, dry_run=dry_run)
    executor_run, executor = _run_paper_executor(
        plan_path=plan_path,
        paper_path=paper_path,
        live_path=live_path,
        dry_run=dry_run,
    )
    return {
        "profile": profile.__dict__,
        "signal_run": signal_run,
        "blend": blend_summary,
        "planner_run": planner_run,
        "planner": planner,
        "executor_run": executor_run,
        "executor": executor,
        "paths": {
            "raw_signal": str(raw_signal_path),
            "signal": str(signal_path),
            "plan": str(plan_path),
            "paper": str(paper_path),
            "live": str(live_path),
        },
    }


def main() -> int:
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
    except ModuleNotFoundError:
        pass

    parser = argparse.ArgumentParser(description="Run blended weather shadow signals and paper-only orders.")
    parser.add_argument("--profile", choices=tuple(PROFILES.keys()) + ("all",), default=os.getenv("WEATHER_BLEND_PROFILE", "all"))
    parser.add_argument("--no-sync", action="store_true", help="Skip sync_weather_remote.sh before reading snapshots.")
    parser.add_argument("--dry-run", action="store_true", help="Build signals/plans in dry-run mode and skip paper executor.")
    parser.add_argument("--snapshot-lookback-minutes", type=float, default=float(os.getenv("WEATHER_BLEND_SNAPSHOT_LOOKBACK_MINUTES", "90")))
    parser.add_argument("--min-hours-to-settle", type=float, default=float(os.getenv("WEATHER_BLEND_MIN_HOURS_TO_SETTLE", "22")))
    parser.add_argument("--max-hours-to-settle", type=float, default=float(os.getenv("WEATHER_BLEND_MAX_HOURS_TO_SETTLE", "28")))
    args = parser.parse_args()

    run_id = _utc_run_id()
    runtime = ROOT / "runtime" / "weather_edge_v1"
    summary_path = runtime / "live_cycle" / f"{run_id}_weather_edge_engine_blended_shadow_paper.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)

    sync = {"cmd": ["skipped"], "returncode": 0, "output": "sync skipped"}
    if not args.no_sync:
        sync = _run(["bash", "scripts/ops/sync_weather_remote.sh"], timeout=240)

    selected = list(PROFILES.values()) if args.profile == "all" else [PROFILES[str(args.profile)]]
    results = [
        _run_profile(
            profile=profile,
            run_id=run_id,
            runtime=runtime,
            snapshot_lookback_minutes=float(args.snapshot_lookback_minutes),
            min_hours_to_settle=float(args.min_hours_to_settle),
            max_hours_to_settle=float(args.max_hours_to_settle),
            dry_run=bool(args.dry_run),
        )
        for profile in selected
    ]
    summary = {
        "run_id": run_id,
        "strategy_id": selected[0].strategy_id if len(selected) == 1 else "mixed",
        "execution_mode": "paper" if not args.dry_run else "dry_run",
        "sync": sync,
        "profiles": results,
        "paths": {"summary": str(summary_path)},
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    ok = sync["returncode"] == 0 and all(int(r["signal_run"]["returncode"]) == 0 and int(r["planner_run"]["returncode"]) == 0 and int(r["executor_run"]["returncode"]) == 0 for r in results)
    return 0 if ok else 1


if __name__ == "__main__":
    os.chdir(ROOT)
    raise SystemExit(main())
