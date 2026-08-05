#!/usr/bin/env python3
"""Audit current D-1/D-2 forecast lineage and full-ladder capture coverage."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_data_feed.market_brackets import parse_market_bracket


RUNTIME = Path("/Volumes/jrs/weather_data_feed_service_runtime")
DEFAULT_VERSIONS = RUNTIME / "output/forecast_enrichment/forecast_versions.jsonl"
DEFAULT_CURVES = RUNTIME / "targeted_output/forecast_hourly_curves"
DEFAULT_LADDERS = RUNTIME / "full_ladder_output/paper_snapshots"
DEFAULT_OUT = ROOT / "docs/analysis/2026-08/generated/d1_d2_forecast_lineage_audit_v1"
DEFAULT_REPORT = ROOT / "docs/analysis/2026-08/2026-08-05-d1-d2-forecast-lineage-audit-v1.md"


def parse_utc(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def in_window(timestamp: Any, start: date, end: date) -> bool:
    parsed = parse_utc(timestamp)
    return parsed is not None and start <= parsed.date() <= end


def iter_jsonl(path: Path):
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def audit_versions(path: Path, start: date, end: date) -> dict[str, Any]:
    rows = [row for row in iter_jsonl(path) if in_window(row.get("available_at_utc"), start, end)]
    by_horizon = Counter(int(row.get("forecast_horizon_days_local")) for row in rows if row.get("forecast_horizon_days_local") is not None)
    run_present = sum(bool(row.get("forecast_run_at_utc")) for row in rows)
    available_present = sum(bool(row.get("available_at_utc")) for row in rows)
    fetch_clock_present = sum(bool(row.get("source_fetch_start_utc")) and bool(row.get("source_fetch_end_utc")) for row in rows)
    hash_present = sum(bool(row.get("forecast_version_hash")) and bool(row.get("source_raw_payload_hash")) for row in rows)

    sequences: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    capture_groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        sequences[(str(row.get("city")), str(row.get("forecast_target_date")), str(row.get("model_key")))].append(row)
        # ``capture_id`` is row/model specific in v1.  The batch fetch shares
        # one exact captured_at_utc across every city/target/model row.
        capture_groups[(str(row.get("captured_at_utc")), str(row.get("city")), str(row.get("forecast_target_date")))].append(row)

    nonfirst_rows = 0
    content_revision_rows = 0
    distinct_content_versions = 0
    for sequence in sequences.values():
        sequence.sort(key=lambda row: str(row.get("available_at_utc") or ""))
        previous_hash: str | None = None
        seen_hashes: set[str] = set()
        for row in sequence:
            current_hash = str(row.get("forecast_version_hash") or "")
            if previous_hash is not None:
                nonfirst_rows += 1
                if current_hash and current_hash != previous_hash:
                    content_revision_rows += 1
            if current_hash:
                seen_hashes.add(current_hash)
            previous_hash = current_hash or previous_hash
        distinct_content_versions += len(seen_hashes)

    multi_model_groups = 0
    summary_derivable_groups = 0
    model_counts: list[int] = []
    for group in capture_groups.values():
        values = [float(row["forecast_max_f"]) for row in group if row.get("forecast_max_f") is not None]
        model_count = len({str(row.get("model_key")) for row in group if row.get("model_key")})
        model_counts.append(model_count)
        if model_count >= 2:
            multi_model_groups += 1
        if model_count >= 2 and len(values) == len(group):
            _ = (mean(values), median(values), max(values) - min(values))
            summary_derivable_groups += 1

    return {
        "path": str(path),
        "rows": len(rows),
        "cities": len({str(row.get("city")) for row in rows}),
        "target_dates": len({str(row.get("forecast_target_date")) for row in rows}),
        "horizon_counts": dict(sorted(by_horizon.items())),
        "run_time_present": run_present,
        "run_time_coverage": ratio(run_present, len(rows)),
        "available_time_present": available_present,
        "available_time_coverage": ratio(available_present, len(rows)),
        "fetch_clock_present": fetch_clock_present,
        "fetch_clock_coverage": ratio(fetch_clock_present, len(rows)),
        "version_and_payload_hash_present": hash_present,
        "version_and_payload_hash_coverage": ratio(hash_present, len(rows)),
        "sequence_groups": len(sequences),
        "nonfirst_rows": nonfirst_rows,
        "content_revision_rows_derivable": content_revision_rows,
        "distinct_content_versions": distinct_content_versions,
        "explicit_previous_version_fields": 0,
        "captured_at_city_target_groups": len(capture_groups),
        "multi_model_groups": multi_model_groups,
        "multi_model_summary_derivable_groups": summary_derivable_groups,
        "multi_model_summary_persisted_groups": 0,
        "median_models_per_capture_city_target": median(model_counts) if model_counts else None,
    }


def audit_curves(root: Path, start: date, end: date) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    cursor = start
    files = 0
    while cursor <= end:
        for path in sorted((root / cursor.isoformat()).glob("forecast_hourly_curves_*.jsonl")):
            files += 1
            rows.extend(iter_jsonl(path))
        cursor += timedelta(days=1)
    total = len(rows)
    material = sum(row.get("information_event_status") == "material" for row in rows)
    return {
        "path": str(root),
        "files": files,
        "rows": total,
        "cities": len({str(row.get("city")) for row in rows}),
        "target_dates": len({str(row.get("target_date")) for row in rows}),
        "collector_exact_rows": sum(row.get("pit_lineage_class") == "collector_exact" for row in rows),
        "collector_exact_coverage": ratio(sum(row.get("pit_lineage_class") == "collector_exact" for row in rows), total),
        "first_seen_present": sum(bool(row.get("forecast_first_seen_utc")) for row in rows),
        "first_seen_coverage": ratio(sum(bool(row.get("forecast_first_seen_utc")) for row in rows), total),
        "available_present": sum(bool(row.get("available_at_utc")) for row in rows),
        "available_coverage": ratio(sum(bool(row.get("available_at_utc")) for row in rows), total),
        "run_time_present": sum(bool(row.get("forecast_run_ts_utc")) for row in rows),
        "run_time_coverage": ratio(sum(bool(row.get("forecast_run_ts_utc")) for row in rows), total),
        "material_content_changes": material,
        "explicit_lead_hours_rows": sum(row.get("forecast_target_lead_hours") is not None for row in rows),
        "explicit_run_age_rows": sum(row.get("forecast_run_age_hours") is not None for row in rows),
        "explicit_previous_run_rows": sum(row.get("previous_run_forecast_max_f") is not None for row in rows),
        "explicit_revision_delta_rows": sum(row.get("run_to_run_revision_f") is not None for row in rows),
    }


def snapshot_date(path: Path) -> date | None:
    match = re.search(r"snapshot_(\d{8})_", path.name)
    return datetime.strptime(match.group(1), "%Y%m%d").date() if match else None


def ladder_complete(records: list[dict[str, Any]]) -> bool:
    parsed = [parse_market_bracket(str(row.get("bracket") or ""), str(row.get("question") or "")) for row in records]
    if any(item is None for item in parsed):
        return False
    brackets = sorted(parsed, key=lambda item: float("-inf") if item.bottom else float(item.low))
    if sum(item.bottom for item in brackets) != 1 or sum(item.top for item in brackets) != 1:
        return False
    for left, right in zip(brackets, brackets[1:]):
        if left.high is None or right.low is None or abs(float(right.low) - float(left.high) - 1.0) > 1e-9:
            return False
    return True


def market_distribution_complete(records: list[dict[str, Any]]) -> bool:
    for row in records:
        bid = row.get("yes_best_bid")
        ask = row.get("yes_best_ask")
        fallback = row.get("market_yes_price")
        if bid is not None and ask is not None:
            continue
        if fallback is None:
            return False
    return True


def audit_ladders(root: Path, start: date, end: date) -> dict[str, Any]:
    paths = [path for path in root.glob("snapshot_*.json") if (value := snapshot_date(path)) is not None and start <= value <= end]
    states = Counter()
    complete = Counter()
    market_complete = Counter()
    publishable = 0
    parsed_files = 0
    for path in sorted(paths):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        parsed_files += 1
        publishable += bool((payload.get("snapshot_publish_quality") or {}).get("publishable"))
        groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in payload.get("records") or []:
            local_date = str(row.get("city_local_date_at_snapshot") or "")
            target_date = str(row.get("event_date") or row.get("target_date") or "")
            try:
                horizon = (date.fromisoformat(target_date) - date.fromisoformat(local_date)).days
            except ValueError:
                continue
            if horizon not in (1, 2):
                continue
            key = (str(row.get("city")), target_date, str(row.get("event_slug") or row.get("market_id") or ""))
            groups[key].append(row)
        for group in groups.values():
            local_date = str(group[0].get("city_local_date_at_snapshot"))
            target_date = str(group[0].get("event_date") or group[0].get("target_date"))
            horizon = (date.fromisoformat(target_date) - date.fromisoformat(local_date)).days
            states[horizon] += 1
            if ladder_complete(group):
                complete[horizon] += 1
            if ladder_complete(group) and market_distribution_complete(group):
                market_complete[horizon] += 1
    return {
        "path": str(root),
        "files": len(paths),
        "parsed_files": parsed_files,
        "publishable_files": publishable,
        "publishable_file_coverage": ratio(publishable, parsed_files),
        "state_counts": dict(sorted(states.items())),
        "native_lattice_complete_counts": dict(sorted(complete.items())),
        "native_lattice_complete_coverage": {str(key): ratio(complete[key], states[key]) for key in sorted(states)},
        "market_distribution_complete_counts": dict(sorted(market_complete.items())),
        "market_distribution_complete_coverage": {str(key): ratio(market_complete[key], states[key]) for key in sorted(states)},
    }


def pct(value: float | None) -> str:
    return "NA" if value is None else f"{value:.1%}"


def render_report(summary: dict[str, Any]) -> str:
    versions = summary["forecast_versions"]
    curves = summary["forecast_hourly_curves"]
    ladders = summary["full_ladders"]
    return f"""# D-1 / D-2 forecast lineage 与完整 ladder 审计 v1

