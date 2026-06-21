#!/usr/bin/env python3
"""Replay current-YES live candidates through Codex preflight."""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]

import sys

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ops import weather_theta_current_yes_tiny_live as live


TELEMETRY_PATHS = [
    ROOT / "runtime/weather_edge_v1/remote_pm_agent/theta_current_yes_fade_confirmed_tiny_live_v1/forward_telemetry.jsonl",
    ROOT / "runtime/weather_edge_v1/remote_pm_agent/theta_current_yes_peak_forming_micro_tiny_live_v1/forward_telemetry.jsonl",
]
ORDER_PATHS = [
    ROOT / "runtime/weather_edge_v1/remote_pm_agent/live/theta_current_yes_fade_confirmed_tiny_live_v1_orders.jsonl",
    ROOT / "runtime/weather_edge_v1/remote_pm_agent/live/theta_current_yes_peak_forming_micro_tiny_live_v1_orders.jsonl",
]
OUT_DIR = ROOT / "docs/analysis/2026-06/generated/current_yes_codex_preflight_replay_v1"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def safe_float(value: Any) -> float | None:
    try:
        out = float(value)
    except Exception:
        return None
    return out if math.isfinite(out) else None


def order_place_status(row: dict[str, Any]) -> str:
    response = row.get("exchange_response") if isinstance(row.get("exchange_response"), dict) else {}
    place = response.get("place") if isinstance(response.get("place"), dict) else {}
    return str(place.get("status") or row.get("status") or "")


def order_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(row.get("strategy_instance") or ""),
        str(row.get("city") or ""),
        str(row.get("target_date") or ""),
        str(row.get("bracket") or row.get("current_bracket") or ""),
    )


