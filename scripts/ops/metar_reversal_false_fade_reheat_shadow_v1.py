"""METAR reversal Head B shadow v1 (zero-notional journal, no orders).

This runner belongs to the intraday METAR reversal / rich-current-collapse
family, not to the forecast-tail low-price YES lottery sleeve.  It records
both the older `false_fade_reheat_conflict -> d1_yes` trigger and the newer
bracket-aware `rich_current_conflict_b4_triggered` sibling tag from
`hotter-tail-reversal-shapes-v1`.

Execution assumptions for this family are deliberately separate from the
low-price lottery live sleeve: the current offline read is taker entry +
hold-to-settlement for the rich-current branch; TP20 and maker-first are
telemetry/stress fields only.

Watches intraday city-date states and journals the pre-registered
`false_fade_reheat_conflict -> d1_yes` trigger from
`docs/analysis/2026-07/2026-07-03-metar-reversal-expression-matrix-v1.md`:

    BUY d1_yes when, at a local-intraday snapshot,
      d_tmpf_1h >= +0.5F            (still actively warming)
      forecast_max - running >= 1.0 (native units of forecast headroom)
      forecast peak not yet passed  (forecast_peak_delta_hours_local <= 0)
      d1_yes ask <= 0.30            (market prices the next bracket as tail)
      current_high_yes ask >= 0.40  (market leans "current bracket is done")

Historical evidence: 32 rows / 22 dates / 15 cities, ROI +119.5%
CI [+29.8%, +216.2%] at $1 taker on historical asks; top5-removed +27.2%;
robust to +1c. Open risks this shadow exists to resolve: quote staleness of
historical asks, live fill feasibility (median best-ask size was ~16 shares),
and June decay (May +238% vs June +38.6% point).

Every evaluated city-date state row is journaled (triggered or not) so the
forward analysis has the full denominator, fresh-book quotes, and obs ages.
This script never submits orders and never touches the live sleeves.

Usage:
  .venv/bin/python scripts/ops/metar_reversal_false_fade_reheat_shadow_v1.py run
  .venv/bin/python scripts/ops/metar_reversal_false_fade_reheat_shadow_v1.py loop --interval-seconds 900
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.strategies.runtime.production import load_production_spec  # noqa: E402

PRODUCTION_SPEC = load_production_spec()
SNAPSHOT_DIR_DEFAULT = PRODUCTION_SPEC.strategy_paper_snapshot_dir()
OBS_DEFAULT = PRODUCTION_SPEC.observation_cache_path()
RUNTIME_DIR = ROOT / "runtime/weather_edge_v1/metar_reversal_false_fade_reheat_shadow_v1"
DECISIONS_OUT = RUNTIME_DIR / "state_decisions.jsonl"
HISTORY_OUT = RUNTIME_DIR / "summary_history.jsonl"

# frozen false_fade_reheat_conflict thresholds (pre-registered; do not tune in place)
TREND_1H_MIN_F = 0.5
FORECAST_GAP_MIN_NATIVE = 1.0
PEAK_DELTA_MAX_H = 0.0
D1_YES_ASK_MAX = 0.30
CURRENT_YES_ASK_MIN = 0.40
MAX_OBS_AGE_MIN = 120.0


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def configure_runtime_dir(runtime_dir: Path) -> None:
    """Route every mutable artifact through the controller-provided runtime root."""

    global RUNTIME_DIR, DECISIONS_OUT, HISTORY_OUT
    RUNTIME_DIR = runtime_dir
    DECISIONS_OUT = runtime_dir / "state_decisions.jsonl"
    HISTORY_OUT = runtime_dir / "summary_history.jsonl"


def to_float(value: Any, default: float = math.nan) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, sort_keys=True, default=str) + "\n")


def latest_snapshot(snapshot_dir: Path) -> Path | None:
    files = sorted(snapshot_dir.glob("snapshot_*.json"))
    return files[-1] if files else None


def parse_bracket_bounds(bracket: str, question: str) -> tuple[float, float]:
    """returns (low, high) in native market units; +/-inf for edge brackets."""
    b = str(bracket).strip().replace("°", "")
    q = (question or "").lower()
    m = re.match(r"^(-?\d+(?:\.\d+)?)-(-?\d+(?:\.\d+)?)$", b)
    if m:
        return float(m.group(1)), float(m.group(2))
    m = re.match(r"^(-?\d+(?:\.\d+)?)\+$", b)
    if m:
        return float(m.group(1)), math.inf
    m = re.match(r"^(-?\d+(?:\.\d+)?)$", b)
    if m:
        v = float(m.group(1))
        if "or below" in q or "or lower" in q:
            return -math.inf, v
        if "or above" in q or "or higher" in q:
            return v, math.inf
        return v, v
    return math.nan, math.nan


def obs_to_native(value_c: float, unit: str) -> float:
    if not math.isfinite(value_c):
        return math.nan
    return value_c * 9.0 / 5.0 + 32.0 if str(unit).upper() == "F" else value_c


def load_obs(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for rec in payload.get("records", []):
        out[(str(rec.get("city")), str(rec.get("target_date")))] = rec
    return out


def build_states(snapshot_path: Path, obs_index: dict[tuple[str, str], dict[str, Any]]) -> list[dict[str, Any]]:
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    records = payload.get("records", [])
    by_market: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for rec in records:
        city = str(rec.get("city"))
        tdate = str(rec.get("target_date"))
        if str(rec.get("city_local_date_at_snapshot")) != tdate:
            continue  # intraday states only: market date == city local date
        by_market.setdefault((city, tdate), []).append(rec)

    states: list[dict[str, Any]] = []
    for (city, tdate), recs in sorted(by_market.items()):
        obs = obs_index.get((city, tdate))
        if not obs or obs.get("status") != "ok":
            states.append({"city": city, "target_date": tdate, "state_status": "obs_missing"})
            continue
        unit = str(recs[0].get("unit") or obs.get("unit") or "C")
        running_native = obs_to_native(to_float(obs.get("running_max_c")), unit)
        if not math.isfinite(running_native):
            states.append({"city": city, "target_date": tdate, "state_status": "no_running_max"})
            continue

        ladder = []
        for rec in recs:
            low, high = parse_bracket_bounds(str(rec.get("bracket")), str(rec.get("question")))
            if not math.isfinite(low) and not math.isfinite(high):
                continue
            ask = to_float(rec.get("yes_best_ask"))
            quote_source = "clob_book"
            if not math.isfinite(ask):
                ask = to_float(rec.get("market_yes_price"))
                quote_source = "market_yes_price"
            ladder.append(
                {
                    "bracket": str(rec.get("bracket")),
                    "low": low,
                    "high": high,
                    "yes_ask": ask,
                    "yes_ask_size": to_float(rec.get("yes_ask_size")),
                    "quote_source": quote_source,
                    "forecast_max_native": to_float(rec.get("forecast_max_native")),
                    "forecast_peak_delta_hours_local": to_float(rec.get("forecast_peak_delta_hours_local")),
                    "book_status": str(rec.get("yes_book_status")),
                }
            )
        ladder.sort(key=lambda x: (x["low"], x["high"]))
        current = next((b for b in ladder if b["low"] - 0.5 <= running_native <= (b["high"] if math.isfinite(b["high"]) else math.inf) + 0.49), None)
        if current is None:
            states.append({"city": city, "target_date": tdate, "state_status": "no_current_bracket"})
            continue
        above = [b for b in ladder if b["low"] > current["low"]]
        d1 = above[0] if above else None

        forecast_max_native = next((b["forecast_max_native"] for b in ladder if math.isfinite(b["forecast_max_native"])), math.nan)
        peak_delta = next((b["forecast_peak_delta_hours_local"] for b in ladder if math.isfinite(b["forecast_peak_delta_hours_local"])), math.nan)
        gap = forecast_max_native - running_native if math.isfinite(forecast_max_native) else math.nan
        trend_1h = to_float(obs.get("d_tmpf_1h"))
        obs_age = to_float(obs.get("age_min"))

        # bracket-aware forecast position: which ladder step the forecast max lands on
        forecast_steps = math.nan
        if math.isfinite(forecast_max_native):
            fr = round(forecast_max_native)
            for i, b in enumerate(ladder):
                hi = b["high"] if math.isfinite(b["high"]) else math.inf
                if b["low"] - 0.01 <= fr <= hi + 0.01:
                    forecast_steps = i - ladder.index(current)
                    break

        conds = {
            "trend_ok": math.isfinite(trend_1h) and trend_1h >= TREND_1H_MIN_F,
            "gap_ok": math.isfinite(gap) and gap >= FORECAST_GAP_MIN_NATIVE,
            "peak_ahead_ok": math.isfinite(peak_delta) and peak_delta <= PEAK_DELTA_MAX_H,
            "d1_cheap_ok": d1 is not None and math.isfinite(d1["yes_ask"]) and 0.005 < d1["yes_ask"] <= D1_YES_ASK_MAX,
            "current_rich_ok": math.isfinite(current["yes_ask"]) and current["yes_ask"] >= CURRENT_YES_ASK_MIN,
            "obs_fresh_ok": math.isfinite(obs_age) and obs_age <= MAX_OBS_AGE_MIN,
        }
        # B4 variant of the same shape (hotter-tail-reversal-shapes-v1): bracket-aware
        # conflict, richer current anchor, no d1 ask cap; journaled as a sibling tag
        conds_b4 = {
            "trend_ok": conds["trend_ok"],
            "peak_ahead_ok": conds["peak_ahead_ok"],
            "forecast_steps_ge_1": math.isfinite(forecast_steps) and forecast_steps >= 1,
            "current_rich_060": math.isfinite(current["yes_ask"]) and current["yes_ask"] >= 0.60,
            "obs_fresh_ok": conds["obs_fresh_ok"],
        }
        states.append(
            {
                "state_status": "ok",
                "city": city,
                "target_date": tdate,
                "unit": unit,
                "snapshot_file": snapshot_path.name,
                "snapshot_ts_utc": str(payload.get("ts_utc")),
                "obs_station": str(obs.get("station")),
                "obs_age_min": obs_age,
                "obs_cadence_min": to_float(obs.get("cadence_min")),
                "obs_last_utc": str(obs.get("last_obs_utc")),
                "current_temp_c": to_float(obs.get("current_temp_c")),
                "running_max_c": to_float(obs.get("running_max_c")),
                "running_native": running_native,
                "minutes_since_running_max": to_float(obs.get("minutes_since_running_max")),
                "temp_trend_1h_f": trend_1h,
                "temp_trend_3h_f": to_float(obs.get("d_tmpf_3h")),
                "forecast_max_native": forecast_max_native,
                "forecast_gap_native": gap,
                "forecast_peak_delta_hours_local": peak_delta,
                "current_bracket": current["bracket"],
                "current_yes_ask": current["yes_ask"],
                "current_quote_source": current["quote_source"],
                "d1_bracket": d1["bracket"] if d1 else None,
                "d1_yes_ask": d1["yes_ask"] if d1 else None,
                "d1_yes_ask_size": d1["yes_ask_size"] if d1 else None,
                "d1_quote_source": d1["quote_source"] if d1 else None,
                "d1_book_status": d1["book_status"] if d1 else None,
                "forecast_steps": forecast_steps if math.isfinite(forecast_steps) else None,
                "conds": conds,
                "conds_b4": conds_b4,
                "false_fade_reheat_conflict_triggered": all(conds.values()),
                "rich_current_conflict_b4_triggered": all(conds_b4.values()),
                "frozen_thresholds": {
                    "trend_1h_min_f": TREND_1H_MIN_F,
                    "forecast_gap_min_native": FORECAST_GAP_MIN_NATIVE,
                    "peak_delta_max_h": PEAK_DELTA_MAX_H,
                    "d1_yes_ask_max": D1_YES_ASK_MAX,
                    "current_yes_ask_min": CURRENT_YES_ASK_MIN,
                },
            }
        )
    return states


def run_once(args: argparse.Namespace) -> dict[str, Any]:
    snapshot_path = latest_snapshot(Path(args.snapshot_dir))
    if snapshot_path is None:
        return {"status": "no_snapshot", "generated_at_utc": now_utc()}
    obs_index = load_obs(Path(args.observation_cache).expanduser())
    states = build_states(snapshot_path, obs_index)
    cycle_id = hashlib.sha1(f"{snapshot_path.name}|{now_utc()}".encode()).hexdigest()[:12]
    triggered = 0
    triggered_b4 = 0
    for state in states:
        state["cycle_id"] = cycle_id
        state["generated_at_utc"] = now_utc()
        state["mode"] = "zero_notional_shadow"
        append_jsonl(DECISIONS_OUT, state)
        triggered += int(bool(state.get("false_fade_reheat_conflict_triggered")))
        triggered_b4 += int(bool(state.get("rich_current_conflict_b4_triggered")))
    summary = {
        "generated_at_utc": now_utc(),
        "cycle_id": cycle_id,
        "snapshot_file": snapshot_path.name,
        "states": len(states),
        "states_ok": sum(1 for s in states if s.get("state_status") == "ok"),
        "false_fade_reheat_conflict_triggered": triggered,
        "rich_current_conflict_b4_triggered": triggered_b4,
    }
    append_jsonl(HISTORY_OUT, summary)
    print(json.dumps(summary))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["run", "loop"], nargs="?", default="run")
    parser.add_argument("--snapshot-dir", default=str(SNAPSHOT_DIR_DEFAULT))
    parser.add_argument("--observation-cache", default=str(OBS_DEFAULT))
    parser.add_argument("--runtime-dir", default=str(RUNTIME_DIR))
    parser.add_argument("--interval-seconds", type=float, default=900.0)
    args = parser.parse_args()
    configure_runtime_dir(Path(args.runtime_dir).expanduser().resolve())
    if args.command == "run":
        run_once(args)
        return 0
    while True:
        try:
            run_once(args)
        except Exception as exc:  # keep the loop alive; failures are visible in history
            append_jsonl(HISTORY_OUT, {"generated_at_utc": now_utc(), "status": "error", "error": str(exc)})
            print(f"error: {exc}")
        time.sleep(args.interval_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