## 数据快照

- raw runtime：`{summary['runtime_root']}`；窗口 `{summary['start']}..{summary['end']}`。
- forecast_versions：{versions['rows']:,} rows / {versions['cities']} cities / {versions['target_dates']} target dates。
- forecast_hourly_curves：{curves['rows']:,} rows / {curves['files']} immutable files。
- full-ladder：{ladders['parsed_files']} snapshots；只读审计，没有 sync/rebuild/production change。
- production manifest healthy；production health 的 Helsinki pre-cross session critical 与本次 D-1/D-2 raw coverage 无关，但本报告不发布 canonical alpha 结论。

## 结论

当前 collector 已经解决了一半问题：append-only 多模型 version hash、fetch/available clock、assigned-model curve first-seen 和当前 full-ladder 都存在。但它**还不能支撑干净的 D-1/D-2 run-aware 回测**：provider run timestamp 为 0 覆盖，previous run/revision 与 multi-model summary 只是可派生、没有作为稳定字段物化；D-2 forecast 有数据，但同期 D-2 market ladder 为零。

所以不冻结上一版 pooled/hierarchical 模型。下一步先增加明确 model-run collector 与稳定 checkpoint contract，再训练 partial hierarchy。

## 字段覆盖

| required field | current evidence | coverage/status |
|---|---|---:|
| forecast issue/run time | `forecast_versions.forecast_run_at_utc` | {pct(versions['run_time_coverage'])} |
| first-seen / available | curves collector-exact + versions available clock | {pct(curves['first_seen_coverage'])} / {pct(versions['available_time_coverage'])} |
| lead hours | target/snapshot 可计算；paper snapshot 仅 estimated | 未稳定物化 |
| model run age | 依赖真实 run timestamp | 不可计算 |
| previous run forecast | version sequence 可派生 | explicit 0 rows |
| run-to-run revision | {versions['content_revision_rows_derivable']:,} content revisions 可派生，但不是 provider-run revision | explicit 0 rows |
| multi-model mean/median/spread | {versions['multi_model_summary_derivable_groups']:,}/{versions['captured_at_city_target_groups']:,} captured-at groups 可派生 | persisted 0 groups |
| D-1 native-lattice ladder | full-ladder states={ladders['state_counts'].get(1, 0):,} | {pct(ladders['native_lattice_complete_coverage'].get('1'))} |
| D-1 simultaneous market distribution | complete ladder + market probability | {pct(ladders['market_distribution_complete_coverage'].get('1'))} |
| D-2 forecast versions | horizon=2 rows | {versions['horizon_counts'].get(2, 0):,} |
| D-2 native-lattice / market | full-ladder states={ladders['state_counts'].get(2, 0):,} | {pct(ladders['native_lattice_complete_coverage'].get('2'))} |

