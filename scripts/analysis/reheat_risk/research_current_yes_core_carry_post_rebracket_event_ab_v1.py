#!/usr/bin/env python3
"""PIT replay of fixed checkpoints versus post-rebracket Core Carry scoring.

This is a research replay only.  It uses the immutable Mac observation history,
the targeted paper-snapshot archive, the frozen production v3 probability
artifact, full five-share ask depth, official fees, and pm_history settlement.
It does not submit or simulate maker fills.
"""

from __future__ import annotations

import argparse
import bisect
import gzip
import hashlib
import json
import math
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src.strategies.weather_edge_v1.tools.current_yes_core_carry import (  # noqa: E402
    official_weather_fee_per_share,
    score_probability,
    walk_ask_ladder,
)
from weather_data_feed.market_brackets import parse_market_bracket  # noqa: E402


DEFAULT_ARTIFACT = Path(
    "/Users/deepsleep/projects/pm_agents_prod/"
    "src/strategies/weather_edge_v1/config/current_yes_core_carry_model_v3.json"
)
OBS_ROOT = Path("/Volumes/jrs/weather_data_feed_service_runtime/output/observations")
SNAPSHOT_ROOT = Path(
    "/Volumes/jrs/weather_data_feed_service_runtime/targeted_output/paper_snapshots"
)
PM_HISTORY = ROOT / "runtime/weather_edge_v1/market_data/cache/pm_history"
OUT_ROOT = ROOT / "docs/analysis/2026-07"
OUT_JSON = OUT_ROOT / "2026-07-30-current-yes-core-carry-post-rebracket-event-ab-v1.json"
OUT_MD = OUT_ROOT / "2026-07-30-current-yes-core-carry-post-rebracket-event-ab-v1.md"
GENERATED = OUT_ROOT / "generated/current_yes_core_carry_post_rebracket_event_ab_v1"
QUANTITY = 5.0
MAX_EVENT_BOOK_LAG_MIN = 20.0
ARCHIVE_CACHE: dict[Path, dict[tuple[str, str, str, datetime | None], list[dict[str, Any]]]] = {}


def dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def finite(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def round_half_up(value: float) -> int:
    return int(math.floor(float(value) + 0.5))


def native_running(row: Mapping[str, Any]) -> int | None:
    value = finite(row.get("running_max_c"))
    if value is None:
        return None
    native = value if str(row.get("unit") or "").upper() == "C" else value * 9.0 / 5.0 + 32.0
    return round_half_up(native)


def interval(record: Mapping[str, Any]) -> tuple[float, float] | None:
    parsed = parse_market_bracket(str(record.get("bracket") or ""), str(record.get("question") or ""))
    if parsed is None:
        return None
    if parsed.bottom:
        return (-math.inf, float(parsed.high) + 0.5) if parsed.high is not None else None
    if parsed.top:
        return (float(parsed.low) - 0.5, math.inf) if parsed.low is not None else None
    if parsed.low is None or parsed.high is None:
        return None
    return (float(parsed.low) - 0.5, float(parsed.high) + 0.5)


def current_record(records: Iterable[Mapping[str, Any]], running: int) -> Mapping[str, Any] | None:
    for record in records:
        bounds = interval(record)
        if bounds is not None and bounds[0] <= running < bounds[1]:
            return record
    return None


def is_bounded_exact(record: Mapping[str, Any]) -> bool:
    parsed = parse_market_bracket(str(record.get("bracket") or ""), str(record.get("question") or ""))
    return bool(parsed and not parsed.bottom and not parsed.top)


def load_artifact(path: Path) -> dict[str, Any]:
    artifact = json.loads(path.read_text(encoding="utf-8"))
    unsigned = {key: value for key, value in artifact.items() if key != "artifact_hash"}
    actual = hashlib.sha256(
        json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if actual != artifact.get("artifact_hash"):
        raise ValueError(f"artifact hash mismatch: {actual}")
    return artifact


def load_observations(start: str, end: str) -> tuple[dict[tuple[str, str], list[dict[str, Any]]], dict[str, int]]:
    raw: dict[tuple[str, str, str], dict[str, Any]] = {}
    counts: Counter[str] = Counter()
    for day_dir in sorted(OBS_ROOT.iterdir()):
        if not day_dir.is_dir() or not start <= day_dir.name <= end:
            continue
        path = day_dir / "observations.jsonl"
        if not path.exists():
            continue
        counts["history_files"] += 1
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                counts["raw_rows"] += 1
                row = json.loads(line)
                if row.get("status") != "ok":
                    continue
                city, target_date = str(row.get("city") or ""), str(row.get("target_date") or "")
                report_ts, fetched = str(row.get("last_obs_utc") or ""), dt(row.get("fetched_at_utc"))
                running = native_running(row)
                if not city or not target_date or not report_ts or fetched is None or running is None:
                    continue
                key = (city, target_date, report_ts)
                previous = raw.get(key)
                if previous is None or fetched < previous["_fetched"]:
                    raw[key] = {**row, "_fetched": fetched, "_running_native": running}
    by_key: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in raw.values():
        by_key[(str(row["city"]), str(row["target_date"]))].append(row)
    for rows in by_key.values():
        rows.sort(key=lambda row: (row["_fetched"], str(row.get("last_obs_utc") or "")))
        previous_running: int | None = None
        for row in rows:
            running = int(row["_running_native"])
            row["_new_high"] = previous_running is not None and running > previous_running
            row["_previous_running_native"] = previous_running
            previous_running = running if previous_running is None else max(previous_running, running)
    counts["first_seen_reports"] = len(raw)
    counts["city_days"] = len(by_key)
    counts["strict_new_high_reports"] = sum(
        bool(row["_new_high"]) for rows in by_key.values() for row in rows
    )
    return dict(by_key), dict(counts)


def load_winners(
    start: str, end: str, keys: Iterable[tuple[str, str]] | None = None
) -> tuple[dict[tuple[str, str], str], dict[str, int]]:
    winners: dict[tuple[str, str], str] = {}
    counts: Counter[str] = Counter()
    paths = (
        [PM_HISTORY / f"{city}_{target_date}.json" for city, target_date in sorted(set(keys))]
        if keys is not None
        else PM_HISTORY.glob("*_????-??-??.json")
    )
    for path in paths:
        if not path.exists():
            continue
        city, target_date = path.stem.rsplit("_", 1)
        if not start <= target_date <= end:
            continue
        counts["files"] += 1
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        found = []
        for bracket in (payload or {}).get("brackets", []):
            price = finite(bracket.get("final_price"))
            if price is not None and price >= 0.99:
                found.append(str(bracket.get("label") or "").replace("°", "").strip())
        if len(found) == 1:
            winners[(city, target_date)] = found[0]
    counts["settled_city_days"] = len(winners)
    return winners, dict(counts)


def archived_asks(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    path = Path(str(record.get("yes_book_archive_path") or ""))
    if not path.exists():
        return []
    wanted_city = str(record.get("city") or "")
    wanted_date = str(record.get("target_date") or record.get("event_date") or "")
    wanted_bracket = str(record.get("bracket") or "")
    wanted_ts = dt(record.get("snapshot_ts_utc") or record.get("ts_utc"))
    if path not in ARCHIVE_CACHE:
        books: dict[tuple[str, str, str, datetime | None], list[dict[str, Any]]] = {}
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                if row.get("status") == "ok" and str(row.get("outcome") or "").lower() == "yes":
                    key = (
                        str(row.get("city") or ""),
                        str(row.get("event_date") or ""),
                        str(row.get("bracket") or ""),
                        dt(row.get("snapshot_ts_utc")),
                    )
                    books[key] = list((row.get("raw") or {}).get("asks") or [])
        ARCHIVE_CACHE[path] = books
    return ARCHIVE_CACHE[path].get((wanted_city, wanted_date, wanted_bracket, wanted_ts), [])


def ladder_cost(record: Mapping[str, Any]) -> dict[str, Any]:
    ask, size = finite(record.get("yes_best_ask")), finite(record.get("yes_ask_size"))
    if ask is not None and size is not None and size >= QUANTITY:
        out = walk_ask_ladder([{"price": ask, "size": size}], QUANTITY)
        out["source"] = "top_ask"
        return out
    out = walk_ask_ladder(archived_asks(record), QUANTITY)
    out["source"] = "archived_full_ladder"
    return out


def score_state(
    record: Mapping[str, Any],
    obs: Mapping[str, Any],
    artifact: Mapping[str, Any],
    local_hour: int,
) -> dict[str, Any]:
    bid, ask = finite(record.get("yes_best_bid")), finite(record.get("yes_best_ask"))
    mid = None if bid is None or ask is None or not 0 < bid <= ask < 1 else (bid + ask) / 2
    features = {
        "market_logit": None if mid is None else math.log(mid / (1 - mid)),
        "decision_hour_local": local_hour,
        "dewpoint_depression_f": finite(obs.get("dewpoint_depression_f")),
        "wind_speed_kt": finite(obs.get("wind_speed_kt")),
    }
    missing = [name for name in artifact["numeric_features"] if finite(features.get(name)) is None]
    probability = None if mid is None else score_probability(features, artifact)
    ladder = ladder_cost(record)
    cost = finite(ladder.get("effective_cost_per_share"))
    edge = None if probability is None or cost is None else probability - cost
    policy = artifact["entry_policy"]
    support = artifact.get("numeric_feature_support", {})
    support_bad = [
        name
        for name in policy.get("enforce_training_support_live_features", [])
        if finite(features.get(name)) is not None
        and (
            float(features[name]) < float(support[name]["min"])
            or float(features[name]) > float(support[name]["max"])
        )
    ]
    eligible = bool(
        is_bounded_exact(record)
        and not missing
        and not support_bad
        and mid is not None
        and float(policy["market_mid_floor"]) <= mid <= float(policy["market_mid_ceiling"])
        and ladder["executable"]
        and edge is not None
        and edge > float(policy["min_edge_after_fee_and_depth"])
    )
    return {
        "market_mid": mid,
        "model_probability": probability,
        "cost_per_share": cost,
        "edge": edge,
        "eligible": eligible,
        "missing_features": ",".join(missing),
        "support_violations": ",".join(support_bad),
        "cost_source": ladder["source"],
        "principal_5": ladder.get("principal"),
        "fee_5": ladder.get("fee"),
    }


def load_candidates(
    start: str,
    end: str,
    observations: dict[tuple[str, str], list[dict[str, Any]]],
    artifact: Mapping[str, Any],
) -> tuple[pd.DataFrame, dict[str, int]]:
    obs_times = {key: [row["_fetched"] for row in rows] for key, rows in observations.items()}
    rows_out: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    fixed_seen: set[tuple[str, str, int]] = set()
    event_seen: set[tuple[str, str, str]] = set()
    start_file_day = (datetime.fromisoformat(start) - pd.Timedelta(days=1)).strftime("%Y%m%d")
    end_file_day = (datetime.fromisoformat(end) + pd.Timedelta(days=1)).strftime("%Y%m%d")
    for path in sorted(SNAPSHOT_ROOT.glob("snapshot_*.json")):
        match = re.match(r"snapshot_(\d{8})_", path.name)
        if not match or not start_file_day <= match.group(1) <= end_file_day:
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        payload_available = dt(
            payload.get("available_at_utc")
            or payload.get("published_at_utc")
            or payload.get("ts_utc")
        )
        if payload_available is None:
            continue
        groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for raw in payload.get("records") or []:
            city = str(raw.get("city") or "")
            target_date = str(raw.get("target_date") or raw.get("event_date") or "")
            if start <= target_date <= end and str(raw.get("city_local_date_at_snapshot") or "") == target_date:
                groups[(city, target_date)].append(raw)
        if not groups:
            continue
        counts["snapshot_files"] += 1
        for key, market_rows in groups.items():
            book_times = [
                dt(row.get("yes_book_fetched_at_utc"))
                for row in market_rows
                if dt(row.get("yes_book_fetched_at_utc")) is not None
            ]
            # Old snapshots predate payload.available_at_utc.  The latest book
            # fetch is a conservative lower bound for when this city ladder was
            # actually available to a strategy.
            available = max([payload_available, *book_times])
            obs_rows = observations.get(key)
            if not obs_rows:
                continue
            idx = bisect.bisect_right(obs_times[key], available) - 1
            if idx < 0:
                continue
            obs = obs_rows[idx]
            running = int(obs["_running_native"])
            current = current_record(market_rows, running)
            if current is None:
                counts["state_without_current_rung"] += 1
                continue
            timezone_name = str(current.get("timezone_name") or obs.get("timezone_name") or "")
            if not timezone_name:
                continue
            local = available.astimezone(ZoneInfo(timezone_name))
            if local.date().isoformat() != key[1] or not 13 <= local.hour <= 17:
                continue
            fixed_key = (key[0], key[1], local.hour)
            is_fixed = local.minute >= 30 and fixed_key not in fixed_seen
            event_id = str(obs.get("last_obs_utc") or "")
            event_lag = (available - obs["_fetched"]).total_seconds() / 60
            previous_running = obs.get("_previous_running_native")
            previous_record = (
                current_record(market_rows, int(previous_running))
                if previous_running is not None
                else None
            )
            really_rebracketed = bool(
                obs.get("_new_high")
                and previous_record is not None
                and str(previous_record.get("bracket")) != str(current.get("bracket"))
            )
            event_key = (key[0], key[1], event_id)
            is_event = (
                really_rebracketed
                and 0 <= event_lag <= MAX_EVENT_BOOK_LAG_MIN
                and event_key not in event_seen
            )
            if not is_fixed and not is_event:
                continue
            scored = score_state(current, obs, artifact, local.hour)
            successfully_scored = (
                scored["model_probability"] is not None
                and scored["market_mid"] is not None
                and not scored["missing_features"]
            )
            if is_fixed and successfully_scored:
                fixed_seen.add(fixed_key)
            elif is_fixed:
                is_fixed = False
            if is_event and successfully_scored:
                event_seen.add(event_key)
            elif is_event:
                is_event = False
            if not is_fixed and not is_event:
                continue
            rows_out.append(
                {
                    "city": key[0],
                    "target_date": key[1],
                    "available_at_utc": available.isoformat(),
                    "local_time": local.isoformat(),
                    "local_hour": local.hour,
                    "local_minute": local.minute,
                    "candidate_fixed": is_fixed,
                    "candidate_event": is_event,
                    "event_id": event_id if is_event else "",
                    "event_lag_min": event_lag if is_event else None,
                    "previous_running_native": previous_running,
                    "running_native": running,
                    "bracket": str(current.get("bracket") or ""),
                    "condition_id": str(current.get("condition_id") or ""),
                    **scored,
                }
            )
    counts["fixed_checkpoints"] = sum(bool(row["candidate_fixed"]) for row in rows_out)
    counts["post_rebracket_checkpoints"] = sum(bool(row["candidate_event"]) for row in rows_out)
    counts["candidate_rows"] = len(rows_out)
    return pd.DataFrame(rows_out), dict(counts)


def select_first(frame: pd.DataFrame, mask: pd.Series) -> pd.DataFrame:
    chosen = frame[mask & frame["eligible"]].copy()
    if chosen.empty:
        return chosen
    chosen["_ts"] = pd.to_datetime(chosen["available_at_utc"], utc=True, format="mixed")
    return (
        chosen.sort_values(["target_date", "_ts", "city"])
        .drop_duplicates(["city", "target_date"], keep="first")
        .drop(columns="_ts")
    )


def performance(entries: pd.DataFrame, winners: Mapping[tuple[str, str], str]) -> dict[str, Any]:
    if entries.empty:
        return {"entries": 0, "settled": 0}
    work = entries.copy()
    work["winner"] = [winners.get((row.city, row.target_date)) for row in work.itertuples()]
    work = work[work["winner"].notna()].copy()
    work["win"] = work["bracket"].astype(str).eq(work["winner"].astype(str))
    work["cost_5"] = QUANTITY * pd.to_numeric(work["cost_per_share"])
    work["pnl_5"] = QUANTITY * work["win"].astype(float) - work["cost_5"]
    work["brier"] = (pd.to_numeric(work["model_probability"]) - work["win"].astype(float)) ** 2
    total_cost = float(work["cost_5"].sum())
    return {
        "entries": int(len(entries)),
        "settled": int(len(work)),
        "city_days": int(work[["city", "target_date"]].drop_duplicates().shape[0]),
        "wins": int(work["win"].sum()),
        "win_rate": float(work["win"].mean()) if len(work) else None,
        "cost_usd": total_cost,
        "pnl_usd": float(work["pnl_5"].sum()),
        "roi": float(work["pnl_5"].sum() / total_cost) if total_cost else None,
        "mean_brier": float(work["brier"].mean()) if len(work) else None,
        "date_min": str(work["target_date"].min()) if len(work) else None,
        "date_max": str(work["target_date"].max()) if len(work) else None,
        "_rows": work,
    }


def paired_bootstrap(
    fixed_rows: pd.DataFrame, hybrid_rows: pd.DataFrame, *, seed: int = 20260730, n: int = 10000
) -> dict[str, Any]:
    def by_date(frame: pd.DataFrame) -> dict[str, float]:
        return frame.groupby("target_date")["pnl_5"].sum().to_dict() if not frame.empty else {}

    left, right = by_date(fixed_rows), by_date(hybrid_rows)
    dates = sorted(set(left) | set(right))
    if not dates:
        return {"date_blocks": 0}
    delta = np.array([right.get(day, 0.0) - left.get(day, 0.0) for day in dates])
    rng = np.random.default_rng(seed)
    samples = rng.choice(delta, size=(n, len(delta)), replace=True).sum(axis=1)
    return {
        "date_blocks": len(dates),
        "pnl_delta_hybrid_minus_fixed": float(delta.sum()),
        "ci95": [float(np.quantile(samples, 0.025)), float(np.quantile(samples, 0.975))],
        "probability_delta_positive": float((samples > 0).mean()),
    }


def event_domain_diagnostic(
    candidates: pd.DataFrame, winners: Mapping[tuple[str, str], str], artifact: Mapping[str, Any]
) -> dict[str, Any]:
    events = candidates[candidates["candidate_event"]].copy()
    positive = events[pd.to_numeric(events["edge"], errors="coerce").gt(0)].copy()
    floor = float(artifact["entry_policy"]["market_mid_floor"])
    ceiling = float(artifact["entry_policy"]["market_mid_ceiling"])
    positive["winner"] = [
        winners.get((row.city, row.target_date)) for row in positive.itertuples()
    ]
    settled = positive[positive["winner"].notna()].copy()
    settled["win"] = settled["bracket"].astype(str).eq(settled["winner"].astype(str))
    settled["cost_5"] = QUANTITY * pd.to_numeric(settled["cost_per_share"])
    settled["pnl_5"] = QUANTITY * settled["win"].astype(float) - settled["cost_5"]

    def summarize(frame: pd.DataFrame) -> dict[str, Any]:
        cost = float(frame["cost_5"].sum()) if len(frame) else 0.0
        return {
            "settled": int(len(frame)),
            "wins": int(frame["win"].sum()) if len(frame) else 0,
            "win_rate": float(frame["win"].mean()) if len(frame) else None,
            "cost_usd": cost,
            "pnl_usd": float(frame["pnl_5"].sum()) if len(frame) else 0.0,
            "roi": float(frame["pnl_5"].sum() / cost) if cost else None,
        }

    low_mid = settled[settled["market_mid"].between(0.5, floor, inclusive="left")]
    return {
        "event_checkpoints": int(len(events)),
        "positive_taker_ev_before_frozen_domain": int(len(positive)),
        "positive_ev_below_mid_floor": int((positive["market_mid"] < floor).sum()),
        "positive_ev_above_mid_ceiling": int((positive["market_mid"] > ceiling).sum()),
        "all_positive_ev_out_of_domain_settled": summarize(settled),
        "mid_0p50_to_0p80_exploratory": summarize(low_mid),
        "cases": settled[
            [
                "city",
                "target_date",
                "local_time",
                "bracket",
                "market_mid",
                "model_probability",
                "cost_per_share",
                "edge",
                "winner",
                "win",
                "pnl_5",
            ]
        ].to_dict(orient="records"),
        "status": "diagnostic_only_model_extrapolation_not_an_eligible_policy",
    }


def clean_perf(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != "_rows"}


def fmt_pct(value: Any) -> str:
    return "NA" if value is None else f"{100 * float(value):.2f}%"


def fmt_float(value: Any, digits: int = 4) -> str:
    return "NA" if value is None else f"{float(value):.{digits}f}"


def write_report(payload: Mapping[str, Any]) -> None:
    policies = payload["policies"]
    lines = [
        "# Current-YES Core Carry：post-rebracket event-driven A/B v1",
        "",
        "Status: research replay / no live change",
        "",
        "## 结论",
        "",
        str(payload["conclusion"]),
        "",
        "## 同分母结果",
        "",
        "| policy | settled entries | wins | win rate | cost | PnL | ROI | Brier |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name in ("fixed_only", "event_only", "hybrid"):
        row = policies[name]
        lines.append(
            f"| {name} | {row.get('settled', 0)} | {row.get('wins', 0)} | "
            f"{fmt_pct(row.get('win_rate'))} | ${row.get('cost_usd', 0):.2f} | "
            f"${row.get('pnl_usd', 0):.2f} | {fmt_pct(row.get('roi'))} | "
            f"{fmt_float(row.get('mean_brier'))} |"
        )
    lines += [
        "",
        "所有 policy 使用同一 frozen v3 probability、同一 5-share ask ladder、同一官方 fee。"
        "`hybrid` 是 fixed `:30` checkpoint 加 post-rebracket checkpoint，且仍按 city-day 首个正 EV 锁定。",
        "",
        "## Coverage / 事件漏斗",
        "",
        "```json",
        json.dumps(payload["coverage"], ensure_ascii=False, indent=2),
        "```",
        "",
        "## 配对不确定性",
        "",
        "```json",
        json.dumps(payload["paired_date_bootstrap"], ensure_ascii=False, indent=2),
        "```",
        "",
        "## Event 域外诊断（不计入 A/B policy）",
        "",
        "```json",
        json.dumps(payload["event_domain_diagnostic"], ensure_ascii=False, indent=2),
        "```",
        "",
        "这些行的正 EV 全部位于 frozen Core Carry mid domain 之外；其结算只说明值得建独立"
        " `post-cross low/mid` forward shadow，不构成删除现有 0.80 floor 的证据。",
        "",
        "## 口径边界",
        "",
        "- observation 以 `(city,target_date,last_obs_utc)` first-seen 去重，严格要求 fetched_at 不晚于盘口发布。",
        "- rebracket 必须使 settlement native lattice 的 current exact bracket 改变；普通升温不算事件。",
        f"- 只接受事件后 {MAX_EVENT_BOOK_LAG_MIN:.0f} 分钟内第一份 targeted snapshot；缺盘口记 coverage gap。",
        "- 这是 7/18 后短窗口、约 15 分钟盘口粒度的 research replay，不是假设 maker 成交，也不是 live_real PnL。",
        "- 旧 hourly archive 无法还原事件时点，因此没有把 5–7 月旧样本伪装成 event-driven 回测。",
        "",
        "## Artifact",
        "",
        f"- model: `{payload['artifact']['version']}`",
        f"- hash: `{payload['artifact']['hash']}`",
        f"- source: `{payload['artifact']['path']}`",
        "",
    ]
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date", default="2026-07-18")
    parser.add_argument("--end-date", default="2026-07-29")
    parser.add_argument("--artifact", type=Path, default=DEFAULT_ARTIFACT)
    parser.add_argument(
        "--reuse-candidates",
        action="store_true",
        help="Reuse the persisted candidate checkpoint CSV for report-only reruns.",
    )
    args = parser.parse_args()

    artifact = load_artifact(args.artifact)
    observations, observation_counts = load_observations(args.start_date, args.end_date)
    candidate_cache = GENERATED / "candidate_checkpoints.csv"
    if args.reuse_candidates and candidate_cache.exists():
        candidates = pd.read_csv(candidate_cache)
        start_file_day = (datetime.fromisoformat(args.start_date) - pd.Timedelta(days=1)).strftime("%Y%m%d")
        end_file_day = (datetime.fromisoformat(args.end_date) + pd.Timedelta(days=1)).strftime("%Y%m%d")
        snapshot_files = sum(
            bool(
                (match := re.match(r"snapshot_(\d{8})_", path.name))
                and start_file_day <= match.group(1) <= end_file_day
            )
            for path in SNAPSHOT_ROOT.glob("snapshot_*.json")
        )
        candidate_counts = {
            "snapshot_files_in_filename_window": snapshot_files,
            "fixed_checkpoints": int(candidates["candidate_fixed"].sum()),
            "post_rebracket_checkpoints": int(candidates["candidate_event"].sum()),
            "candidate_rows": int(len(candidates)),
            "source": "persisted_candidate_checkpoint_csv",
        }
    else:
        candidates, candidate_counts = load_candidates(
            args.start_date, args.end_date, observations, artifact
        )
    if candidates.empty:
        raise RuntimeError("no replay candidates")
    candidate_keys = set(zip(candidates["city"].astype(str), candidates["target_date"].astype(str)))
    winners, settlement_counts = load_winners(
        args.start_date, args.end_date, candidate_keys
    )
    GENERATED.mkdir(parents=True, exist_ok=True)
    # Persist the expensive raw replay before downstream selection/reporting.
    candidates.to_csv(GENERATED / "candidate_checkpoints.csv", index=False)
    fixed = select_first(candidates, candidates["candidate_fixed"])
    event = select_first(candidates, candidates["candidate_event"])
    hybrid = select_first(candidates, candidates["candidate_fixed"] | candidates["candidate_event"])
    perfs = {
        "fixed_only": performance(fixed, winners),
        "event_only": performance(event, winners),
        "hybrid": performance(hybrid, winners),
    }
    bootstrap = paired_bootstrap(
        perfs["fixed_only"].get("_rows", pd.DataFrame()),
        perfs["hybrid"].get("_rows", pd.DataFrame()),
    )
    domain_diagnostic = event_domain_diagnostic(candidates, winners, artifact)
    fixed_keys = set(zip(fixed["city"], fixed["target_date"])) if not fixed.empty else set()
    event_keys = set(zip(event["city"], event["target_date"])) if not event.empty else set()
    hybrid_keys = set(zip(hybrid["city"], hybrid["target_date"])) if not hybrid.empty else set()
    additional = sorted(hybrid_keys - fixed_keys)
    replaced = sorted((fixed_keys & hybrid_keys) & event_keys)
    delta = float(bootstrap.get("pnl_delta_hybrid_minus_fixed", 0.0))
    ci = bootstrap.get("ci95", [None, None])
    if not additional and not replaced:
        exploratory = domain_diagnostic["mid_0p50_to_0p80_exploratory"]
        conclusion = (
            "按冻结 Core Carry 适用域，event checkpoint 没有改变任何首单，不能扩大现有策略。"
            f"但域外 mid 0.50–0.80 诊断有 {exploratory['settled']} 单、"
            f"{exploratory['wins']} 胜、ROI {100 * exploratory['roi']:.2f}%；"
            "应另建 post-cross low/mid zero-notional forward，而不是直接放宽当前 live floor。"
        )
    elif ci[0] is not None and ci[0] > 0:
        conclusion = (
            f"hybrid 比 fixed-only 多出/提前了 {len(additional) + len(replaced)} 个 city-day，"
            f"5-share PnL 差为 ${delta:.2f}，date-block 95% CI 全为正；值得进入 zero-notional forward。"
        )
    else:
        conclusion = (
            f"hybrid 改变了 {len(additional) + len(replaced)} 个 city-day，5-share PnL 差为 ${delta:.2f}，"
            "但短窗口 date-block CI 跨 0；说明机制可扩候选，尚不足以直接改 live。"
        )
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "window": [args.start_date, args.end_date],
        "artifact": {
            "version": artifact["artifact_version"],
            "hash": artifact["artifact_hash"],
            "path": str(args.artifact),
        },
        "coverage": {
            "observations": observation_counts,
            "settlements": settlement_counts,
            "candidates": candidate_counts,
            "settled_fraction_of_candidate_city_days": (
                len(set(winners).intersection(candidate_keys)) / len(candidate_keys)
                if candidate_keys
                else None
            ),
            "hybrid_additional_city_days": [list(key) for key in additional],
            "hybrid_event_selected_existing_city_days": [list(key) for key in replaced],
        },
        "policies": {name: clean_perf(value) for name, value in perfs.items()},
        "paired_date_bootstrap": bootstrap,
        "event_domain_diagnostic": domain_diagnostic,
        "conclusion": conclusion,
        "real_live_action": "none",
    }
    fixed.to_csv(GENERATED / "fixed_entries.csv", index=False)
    event.to_csv(GENERATED / "event_entries.csv", index=False)
    hybrid.to_csv(GENERATED / "hybrid_entries.csv", index=False)
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_report(payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
