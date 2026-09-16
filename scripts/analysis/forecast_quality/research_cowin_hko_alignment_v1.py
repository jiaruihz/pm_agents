#!/usr/bin/env python3
"""Measure PIT temperature and floor-cross alignment between CoWIN and HKO."""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_INPUT = Path(
    "/Volumes/jrs/weather_data_feed_service_runtime/output/high_frequency_observations/"
    "high_frequency_observations.jsonl"
)


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def load_observations(path: Path) -> list[dict[str, Any]]:
    first_seen: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("city") != "Hong Kong" or row.get("source") not in {"cowin_obs", "hko_obs"}:
                continue
            obs = parse_dt(row.get("observation_time_utc"))
            detect = parse_dt(row.get("local_detect_ts_utc") or row.get("fetched_at_utc"))
            temp = row.get("temp_c")
            if obs is None or detect is None or temp is None:
                continue
            key = (str(row["source"]), str(row.get("target_date") or ""), obs.isoformat(), str(row.get("station") or ""))
            old = first_seen.get(key)
            old_detect = parse_dt((old or {}).get("detect_ts_utc"))
            normalized = {
                "source": row["source"],
                "target_date": row.get("target_date"),
                "station": row.get("station"),
                "obs_ts_utc": obs.isoformat(),
                "detect_ts_utc": detect.isoformat(),
                "temp_c": float(temp),
                "detect_lag_min": (detect - obs).total_seconds() / 60.0,
            }
            if old is None or (old_detect is not None and detect < old_detect):
                first_seen[key] = normalized
    return sorted(first_seen.values(), key=lambda row: (row["target_date"], row["source"], row["obs_ts_utc"]))


def pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 2 or len(xs) != len(ys):
        return None
    mx = statistics.fmean(xs)
    my = statistics.fmean(ys)
    numerator = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    denominator = math.sqrt(sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys))
    return numerator / denominator if denominator else None


def first_cross(rows: list[dict[str, Any]], threshold: int) -> dict[str, Any] | None:
    candidates = [row for row in rows if row["temp_c"] >= threshold]
    return min(candidates, key=lambda row: row["detect_ts_utc"]) if candidates else None