def load_settlements() -> dict[tuple[str, str, str], dict[str, Any]]:
    conn = sqlite3.connect(f"file:{ROOT / 'runtime/weather.db'}?mode=ro", uri=True, timeout=1.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT city, target_date, bracket, final_price, settlement_status
        FROM settlement_outcomes
        WHERE source_system='pm_history'
        """
    ).fetchall()
    return {(r["city"], r["target_date"], r["bracket"]): dict(r) for r in rows}


def local_forecast_cache_path(raw_path: str) -> Path | None:
    if not raw_path:
        return None
    marker = "/runtime/weather_edge_v1/"
    if marker in raw_path:
        suffix = raw_path.split(marker, 1)[1]
        return ROOT / "runtime/weather_edge_v1/remote_pm_agent" / suffix
    path = Path(raw_path)
    return path if path.is_absolute() else ROOT / path


def enrich_forecast_curve(row: dict[str, Any]) -> None:
    cache_path = local_forecast_cache_path(str(row.get("forecast_peak_cache_path") or ""))
    if not cache_path or not cache_path.exists():
        return
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        source = str(row.get("forecast_peak_source") or "")
        model = "ecmwf" if "ecmwf" in source else "gfs"
        details = live.forecast_details_from_open_meteo(payload, source_model=model)
    except Exception:
        return
    if not details:
        return
    row.update({k: v for k, v in details.items() if v is not None})
    try:
        local_dt = datetime.fromisoformat(str(row.get("decision_local_time") or ""))
    except Exception:
        return
    if isinstance(row.get("forecast_temp_path_f"), list):
        row.update(live.forecast_curve_summary(row["forecast_temp_path_f"], now_local=local_dt))


def telemetry_to_preflight_row(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out["local_time"] = row.get("decision_local_time")
    out["timezone"] = row.get("decision_timezone")
    out["unit"] = row.get("unit") or ""
    out["obs"] = {
        "source": row.get("obs_source"),
        "current_temp_c": row.get("current_temp_c"),
        "running_max_c": row.get("running_max_c"),
        "age_min": row.get("obs_age_min"),
        "last_obs_utc": row.get("last_obs_utc"),
        "running_max_obs_utc": row.get("running_max_obs_utc"),
        "minutes_to_next_obs": row.get("minutes_to_next_obs"),
        "minutes_since_running_max": row.get("minutes_since_running_max"),
    }
    enrich_forecast_curve(out)
    return out


def evaluate_rows(rows: list[dict[str, Any]], settlements: dict[tuple[str, str, str], dict[str, Any]]) -> None:
    for row in rows:
        settlement = settlements.get((str(row.get("city")), str(row.get("target_date")), str(row.get("bracket") or row.get("current_bracket"))))
        row["settlement_status"] = (settlement or {}).get("settlement_status", "missing")
        final_price = safe_float((settlement or {}).get("final_price"))
        row["final_price"] = final_price
        row["won"] = bool(final_price is not None and final_price >= 0.99)


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    settled = [r for r in rows if r.get("settlement_status") == "settled"]
    wins = sum(1 for r in settled if r.get("won"))
    pnl = sum(estimated_yes_pnl(r) for r in settled)
    cost = sum(safe_float(r.get("posted_notional")) or 0.0 for r in settled)
    return {
        "rows": len(rows),
        "settled_rows": len(settled),
        "wins": wins,
        "losses": len(settled) - wins,
        "accuracy": round(wins / len(settled), 6) if settled else None,
        "estimated_pnl_usd": round(pnl, 6),
        "estimated_roi": round(pnl / cost, 6) if cost > 0 else None,
    }


def estimated_yes_pnl(row: dict[str, Any]) -> float:
    price = safe_float(row.get("posted_price"))
    cost = safe_float(row.get("posted_notional"))
    if price is None or price <= 0 or cost is None:
        return 0.0
    if row.get("won"):
        return cost / price * (1.0 - price)
    return -cost


def grouped_summary(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for value in sorted({str(r.get(key) or "") for r in rows}):
        out[value] = summarize([r for r in rows if str(r.get(key) or "") == value])
    return out


def run_codex(rows: list[dict[str, Any]], cache_path: Path, limit: int | None) -> dict[str, dict[str, Any]]:
    cache: dict[str, dict[str, Any]] = {}
    if cache_path.exists():
        for row in read_jsonl(cache_path):
            cache[str(row["replay_id"])] = row
    args = argparse.Namespace(
        enable_llm_preflight=True,
        llm_preflight_backend="codex_cli",
        llm_preflight_timeout_seconds=180,
        llm_preflight_codex_model="",
        llm_preflight_codex_reasoning_effort="low",
    )
    done = 0
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with cache_path.open("a", encoding="utf-8") as fh:
        for row in rows:
            replay_id = str(row["replay_id"])
            if replay_id in cache:
                continue
            if limit is not None and done >= limit:
                break
            preflight_row = telemetry_to_preflight_row(row)
            result = live._run_codex_weather_preflight(preflight_row, args)
            record = {"replay_id": replay_id, "codex_preflight": result}
            fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            fh.flush()
            cache[replay_id] = record
            done += 1
    return cache


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date", default="2026-06-18")
    parser.add_argument("--end-date", default="2026-06-21")
    parser.add_argument("--codex", action="store_true")
    parser.add_argument("--codex-limit", type=int, default=None)
    args = parser.parse_args()

    telemetry = []
    for path in TELEMETRY_PATHS:
        telemetry.extend(
            row
            for row in read_jsonl(path)
            if row.get("decision_status") == "planned"
            and args.start_date <= str(row.get("target_date") or "") <= args.end_date
        )
    telemetry.sort(key=lambda r: str(r.get("created_at_utc") or ""))
    for idx, row in enumerate(telemetry):
        row["bracket"] = row.get("current_bracket")
        row["replay_id"] = f"planned:{idx}:{row.get('strategy_instance')}:{row.get('city')}:{row.get('target_date')}:{row.get('current_bracket')}:{row.get('created_at_utc')}"

    orders = []
    for path in ORDER_PATHS:
        orders.extend(
            row
            for row in read_jsonl(path)
            if args.start_date <= str(row.get("target_date") or "") <= args.end_date
        )
    orders.sort(key=lambda r: str(r.get("created_at_utc") or ""))
    for idx, row in enumerate(orders):
        row["place_status"] = order_place_status(row)
        row["replay_id"] = f"order:{idx}:{row.get('strategy_instance')}:{row.get('city')}:{row.get('target_date')}:{row.get('bracket')}:{row.get('created_at_utc')}"

    settlements = load_settlements()
    evaluate_rows(telemetry, settlements)
    evaluate_rows(orders, settlements)
    submitted = [r for r in orders if r.get("status") == "submitted"]
    matched = [r for r in orders if r.get("place_status") == "matched"]

    # Use telemetry rows for Codex because order JSON lacks weather context; the order and planned
    # counts align one-for-one over this rollout window.
    codex_cache = OUT_DIR / "codex_preflight_replay.jsonl"
    codex = run_codex(telemetry, codex_cache, args.codex_limit) if args.codex else {
        str(row["replay_id"]): row for row in read_jsonl(codex_cache)
    }
    telemetry_by_key: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    for row in telemetry:
        telemetry_by_key.setdefault(order_key(row), []).append(row)
    for row in orders:
        matched_planned = telemetry_by_key.get(order_key(row), [])
        if matched_planned:
            planned = min(
                matched_planned,
                key=lambda p: abs(
                    datetime.fromisoformat(str(p.get("created_at_utc")).replace("Z", "+00:00")).timestamp()
                    - datetime.fromisoformat(str(row.get("created_at_utc")).replace("Z", "+00:00")).timestamp()
                ),
            )
            row["planned_replay_id"] = planned["replay_id"]
            cached = codex.get(str(planned["replay_id"]))
            if cached:
                row["codex_preflight"] = cached.get("codex_preflight")

    with_codex = [r for r in matched if isinstance(r.get("codex_preflight"), dict)]
    codex_kept = [r for r in with_codex if r["codex_preflight"].get("decision") == "allow"]
    codex_blocked = [r for r in with_codex if r["codex_preflight"].get("decision") in {"veto", "shadow_only"}]

    result = {
        "window": {"start_date": args.start_date, "end_date": args.end_date},
        "data_sources": {
            "telemetry_paths": [str(p.relative_to(ROOT)) for p in TELEMETRY_PATHS],
            "order_paths": [str(p.relative_to(ROOT)) for p in ORDER_PATHS],
            "settlement_source": "runtime/weather.db:settlement_outcomes",
            "codex_cache": str(codex_cache.relative_to(ROOT)),
        },
        "baseline": {
            "planned": summarize(telemetry),
            "submitted_orders": summarize(submitted),
            "matched_orders": summarize(matched),
            "matched_by_profile": grouped_summary(matched, "strategy_instance"),
            "matched_by_target_date": grouped_summary(matched, "target_date"),
        },
        "codex_replay": {
            "rows_with_codex": len(with_codex),
            "kept_allow": summarize(codex_kept),
            "blocked_veto_or_shadow": summarize(codex_blocked),
            "policy_block_veto_only_keep_shadow": summarize(
                [r for r in with_codex if r["codex_preflight"].get("decision") != "veto"]
            ),
            "policy_block_veto_confidence_ge_0_80": summarize(
                [
                    r
                    for r in with_codex
                    if not (
                        r["codex_preflight"].get("decision") == "veto"
                        and (safe_float(r["codex_preflight"].get("confidence")) or 0.0) >= 0.80
                    )
                ]
            ),
            "kept_by_profile": grouped_summary(codex_kept, "strategy_instance"),
            "blocked_by_profile": grouped_summary(codex_blocked, "strategy_instance"),
            "decisions": {
                decision: sum(1 for r in with_codex if r["codex_preflight"].get("decision") == decision)
                for decision in sorted({r["codex_preflight"].get("decision") for r in with_codex})
            },
        },
        "rows": [
            {
                "created_at_utc": r.get("created_at_utc"),
                "strategy_instance": r.get("strategy_instance"),
                "city": r.get("city"),
                "target_date": r.get("target_date"),
                "bracket": r.get("bracket"),
                "place_status": r.get("place_status"),
                "settlement_status": r.get("settlement_status"),
                "won": r.get("won"),
                "final_price": r.get("final_price"),
                "posted_price": r.get("posted_price"),
                "posted_notional": r.get("posted_notional"),
                "estimated_yes_pnl_usd": round(estimated_yes_pnl(r), 6),
                "codex_decision": (r.get("codex_preflight") or {}).get("decision") if isinstance(r.get("codex_preflight"), dict) else None,
                "codex_confidence": (r.get("codex_preflight") or {}).get("confidence") if isinstance(r.get("codex_preflight"), dict) else None,
                "codex_risk_tags": (r.get("codex_preflight") or {}).get("risk_tags") if isinstance(r.get("codex_preflight"), dict) else None,
                "codex_summary": (r.get("codex_preflight") or {}).get("temperature_pattern_summary") if isinstance(r.get("codex_preflight"), dict) else None,
            }
            for r in matched
        ],
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / "summary.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