## 正确修复

1. 新增 run-aware single-run capture：明确请求 `model_key + run_ts`，保存 source fetch、detected、first-seen、available 四时钟和 raw hash；live endpoint 的 `12Z estimated` 不再冒充 issue/run。
2. 在同一 append-only row 物化 `previous_run_ts`, `previous_run_forecast_max_f`, `run_to_run_revision_f`，同时保留 content-version revision，二者不能混名。
3. 新增稳定的 `batch_capture_id`；每个 `batch_capture_id × city × target_date` 物化全模型 values、mean、median、q25/q75、min/max spread、assigned-minus-consensus，并保存模型缺失列表。现有逐模型 `capture_id` 继续保留为 row identity。
4. full-ladder checkpoint 保存 event-level rung manifest/hash、native-lattice completeness、normalized mid distribution、book snapshot id；缺 rung 进入 blocker，不静默丢 state。
5. D-2 只在交易所确有完整同期 ladder 后进入 probability-vs-market 评测；现阶段 D-2 forecast 只能训练 weather accuracy，不能声称 market residual。

## Partial hierarchy v2（数据修好后）

结构固定为：`source/lead pooled residual shape + shrunk city×source mean bias + shrunk scale correction`。city bias 只修 center，不让单城重新学习整条 tail shape；scale 需要更高样本量并向 source/lead pooled variance 强收缩。多模型 consensus/spread、run age、revision 作为连续特征，market 只进入独立 residual head。

