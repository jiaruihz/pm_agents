#!/usr/bin/env python3
"""Build the D-1 exact-run forecast-revision to market-repricing denominator.

This is a coverage and mechanism study.  It preserves bootstrap runs, partial
batch completions, missing ladders, and unsettled dates as evidence blockers.
It never creates a plan, order, fill, or execution recommendation.
"""

from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any, Iterable
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_data_feed.assigned_forecast_models import assigned_single_run_model_key  # noqa: E402
from weather_data_feed import load_city_configs  # noqa: E402
from weather_data_feed.forecast_run_contract import (  # noqa: E402
    materialize_full_ladder_checkpoint,
    parse_utc,
    stable_content_hash,
)


DEFAULT_CAPTURE_DIR = Path(
    "/Volumes/jrs/weather_data_feed_service_runtime/output/forecast_run_capture"
)
DEFAULT_SNAPSHOT_DIR = Path(
    "/Volumes/jrs/weather_data_feed_service_runtime/full_ladder_output/paper_snapshots"
)
DEFAULT_OUT = (
    ROOT
    / "docs/analysis/2026-08/generated/d1_forecast_revision_market_repricing"
)
DEFAULT_REPORT = (
    ROOT
    / "docs/analysis/2026-08/2026-08-05-d1-forecast-revision-market-repricing-plan-v1.md"
)
DEFAULT_DB = ROOT / "runtime/weather.db"
BOOTSTRAP_WINDOW_MINUTES = 30
MARKOUT_MINUTES = (30, 60, 90)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def material_forecast_batches(
    rows: list[dict[str, Any]],
    batches: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    rows_by_batch: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        rows_by_batch.setdefault(str(row.get("batch_capture_id") or ""), []).append(row)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for source_batch in batches:
        batch = dict(source_batch)
        batch_id = str(batch.get("batch_capture_id") or "")
        members = rows_by_batch.get(batch_id, [])
        run_timestamps = sorted(
            {str(row.get("forecast_run_at_utc")) for row in members if row.get("forecast_run_at_utc")}
        )
        forecast_run_at_utc = run_timestamps[0] if len(run_timestamps) == 1 else None
        available_values = [
            str(row.get("available_at_utc")) for row in members if row.get("available_at_utc")
        ]
        batch_available_at_utc = max(available_values) if available_values else None
        material_batch_key = stable_content_hash(
            {
                "city": batch.get("city"),
                "target_date": batch.get("target_date"),
                "forecast_run_at_utc": forecast_run_at_utc,
                "batch_content_hash": batch.get("batch_content_hash"),
            }
        )
        batch.update(
            {
                "material_batch_key": material_batch_key,
                "forecast_run_at_utc": forecast_run_at_utc,
                "batch_available_at_utc": batch_available_at_utc,
            }
        )
        grouped.setdefault(material_batch_key, []).append(batch)
    material: list[dict[str, Any]] = []
    material_members: dict[str, list[dict[str, Any]]] = {}
    for material_batch_key, deliveries in grouped.items():
        selected = min(
            deliveries,
            key=lambda item: (
                str(item.get("batch_available_at_utc") or "9999"),
                str(item.get("batch_capture_id") or ""),
            ),
        )
        selected = dict(selected)
        selected["delivery_count"] = len(deliveries)
        selected["delivery_batch_capture_ids"] = sorted(
            str(item.get("batch_capture_id") or "") for item in deliveries
        )
        material.append(selected)
        material_members[str(selected.get("batch_capture_id") or "")] = rows_by_batch.get(
            str(selected.get("batch_capture_id") or ""), []
        )
    material.sort(
        key=lambda item: (
            str(item.get("batch_available_at_utc") or ""),
            str(item.get("city") or ""),
            str(item.get("target_date") or ""),
        )
    )
    return material, material_members


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _assigned_value(batch: dict[str, Any]) -> float | None:
    existing = batch.get("assigned_model_value_f")
    if existing is not None:
        return float(existing)
    values = dict(batch.get("model_values") or {})
    assigned_key = assigned_single_run_model_key(str(batch.get("city") or ""))
    value = values.get(assigned_key)
    return float(value) if value is not None else None


def d1_checkpoint_policy(
    target_date: str,
    event_available_at_utc: str,
    timezone_name: str,
) -> tuple[str, float]:
    event_local = parse_utc(
        event_available_at_utc, field="event_available_at_utc"
    ).astimezone(ZoneInfo(timezone_name))
    target_midnight = datetime.fromisoformat(target_date).replace(
        tzinfo=ZoneInfo(timezone_name)
    )
    hours = (event_local - target_midnight).total_seconds() / 3600.0
    if -6.0 <= hours < 0.0:
        return "D-1_18_24", hours
    if -12.0 <= hours < -6.0:
        return "D-1_12_18", hours
    return "outside_frozen_d1_checkpoint", hours


def lineage_impact(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    source = [dict(row) for row in rows]
    with_previous = [row for row in source if row.get("previous_run_ts")]
    backward = [
        row
        for row in with_previous
        if str(row["previous_run_ts"]) > str(row.get("forecast_run_at_utc") or "")
    ]
    forward = [
        row
        for row in with_previous
        if str(row["previous_run_ts"]) < str(row.get("forecast_run_at_utc") or "")
    ]
    transition_keys = {
        (
            str(row.get("model_key")),
            str(row.get("city")),
            str(row.get("target_date")),
            str(row.get("previous_run_ts")),
            str(row.get("forecast_run_at_utc")),
        )
        for row in forward
    }
    return {
        "raw_forecast_rows": len(source),
        "rows_with_previous_run": len(with_previous),
        "backward_previous_run_rows": len(backward),
        "forward_previous_run_rows": len(forward),
        "unique_forward_transition_keys": len(transition_keys),
        "repeated_forward_transition_rows": len(forward) - len(transition_keys),
        "affected_window_start_utc": min(
            (str(row.get("available_at_utc")) for row in source), default=None
        ),
        "affected_window_end_utc": max(
            (str(row.get("available_at_utc")) for row in source), default=None
        ),
    }


def build_revision_events(
    rows: list[dict[str, Any]],
    batches: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    material, _ = material_forecast_batches(rows, batches)
    timezone_by_city = {
        config.city: config.timezone_name
        for config in load_city_configs(include_station_diff=False)
    }
    available = [
        parse_utc(item.get("batch_available_at_utc"), field="batch_available_at_utc")
        for item in material
        if item.get("batch_available_at_utc")
    ]
    monitor_start = min(available) if available else None
    bootstrap_end = (
        monitor_start + timedelta(minutes=BOOTSTRAP_WINDOW_MINUTES)
        if monitor_start
        else None
    )
    by_sequence: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in material:
        if not item.get("forecast_run_at_utc"):
            continue
        by_sequence.setdefault(
            (str(item.get("city") or ""), str(item.get("target_date") or "")), []
        ).append(item)

    events: list[dict[str, Any]] = []
    for (city, target_date), sequence in sorted(by_sequence.items()):
        complete = [item for item in sequence if not item.get("missing_model_keys")]
        complete.sort(
            key=lambda item: (
                str(item.get("forecast_run_at_utc")),
                str(item.get("batch_available_at_utc")),
            )
        )
        for previous, current in zip(complete, complete[1:]):
            current_available = parse_utc(
                current.get("batch_available_at_utc"), field="batch_available_at_utc"
            )
            had_earlier_partial = any(
                item.get("missing_model_keys")
                and item.get("forecast_run_at_utc") == current.get("forecast_run_at_utc")
                and str(item.get("batch_available_at_utc") or "")
                < str(current.get("batch_available_at_utc") or "")
                for item in sequence
            )
            if bootstrap_end and current_available <= bootstrap_end:
                event_class = "bootstrap_existing_run"
            elif had_earlier_partial:
                event_class = "complete_batch_after_partial"
            else:
                event_class = "forward_new_complete_run"
            previous_values = dict(previous.get("model_values") or {})
            current_values = dict(current.get("model_values") or {})
            common_models = sorted(set(previous_values) & set(current_values))
            revisions = [
                float(current_values[model]) - float(previous_values[model])
                for model in common_models
            ]
            previous_assigned = _assigned_value(previous)
            current_assigned = _assigned_value(current)
            checkpoint_policy, local_hours = d1_checkpoint_policy(
                target_date,
                str(current.get("batch_available_at_utc")),
                timezone_by_city[city],
            )
            events.append(
                {
                    "revision_event_id": stable_content_hash(
                        {
                            "city": city,
                            "target_date": target_date,
                            "previous_run": previous.get("forecast_run_at_utc"),
                            "current_run": current.get("forecast_run_at_utc"),
                            "current_batch": current.get("material_batch_key"),
                        }
                    ),
                    "city": city,
                    "target_date": target_date,
                    "horizon_days_local": 1
                    if any(
                        int(row.get("horizon_days_local") or -1) == 1
                        and row.get("batch_capture_id") == current.get("batch_capture_id")
                        for row in rows
                    )
                    else 2,
                    "previous_run_at_utc": previous.get("forecast_run_at_utc"),
                    "forecast_run_at_utc": current.get("forecast_run_at_utc"),
                    "event_available_at_utc": current.get("batch_available_at_utc"),
                    "event_class": event_class,
                    "checkpoint_policy": checkpoint_policy,
                    "local_hours_from_target_midnight": local_hours,
                    "common_model_count": len(common_models),
                    "consensus_mean_revision_f": (
                        float(current.get("mean_f")) - float(previous.get("mean_f"))
                    ),
                    "consensus_median_revision_f": (
                        float(current.get("median_f")) - float(previous.get("median_f"))
                    ),
                    "mean_absolute_model_revision_f": (
                        sum(abs(value) for value in revisions) / len(revisions)
                        if revisions
                        else None
                    ),
                    "assigned_model_revision_f": (
                        current_assigned - previous_assigned
                        if current_assigned is not None and previous_assigned is not None
                        else None
                    ),
                    "previous_batch_content_hash": previous.get("batch_content_hash"),
                    "current_batch_content_hash": current.get("batch_content_hash"),
                }
            )
    return events, {
        "raw_forecast_batches": len(batches),
        "material_forecast_batches": len(material),
        "duplicate_poll_batches_collapsed": len(batches) - len(material),
        "complete_material_batches": sum(not item.get("missing_model_keys") for item in material),
        "monitor_start_utc": _utc_text(monitor_start) if monitor_start else None,
        "bootstrap_end_utc": _utc_text(bootstrap_end) if bootstrap_end else None,
    }


def _snapshot_paths(snapshot_dir: Path, events: list[dict[str, Any]]) -> list[Path]:
    days: set[str] = set()
    for event in events:
        value = parse_utc(event["event_available_at_utc"], field="event_available_at_utc")
        for offset in (-1, 0, 1):
            days.add((value + timedelta(days=offset)).strftime("%Y%m%d"))
    return sorted(
        path
        for day in days
        for path in snapshot_dir.glob(f"snapshot_{day}_*.json")
    )


def load_market_checkpoints(
    snapshot_dir: Path,
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    target_keys = {(event["city"], event["target_date"]) for event in events}
    checkpoints: list[dict[str, Any]] = []
    for path in _snapshot_paths(snapshot_dir, events):
        payload = json.loads(path.read_text(encoding="utf-8"))
        groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for source in payload.get("records") or []:
            if not isinstance(source, dict):
                continue
            key = (str(source.get("city") or ""), str(source.get("target_date") or ""))
            if key not in target_keys:
                continue
            row = dict(source)
            row["book_status"] = row.get("yes_book_status")
            groups.setdefault(key, []).append(row)
        for (city, target_date), group in groups.items():
            checkpoint_ts = str(payload.get("ts_utc") or group[0].get("snapshot_ts_utc") or "")
            available_at = str(
                payload.get("available_at_utc")
                or max(str(row.get("available_at_utc") or "") for row in group)
            )
            feature_book_snapshot_id = stable_content_hash(
                {"path": path.name, "city": city, "target_date": target_date}
            )
            checkpoint = materialize_full_ladder_checkpoint(
                group,
                city=city,
                target_date=target_date,
                event_id=path.name,
                checkpoint_ts_utc=checkpoint_ts,
                feature_book_snapshot_id=feature_book_snapshot_id,
                horizon_days=1,
            )
            checkpoint.update(
                {
                    "available_at_utc": available_at,
                    "source_path": str(path),
                    "probabilities": {
                        str(row["label"]): row["normalized_market_probability"]
                        for row in checkpoint["rung_manifest"]
                    },
                }
            )
            checkpoints.append(checkpoint)
    checkpoints.sort(key=lambda item: str(item.get("checkpoint_ts_utc") or ""))
    return checkpoints


def _probability_markout(
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> dict[str, Any]:
    if not before or not after:
        return {"status": "missing_checkpoint", "total_variation": None, "mean_rung_shift": None}
    if not before.get("market_distribution_complete") or not after.get("market_distribution_complete"):
        return {"status": "incomplete_market_distribution", "total_variation": None, "mean_rung_shift": None}
    left = dict(before.get("probabilities") or {})
    right = dict(after.get("probabilities") or {})
    if list(left) != list(right):
        return {"status": "ladder_changed", "total_variation": None, "mean_rung_shift": None}
    labels = list(left)
    total_variation = 0.5 * sum(abs(float(right[key]) - float(left[key])) for key in labels)
    mean_before = sum(index * float(left[key]) for index, key in enumerate(labels))
    mean_after = sum(index * float(right[key]) for index, key in enumerate(labels))
    return {
        "status": "scoreable",
        "total_variation": total_variation,
        "mean_rung_shift": mean_after - mean_before,
    }


def attach_market_evidence(
    events: list[dict[str, Any]],
    checkpoints: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for source in events:
        event = dict(source)
        event_time = parse_utc(event["event_available_at_utc"], field="event_available_at_utc")
        eligible = [
            item
            for item in checkpoints
            if item.get("city") == event["city"] and item.get("target_date") == event["target_date"]
        ]
        pre_candidates = [
            item
            for item in eligible
            if parse_utc(item.get("available_at_utc"), field="available_at_utc") <= event_time
        ]
        post_candidates = [
            item
            for item in eligible
            if event_time
            <= parse_utc(item.get("checkpoint_ts_utc"), field="checkpoint_ts_utc")
            <= parse_utc(item.get("available_at_utc"), field="available_at_utc")
        ]
        pre = max(pre_candidates, key=lambda item: item["checkpoint_ts_utc"]) if pre_candidates else None
        post = min(post_candidates, key=lambda item: item["checkpoint_ts_utc"]) if post_candidates else None
        event["pre_book_snapshot_id"] = pre.get("feature_book_snapshot_id") if pre else None
        event["post_book_snapshot_id"] = post.get("feature_book_snapshot_id") if post else None
        event["post_book_delay_minutes"] = (
            (
                parse_utc(post["checkpoint_ts_utc"], field="checkpoint_ts_utc") - event_time
            ).total_seconds()
            / 60.0
            if post
            else None
        )
        immediate = _probability_markout(pre, post)
        event["immediate_market_status"] = immediate["status"]
        event["immediate_total_variation"] = immediate["total_variation"]
        event["immediate_mean_rung_shift"] = immediate["mean_rung_shift"]
        for minutes in MARKOUT_MINUTES:
            target = event_time + timedelta(minutes=minutes)
            later = [
                item
                for item in eligible
                if target
                <= parse_utc(item.get("checkpoint_ts_utc"), field="checkpoint_ts_utc")
                <= parse_utc(item.get("available_at_utc"), field="available_at_utc")
            ]
            mark = min(later, key=lambda item: item["checkpoint_ts_utc"]) if later else None
            values = _probability_markout(post, mark)
            event[f"markout_{minutes}m_status"] = values["status"]
            event[f"markout_{minutes}m_total_variation"] = values["total_variation"]
            event[f"markout_{minutes}m_mean_rung_shift"] = values["mean_rung_shift"]
        result.append(event)
    return result


def load_settled_city_dates(
    path: Path,
    target_keys: set[tuple[str, str]],
) -> set[tuple[str, str]]:
    if not target_keys:
        return set()
    target_dates = sorted({target_date for _, target_date in target_keys})
    placeholders = ",".join("?" for _ in target_dates)
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2.0)
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA busy_timeout=2000")
    try:
        rows = connection.execute(
            f"""
            SELECT city, target_date
            FROM settlement_outcomes
            WHERE settlement_status = 'settled'
              AND target_date IN ({placeholders})
            GROUP BY city, target_date
            HAVING SUM(CASE WHEN final_price >= 0.99 THEN 1 ELSE 0 END) = 1
            """,
            target_dates,
        ).fetchall()
    finally:
        connection.close()
    return {(str(city), str(target_date)) for city, target_date in rows}


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("\n", encoding="utf-8")
        return
    fields = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def render_report(summary: dict[str, Any]) -> str:
    signal = summary["signal_funnel"]
    evidence = summary["evidence_funnel"]
    impact = summary["lineage_impact"]
    classes = summary["event_classes"]
    return "\n".join(
        [
            "# D-1 forecast revision × market repricing 研究计划与首轮审计 v1",
            "",
            "weather-only:",
            "significance=not_run_no_settlement_complete_forward_dates",
            "calibration=frozen_challenger_unchanged",
            "pooled_baseline=retained_negative_control",
            "forward=collector_accumulating",
            "",
            "market residual:",
            "baseline=same-event complete normalized full ladder",
            "forward=coverage_only_no_eligible_complete_forward_revision_yet",
            "execution=not_run_no_probability_gate",
            "",
            "production:",
            "live_action=none",
            "orders_changed=0",
            "",
            "## 数据快照",
            "",
            f"- forecast rows={impact['raw_forecast_rows']}，raw batches={signal['raw_forecast_batches']}，material batches={signal['material_forecast_batches']}。",
            f"- collector observed window={impact['affected_window_start_utc']}..{impact['affected_window_end_utc']}。",
            f"- market checkpoints={evidence['market_checkpoints']}；complete={evidence['complete_market_checkpoints']}。",
            f"- settlement-complete revision events={signal['settlement_complete_events']}；其余保持 unsettled coverage，missing_bracket=0。",
            "",
            "## 先修的 lineage 根因",
            "",
            f"旧 state 产生 backward previous-run rows={impact['backward_previous_run_rows']}；另外 forward transition delivery rows={impact['forward_previous_run_rows']}，折叠后 unique transition keys={impact['unique_forward_transition_keys']}，重复={impact['repeated_forward_transition_rows']}。",
            "根因是每轮从旧 run 重新请求，而 state 只保存最后轮询 run。修复后按 model×city×target×run 保存历史，旧 run 重抓只比较同 run content，不再引用未来 run；原始 payload/run/value 不删除，污染仅限派生 revision lineage。",
            "",
            "## 固定研究问题",
            "",
            "在 D-1 complete exact-run material event 的 first available clock 上，比较事件前最后一份完整 ladder、事件后第一份完整 ladder和 30/60/90m markout；先检验 revision 是否带来方向一致的 market repricing，再在结算后比较 frozen weather posterior 与 M0 market proper score。",
            "",
            "静态 forecast level、revision event 和 market residual 分三层：weather-only challenger 不读取市场；revision 只做连续 feature；M2/M3 只在同 rows、同 labels、同 feature-book 时钟下与 M0 比。",
            "正式 weather score 的主 checkpoint 固定为当地 target 前一日 18:00–24:00 的首个 complete batch（`D-1_18_24`）；12:00–18:00 只作 secondary。revision markout 可保留全部 D-1 events，但不得替代主 checkpoint proper score。",
            "",
            "## 首轮双漏斗",
            "",
            "| signal funnel | count |",
            "|---|---:|",
            *[f"| {key} | {value} |" for key, value in signal.items()],
            "",
            "| evidence funnel | count |",
            "|---|---:|",
            *[f"| {key} | {value} |" for key, value in evidence.items()],
            "",
            f"event classes：`{json.dumps(classes, ensure_ascii=False, sort_keys=True)}`。只有 `forward_new_complete_run` 可进入正式 revision forward；bootstrap 与 partial→complete 继续保留，但不混入 alpha 分母。",
            "",
            "## 下一阶段与冻结规则",
            "",
            "1. collector 修复部署后，从全新 state schema 开始积累 chronological revision；不重写旧 JSONL。",
            "2. 先累计 complete D-1 run events、完整 pre/post ladders与 settlement；模型和 beta 在新 forward 期间不调。",
            "3. weather-only 继续使用已冻结 challenger，新增 revision/spread/lead-age 只能在 inner train 建下一 challenger。",
            "4. market head 固定 M0/M1/M2/M3；M2/M3 必须在 target-date block bootstrap 的 logloss/RPS/calibration 上优于 M0，才进入 ask/fee/depth EV。",
            "5. 当前不做 ROI、maker、selected price band、城市名单或 live 动作。",
            "",
            "## 8 环与结论",
            "",
            "本轮覆盖 lineage、signal/evidence coverage、market checkpoint contract；统计推断因 0 个 eligible settled forward events 未启动，执行、容量、fills、组合相关性均未覆盖。",
            "",
            "结论：`inconclusive / collector-and-lineage-repair`。研究已经启动，但旧 revision 字段不能使用；修复代码需经生产确认后部署，随后才开始干净 forward clock。",
            "",
        ]
    )


def run_study(
    *,
    capture_dir: Path = DEFAULT_CAPTURE_DIR,
    snapshot_dir: Path = DEFAULT_SNAPSHOT_DIR,
    db: Path = DEFAULT_DB,
    output_dir: Path = DEFAULT_OUT,
    report: Path = DEFAULT_REPORT,
) -> dict[str, Any]:
    rows = read_jsonl(capture_dir / "forecast_run_rows.jsonl")
    batches = read_jsonl(capture_dir / "forecast_batches.jsonl")
    blockers = read_jsonl(capture_dir / "blockers.jsonl")
    events, material_summary = build_revision_events(rows, batches)
    checkpoints = load_market_checkpoints(snapshot_dir, events)
    scored_events = attach_market_evidence(events, checkpoints)
    d1_events = [event for event in scored_events if event["horizon_days_local"] == 1]
    settled_city_dates = load_settled_city_dates(
        db,
        {(event["city"], event["target_date"]) for event in d1_events},
    )
    for event in d1_events:
        event["settlement_complete"] = (
            event["city"], event["target_date"]
        ) in settled_city_dates
    event_classes = Counter(str(event["event_class"]) for event in d1_events)
    signal_funnel = {
        **material_summary,
        "d1_complete_run_transition_events": len(d1_events),
        "primary_d1_18_24_transition_events": sum(
            event["checkpoint_policy"] == "D-1_18_24" for event in d1_events
        ),
        "forward_new_complete_run_events": event_classes["forward_new_complete_run"],
        "settlement_complete_events": sum(event["settlement_complete"] for event in d1_events),
        "probability_scoreable_events": 0,
    }
    evidence_funnel = {
        "market_checkpoints": len(checkpoints),
        "complete_market_checkpoints": sum(
            item.get("rung_completeness") and item.get("market_distribution_complete")
            for item in checkpoints
        ),
        "revision_events_with_pre_book": sum(bool(event.get("pre_book_snapshot_id")) for event in d1_events),
        "revision_events_with_post_book": sum(bool(event.get("post_book_snapshot_id")) for event in d1_events),
        "revision_events_immediate_scoreable": sum(
            event.get("immediate_market_status") == "scoreable" for event in d1_events
        ),
        **{
            f"revision_events_{minutes}m_markout_scoreable": sum(
                event.get(f"markout_{minutes}m_status") == "scoreable" for event in d1_events
            )
            for minutes in MARKOUT_MINUTES
        },
        "executable": 0,
        "actual_fills": 0,
    }
    summary = {
        "schema_version": "d1_forecast_revision_market_repricing_research_v1",
        "generated_at_utc": _utc_text(datetime.now(timezone.utc)),
        "lineage_impact": lineage_impact(rows),
        "signal_funnel": signal_funnel,
        "evidence_funnel": evidence_funnel,
        "event_classes": dict(sorted(event_classes.items())),
        "blocker_rows": len(blockers),
        "conclusion": "inconclusive_collector_and_lineage_repair",
        "production": {"live_action": "none", "orders_changed": 0},
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(output_dir / "revision_events.csv", d1_events)
    write_csv(output_dir / "market_checkpoints.csv", checkpoints)
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    report.write_text(render_report(summary), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture-dir", type=Path, default=DEFAULT_CAPTURE_DIR)
    parser.add_argument("--snapshot-dir", type=Path, default=DEFAULT_SNAPSHOT_DIR)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args(argv)
    run_study(
        capture_dir=args.capture_dir,
        snapshot_dir=args.snapshot_dir,
        db=args.db,
        output_dir=args.output_dir,
        report=args.report,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