def latest_fresh_by_detection(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_detect: dict[str, dict[str, Any]] = {}
    for row in rows:
        detect = row["detect_ts_utc"]
        old = by_detect.get(detect)
        if old is None or row["obs_ts_utc"] > old["obs_ts_utc"]:
            by_detect[detect] = row
    return sorted(by_detect.values(), key=lambda row: row["detect_ts_utc"])


def persistent_triggers(rows: list[dict[str, Any]], *, bias_c: float) -> list[dict[str, Any]]:
    triggers: list[dict[str, Any]] = []
    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["source"] == "cowin_obs":
            by_date[str(row["target_date"])].append(row)
    for target_date, day_rows in sorted(by_date.items()):
        count = 0
        previous_candidate: int | None = None
        fired: set[int] = set()
        for row in latest_fresh_by_detection(day_rows):
            adjusted = float(row["temp_c"]) - bias_c
            candidate = math.floor(adjusted + 0.5) - 1
            qualifies = adjusted >= candidate + 0.5 - 1e-9
            strong = adjusted >= candidate + 0.7 - 1e-9
            if candidate != previous_candidate or not qualifies:
                count = 1 if qualifies else 0
            else:
                count += 1
            previous_candidate = candidate
            if count >= 2 and strong and candidate not in fired:
                triggers.append(
                    {
                        "target_date": target_date,
                        "bias_c": round(bias_c, 3),
                        "candidate_no_bracket_c": candidate,
                        "implied_hko_floor_c": candidate + 1,
                        "cowin_temp_c": row["temp_c"],
                        "adjusted_cowin_temp_c": round(adjusted, 3),
                        "cowin_obs_ts_utc": row["obs_ts_utc"],
                        "cowin_detect_ts_utc": row["detect_ts_utc"],
                        "cowin_detect_lag_min": round(float(row["detect_lag_min"]), 3),
                    }
                )
                fired.add(candidate)
    return triggers


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--out-dir", default=str(ROOT / "docs/analysis/2026-07/generated/cowin_hko_alignment_v1"))
    parser.add_argument("--report", default=str(ROOT / "docs/analysis/2026-07/2026-07-14-cowin-hko-alignment-v1.md"))
    parser.add_argument("--json", default=str(ROOT / "docs/analysis/2026-07/2026-07-14-cowin-hko-alignment-v1.json"))
    args = parser.parse_args()

    rows = load_observations(Path(args.input))
    by_source_date: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    by_source_obs: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        by_source_date[(row["source"], str(row["target_date"]))].append(row)
        by_source_obs[(row["source"], str(row["target_date"]), row["obs_ts_utc"])] = row

    matched: list[dict[str, Any]] = []
    for (source, target_date, obs_ts), cowin in by_source_obs.items():
        if source != "cowin_obs":
            continue
        hko = by_source_obs.get(("hko_obs", target_date, obs_ts))
        if hko is None:
            continue
        matched.append(
            {
                "target_date": target_date,
                "obs_ts_utc": obs_ts,
                "cowin_temp_c": cowin["temp_c"],
                "hko_temp_c": hko["temp_c"],
                "cowin_minus_hko_c": round(cowin["temp_c"] - hko["temp_c"], 3),
                "cowin_detect_ts_utc": cowin["detect_ts_utc"],
                "hko_detect_ts_utc": hko["detect_ts_utc"],
            }
        )
    deltas = [row["cowin_minus_hko_c"] for row in matched]
    median_bias = statistics.median(deltas) if deltas else 0.0

    current_hk_date = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Hong_Kong")).date().isoformat()
    dates = sorted(
        date
        for date in (
            {date for source, date in by_source_date if source == "cowin_obs"}
            & {date for source, date in by_source_date if source == "hko_obs"}
        )
        if date < current_hk_date
    )
    daily: list[dict[str, Any]] = []
    crossings: list[dict[str, Any]] = []
    for target_date in dates:
        cowin = by_source_date[("cowin_obs", target_date)]
        hko = by_source_date[("hko_obs", target_date)]
        first_floor = math.floor(min(hko, key=lambda row: row["obs_ts_utc"])["temp_c"])
        max_floor = max(math.floor(max(row["temp_c"] for row in cowin)), math.floor(max(row["temp_c"] for row in hko)))
        daily.append(
            {
                "target_date": target_date,
                "cowin_rows": len(cowin),
                "hko_rows": len(hko),
                "cowin_max_c": max(row["temp_c"] for row in cowin),
                "hko_max_c": max(row["temp_c"] for row in hko),
                "max_delta_c": round(max(row["temp_c"] for row in cowin) - max(row["temp_c"] for row in hko), 3),
                "cowin_median_detect_lag_min": round(statistics.median(row["detect_lag_min"] for row in cowin), 3),
                "hko_median_detect_lag_min": round(statistics.median(row["detect_lag_min"] for row in hko), 3),
            }
        )
        for threshold in range(first_floor + 1, max_floor + 1):
            cowin_cross = first_cross(cowin, threshold)
            hko_cross = first_cross(hko, threshold)
            if cowin_cross is None and hko_cross is None:
                continue
            lead = None
            if cowin_cross and hko_cross:
                lead = (
                    parse_dt(hko_cross["detect_ts_utc"]) - parse_dt(cowin_cross["detect_ts_utc"])
                ).total_seconds() / 60.0
            crossings.append(
                {
                    "target_date": target_date,
                    "floor_threshold_c": threshold,
                    "cowin_reached": cowin_cross is not None,
                    "hko_reached": hko_cross is not None,
                    "cowin_detect_ts_utc": cowin_cross["detect_ts_utc"] if cowin_cross else "",
                    "hko_detect_ts_utc": hko_cross["detect_ts_utc"] if hko_cross else "",
                    "cowin_lead_min": round(lead, 3) if lead is not None else "",
                }
            )

    hko_max_by_date = {
        date: math.floor(max(row["temp_c"] for row in by_source_date[("hko_obs", date)])) for date in dates
    }
    trigger_sets: dict[str, list[dict[str, Any]]] = {
        "raw": persistent_triggers(rows, bias_c=0.0),
        "median_bias_adjusted": persistent_triggers(rows, bias_c=median_bias),
    }
    trigger_summary: dict[str, dict[str, Any]] = {}
    for name, trigger_rows in trigger_sets.items():
        evaluated: list[dict[str, Any]] = []
        for row in trigger_rows:
            target_date = row["target_date"]
            if target_date not in hko_max_by_date:
                continue
            trigger_detect = parse_dt(row["cowin_detect_ts_utc"])
            known_hko = [
                hko_row
                for hko_row in by_source_date[("hko_obs", target_date)]
                if parse_dt(hko_row["detect_ts_utc"]) <= trigger_detect
            ]
            if not known_hko:
                continue
            row["hko_running_floor_at_trigger_c"] = math.floor(max(hko_row["temp_c"] for hko_row in known_hko))
            if row["candidate_no_bracket_c"] < row["hko_running_floor_at_trigger_c"]:
                continue
            row["hko_daily_max_floor_c"] = hko_max_by_date[row["target_date"]]
            row["hko_floor_confirmed"] = hko_max_by_date[row["target_date"]] >= row["implied_hko_floor_c"]
            row["variant"] = name
            evaluated.append(row)
        trigger_sets[name] = evaluated
        hits = sum(bool(row["hko_floor_confirmed"]) for row in evaluated)
        trigger_summary[name] = {
            "triggers": len(evaluated),
            "hits": hits,
            "precision": round(hits / len(evaluated), 4) if evaluated else None,
            "active_dates": len({row["target_date"] for row in evaluated}),
        }

    true_crosses = [row for row in crossings if row["cowin_reached"] and row["hko_reached"]]
    cowin_crosses = [row for row in crossings if row["cowin_reached"]]
    daily_max_deltas = [float(row["max_delta_c"]) for row in daily]
    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "coverage_dates": dates,
        "deduped_rows": len(rows),
        "matched_same_minute_rows": len(matched),
        "temperature_pearson": round(pearson([row["cowin_temp_c"] for row in matched], [row["hko_temp_c"] for row in matched]) or 0.0, 4),
        "mean_cowin_minus_hko_c": round(statistics.fmean(deltas), 3) if deltas else None,
        "median_cowin_minus_hko_c": round(median_bias, 3) if deltas else None,
        "mae_cowin_vs_hko_c": round(statistics.fmean(abs(value) for value in deltas), 3) if deltas else None,
        "raw_floor_cross_precision": round(sum(row["hko_reached"] for row in cowin_crosses) / len(cowin_crosses), 4) if cowin_crosses else None,
        "shared_floor_crosses": len(true_crosses),
        "median_cowin_detect_lead_min": round(statistics.median(float(row["cowin_lead_min"]) for row in true_crosses), 3) if true_crosses else None,
        "persistent": trigger_summary,
    }
    payload = {"summary": summary, "daily": daily, "crossings": crossings, "persistent_triggers": trigger_sets}

    out_dir = Path(args.out_dir)
    write_csv(out_dir / "matched_same_minute.csv", matched)
    write_csv(out_dir / "daily_summary.csv", daily)
    write_csv(out_dir / "floor_crossings.csv", crossings)
    write_csv(out_dir / "persistent_triggers_raw.csv", trigger_sets["raw"])
    write_csv(out_dir / "persistent_triggers_bias_adjusted.csv", trigger_sets["median_bias_adjusted"])
    Path(args.json).write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    lines = [
        "# CoWIN to HKO Alignment v1",
        "",
        "Status: `snapshot`",
        f"Generated: `{summary['generated_at_utc']}`",
        "",
        "## Summary",
        "",
        f"- Coverage: `{', '.join(dates)}` ({len(dates)} overlapping days).",
        f"- Same-minute matched rows: `{summary['matched_same_minute_rows']}`; Pearson `{summary['temperature_pearson']}`.",
        f"- CoWIN-HKO bias: mean `{summary['mean_cowin_minus_hko_c']} C`, median `{summary['median_cowin_minus_hko_c']} C`, MAE `{summary['mae_cowin_vs_hko_c']} C`.",
        f"- Raw floor-cross precision: `{summary['raw_floor_cross_precision']}`; shared crosses `{summary['shared_floor_crosses']}`; median PIT detection lead `{summary['median_cowin_detect_lead_min']} min`.",
        f"- Raw persistent rule: `{trigger_summary['raw']}`.",
        f"- Median-bias-adjusted persistent rule: `{trigger_summary['median_bias_adjusted']}`.",
        "",
        "## Daily Max",
        "",
        "| date | CoWIN max | HKO max | delta | CoWIN detect lag | HKO detect lag |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in daily:
        lines.append(
            f"| {row['target_date']} | {row['cowin_max_c']:.1f} | {row['hko_max_c']:.1f} | {row['max_delta_c']:+.1f} | "
            f"{row['cowin_median_detect_lag_min']:.1f}m | {row['hko_median_detect_lag_min']:.1f}m |"
        )
    lines.extend(
        [
            "",
            "## Verdict",
            "",
            "significance=NA; baseline=NA; forward=NA; conclusion=inconclusive",
            "",
            "- The HKO official-lock runner has no demonstrated entry edge: by the time HKO confirms the floor, the NO book is usually absent or already above the configured price cap.",
            f"- CoWIN is useful as an earlier predictor, not as a substitute settlement observation. Its same-minute correlation with HKO is strong, but the daily maximum basis ranges from "
            f"{min(daily_max_deltas):+.1f} C to {max(daily_max_deltas):+.1f} C in this sample.",
            f"- The {summary['median_cowin_detect_lead_min']}-minute median shared-cross lead mixes publication latency with station-temperature basis. It must not be interpreted as pure feed-speed advantage.",
            f"- Even after a fixed median-bias adjustment, the persistent rule has "
            f"{trigger_summary['median_bias_adjusted']['triggers'] - trigger_summary['median_bias_adjusted']['hits']} false triggers in "
            f"{trigger_summary['median_bias_adjusted']['triggers']} opportunities. That is not sufficient for live exact-bracket NO orders.",
            "- Recommended next state: keep HKO official-lock as telemetry and run a separate CoWIN-to-HKO probabilistic shadow with dynamic intraday bias and trigger-time book capture.",
            "",
            f"This report uses {len(dates)} overlapping dates, PIT local detection timestamps, and no execution/PnL denominator. It does not authorize CoWIN-based live orders.",
            "",
        ]
    )
    Path(args.report).write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