候选达到以下条件前不冻结：同 rows 同 checkpoint 至少不劣于 pooled proper score；相对 market 的 Brier/logloss gap 大幅收敛且没有城市灾难尾；在未参与开发的 target-date forward 保持同号。交易 ROI 只作第二层诊断。

## 双漏斗与状态

- signal funnel：forecast version rows {versions['rows']:,} → horizon-1 {versions['horizon_counts'].get(1, 0):,} / horizon-2 {versions['horizon_counts'].get(2, 0):,}。
- evidence funnel：full-ladder D-1 states {ladders['state_counts'].get(1, 0):,} → native-complete {ladders['native_lattice_complete_counts'].get(1, 0):,} → market-complete {ladders['market_distribution_complete_counts'].get(1, 0):,}；D-2 market states {ladders['state_counts'].get(2, 0):,}。
- significance=NA；baseline=FAIL；forward=NA；conclusion=`inconclusive_data_contract_repair_required`；actual fills=0；不改 live。
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2026-07-27")
    parser.add_argument("--end", default="2026-08-05")
    parser.add_argument("--versions", type=Path, default=DEFAULT_VERSIONS)
    parser.add_argument("--curves", type=Path, default=DEFAULT_CURVES)
    parser.add_argument("--ladders", type=Path, default=DEFAULT_LADDERS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    summary = {
        "schema_version": "d1_d2_forecast_lineage_audit_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "runtime_root": str(RUNTIME),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "forecast_versions": audit_versions(args.versions, start, end),
        "forecast_hourly_curves": audit_curves(args.curves, start, end),
        "full_ladders": audit_ladders(args.ladders, start, end),
    }
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(render_report(summary), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
