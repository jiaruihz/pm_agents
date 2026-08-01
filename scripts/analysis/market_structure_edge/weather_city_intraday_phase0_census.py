#!/usr/bin/env python3
"""Freeze and validate the deployed three-city intraday runtime contract.

This is a read-only production census.  It reads an already-generated
production manifest plus raw runtime journals, writes sanitized golden
fixtures and a compact census JSON, and never fetches data or changes a
collector/strategy process.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_RUNTIME = Path("/Volumes/jrs/weather_data_feed_service_runtime")
DEFAULT_PROD = Path("/Users/deepsleep/projects/pm_agents_prod")
SCHEMA_VERSION = "weather_city_intraday_phase0_census_v1"


def parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists():
        return
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line_number, line in enumerate(handle, 1):
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                row = dict(row)
                row["_phase0_raw_line"] = line_number
                yield row


def sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _shape(value: Any) -> Any:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, list):
        variants = {_stable_json(_shape(item)) for item in value[:20]}
        return {"list": [json.loads(item) for item in sorted(variants)]}
    if isinstance(value, dict):
        return {key: _shape(value[key]) for key in sorted(value) if not key.startswith("_phase0_")}
    return type(value).__name__


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def schema_fingerprint(value: Any) -> str:
    return hashlib.sha256(_stable_json(_shape(value)).encode()).hexdigest()


def compact_record(row: dict[str, Any], fields: list[str]) -> dict[str, Any]:
    return {field: row.get(field) for field in fields}


POINT_FIELDS = [
    "schema_version", "city", "source", "source_kind", "station", "target_date",
    "observation_time_utc", "source_first_seen_at_utc", "fetched_at_utc", "temp_c",
    "temp_f", "temp_round_f", "wind_speed_kt", "pressure_hpa", "payload_hash",
    "raw_payload_hash",
]
INTERVAL_FIELDS = [
    "schema_version", "city", "source", "station", "target_date",
    "observation_time_utc", "measurement_interval_end_utc", "available_at_utc",
    "first_seen_at_utc", "temp_c", "max_temp_c_past_10m", "provider_item_id",
    "knmi_revision", "knmi_revision_kind", "event_role", "information_event_id",
    "revision_of_event_id", "material_state_change", "payload_hash", "raw_payload_hash",
]
BOOK_FIELDS = [
    "schema_version", "city", "source", "target_date", "reference_market_value",
    "relative_offset", "outcome", "book_fetched_at_utc", "book_status", "token_id",
    "condition_id",
]
OFFICIAL_FIELDS = [
    "schema_version", "city", "source", "station", "target_date", "status",
    "fetched_at_utc", "last_obs_utc", "running_max_c", "current_temp_c",
]


def fixture_envelope(kind: str, raw_path: Path, records: Any, *, note: str) -> dict[str, Any]:
    line_numbers: list[int] = []
    raw_records = records if isinstance(records, list) else [records]
    for row in raw_records:
        if isinstance(row, dict) and row.get("_phase0_raw_line"):
            line_numbers.append(int(row["_phase0_raw_line"]))
    clean_records = json.loads(_stable_json(records), object_hook=lambda obj: {
        key: value for key, value in obj.items() if not key.startswith("_phase0_")
    })
    return {
        "schema_version": "weather_city_intraday_phase0_fixture_v1",
        "fixture_kind": kind,
        "raw_ref": {
            "path": str(raw_path),
            "line_numbers": line_numbers,
            "source_file_sha256_at_census": sha256_file(raw_path),
        },
        "note": note,
        "records": clean_records,
        "record_schema_fingerprint": schema_fingerprint(clean_records),
    }


def validate_fixture(fixture: dict[str, Any]) -> list[str]:
    kind = fixture.get("fixture_kind")
    records = fixture.get("records")
    rows = records if isinstance(records, list) else [records]
    errors: list[str] = []
    if not rows or not all(isinstance(row, dict) for row in rows):
        return ["records_missing"]
    required: dict[str, set[str]] = {
        "point_observation": {"city", "source", "observation_time_utc", "source_first_seen_at_utc", "temp_c"},
        "interval_initial": {"city", "source", "measurement_interval_end_utc", "available_at_utc", "event_role", "information_event_id"},
        "interval_revision_pair": {"city", "source", "measurement_interval_end_utc", "available_at_utc", "event_role", "information_event_id"},
        "one_sided_book": {"city", "book_status", "book_fetched_at_utc", "summary"},
        "cross_day_partition": {"city", "target_date", "fetched_at_utc", "status"},
        "multi_anchor_mismatch": {"official_anchor", "source_or_book_anchor", "official", "book"},
    }
    needed = required.get(str(kind))
    if needed is None:
        return [f"unknown_fixture_kind:{kind}"]
    for index, row in enumerate(rows):
        missing = sorted(key for key in needed if row.get(key) is None)
        if missing:
            errors.append(f"row_{index}_missing:{','.join(missing)}")
    if kind == "interval_revision_pair":
        if len(rows) != 2:
            errors.append("revision_pair_requires_two_rows")
        elif rows[0].get("information_event_id") != rows[1].get("revision_of_event_id"):
            errors.append("revision_lineage_broken")
    if kind == "one_sided_book" and rows:
        summary = rows[0].get("summary") or {}
        sides = [summary.get("best_bid") is not None, summary.get("best_ask") is not None]
        if sum(sides) != 1:
            errors.append("book_is_not_one_sided")
    if kind == "cross_day_partition" and rows:
        physical_shard = fixture.get("physical_shard")
        if not physical_shard or physical_shard == rows[0].get("target_date"):
            errors.append("fixture_is_not_cross_day")
    if kind == "multi_anchor_mismatch" and rows:
        if rows[0].get("official_anchor") == rows[0].get("source_or_book_anchor"):
            errors.append("anchors_do_not_mismatch")
    return errors


def latest_point(runtime: Path, city: str, source: str, day: date, cutoff: datetime) -> tuple[Path, dict[str, Any]]:
    path = runtime / "output" / "live_cross_observations" / day.isoformat() / "high_frequency_observations.jsonl"
    rows = [
        row for row in iter_jsonl(path)
        if row.get("city") == city and row.get("source") == source
        and (parse_dt(row.get("source_first_seen_at_utc")) or datetime.max.replace(tzinfo=timezone.utc)) <= cutoff
    ]
    if not rows:
        raise RuntimeError(f"missing point sample: {city}/{source}/{path}")
    return path, max(rows, key=lambda row: parse_dt(row.get("source_first_seen_at_utc")) or datetime.min.replace(tzinfo=timezone.utc))


def latest_knmi_revision_pair(runtime: Path, cutoff: datetime) -> tuple[Path, list[dict[str, Any]]]:
    paths = sorted((runtime / "output" / "knmi_open_data").glob("*/knmi_observations.jsonl"), reverse=True)
    for path in paths:
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in iter_jsonl(path):
            available = parse_dt(row.get("available_at_utc"))
            if row.get("city") == "Amsterdam" and available and available <= cutoff:
                groups[str(row.get("observation_time_utc"))].append(row)
        for _, rows in sorted(groups.items(), reverse=True):
            initial = next((row for row in rows if row.get("event_role") == "new_content"), None)
            revisions = [row for row in rows if row.get("event_role") == "revision"]
            if initial and revisions:
                revision = min(revisions, key=lambda row: parse_dt(row.get("available_at_utc")) or cutoff)
                return path, [initial, revision]
    raise RuntimeError("missing Amsterdam KNMI revision pair")


def first_one_sided_book(runtime: Path, city: str, directory: str, day: date, cutoff: datetime) -> tuple[Path, dict[str, Any]]:
    path = runtime / "output" / directory / "active_bracket_books" / f"{day.isoformat()}.jsonl"
    for row in iter_jsonl(path):
        fetched = parse_dt(row.get("book_fetched_at_utc"))
        summary = row.get("summary") or {}
        sides = [summary.get("best_bid") is not None, summary.get("best_ask") is not None]
        if row.get("city") == city and row.get("book_status") == "ok" and sum(sides) == 1 and fetched and fetched <= cutoff:
            compact = compact_record(row, BOOK_FIELDS)
            compact["summary"] = {
                "best_bid": summary.get("best_bid"), "best_ask": summary.get("best_ask"),
                "bid_size": summary.get("bid_size"), "ask_size": summary.get("ask_size"),
                "spread": summary.get("spread"), "tick_size": summary.get("tick_size"),
            }
            compact["_phase0_raw_line"] = row.get("_phase0_raw_line")
            return path, compact
    raise RuntimeError(f"missing one-sided book: {city}")


def cross_day_sample(runtime: Path, city: str, physical_shard: date, target_date: date, cutoff: datetime) -> tuple[Path, dict[str, Any]]:
    path = runtime / "output" / "observations" / physical_shard.isoformat() / "observations.jsonl"
    for row in iter_jsonl(path):
        if row.get("city") == city and row.get("target_date") == target_date.isoformat() and row.get("status") == "awaiting_first_observation" and (parse_dt(row.get("fetched_at_utc")) or cutoff) <= cutoff:
            fields = ["schema_version", "city", "source", "station", "target_date", "timezone_name", "status", "fetched_at_utc", "local_day_elapsed_min", "first_observation_grace_min", "n_obs", "error"]
            compact = compact_record(row, fields)
            compact["_phase0_raw_line"] = row.get("_phase0_raw_line")
            return path, compact
    raise RuntimeError(f"missing cross-day sample: {city}")


def load_tokyo_official(runtime: Path, cutoff: datetime) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for shard in (date(2026, 7, 31), date(2026, 8, 1)):
        path = runtime / "output" / "observations" / shard.isoformat() / "observations.jsonl"
        rows.extend(row for row in iter_jsonl(path) if row.get("city") == "Tokyo" and row.get("status") == "ok" and (parse_dt(row.get("fetched_at_utc")) or cutoff) <= cutoff)
    return rows


def tokyo_anchor_mismatch(runtime: Path, cutoff: datetime) -> tuple[Path, dict[str, Any]]:
    official = load_tokyo_official(runtime, cutoff)
    path = runtime / "output" / "tokyo_current_break_active_ladder_shadow" / "active_bracket_books" / "2026-08-01.jsonl"
    for book in iter_jsonl(path):
        decision = parse_dt(book.get("book_fetched_at_utc"))
        if book.get("city") != "Tokyo" or book.get("source") != "jma_amedas" or book.get("outcome") != "no" or book.get("relative_offset") != 0 or book.get("book_status") != "ok" or not decision or decision > cutoff:
            continue
        known = [row for row in official if row.get("target_date") == book.get("target_date") and (parse_dt(row.get("fetched_at_utc")) or cutoff) <= decision and (parse_dt(row.get("last_obs_utc")) or cutoff) <= decision]
        if not known:
            continue
        latest = max(known, key=lambda row: (parse_dt(row.get("last_obs_utc")) or datetime.min.replace(tzinfo=timezone.utc), parse_dt(row.get("fetched_at_utc")) or datetime.min.replace(tzinfo=timezone.utc)))
        official_anchor = math.floor(float(latest["running_max_c"]) + 0.5)
        book_anchor = int(book["reference_market_value"])
        if official_anchor != book_anchor:
            compact_book = compact_record(book, BOOK_FIELDS)
            summary = book.get("summary") or {}
            compact_book["summary"] = {key: summary.get(key) for key in ("best_bid", "best_ask", "bid_size", "ask_size", "spread", "tick_size")}
            compact_book["_phase0_raw_line"] = book.get("_phase0_raw_line")
            return path, {
                "official_anchor": official_anchor,
                "source_or_book_anchor": book_anchor,
                "official": compact_record(latest, OFFICIAL_FIELDS),
                "book": compact_book,
                "_phase0_raw_line": book.get("_phase0_raw_line"),
            }
    raise RuntimeError("missing Tokyo source/official anchor mismatch")


def process_matches(manifest: dict[str, Any], needle: str) -> list[dict[str, Any]]:
    result = []
    for process in manifest.get("processes", []):
        if needle not in str(process.get("command") or ""):
            continue
        checkout = process.get("checkout") or {}
        result.append({
            "pid": process.get("pid"), "started": process.get("started"),
            "execution_mode": process.get("execution_mode"), "command": process.get("command"),
            "checkout_root": checkout.get("root"), "checkout_head": checkout.get("head"),
            "checkout_branch": checkout.get("branch"), "checkout_dirty_tracked": checkout.get("dirty_tracked"),
            "reported_loaded_repo_sha": (process.get("runtime_summary") or {}).get("deployed_repo_sha"),
        })
    return result


def file_identity(path: Path) -> dict[str, Any]:
    return {"path": str(path), "exists": path.is_file(), "sha256": sha256_file(path)}


def artifact_identities(config_path: Path, checkout: Path, city: str) -> list[dict[str, Any]]:
    if not config_path.is_file():
        return []
    config = json.loads(config_path.read_text(encoding="utf-8"))
    profile = next((item for item in config.get("profiles", []) if item.get("city") == city), None)
    if profile is None:
        return []
    result: list[dict[str, Any]] = []
    for artifact_name, declaration in sorted((profile.get("artifacts") or {}).items()):
        for path_key, declared_key in (("path", "sha256"), ("spec_path", "spec_sha256")):
            relative = declaration.get(path_key)
            if not relative:
                continue
            path = checkout / str(relative)
            actual = sha256_file(path)
            declared = declaration.get(declared_key)
            result.append({
                "artifact": artifact_name,
                "kind": "model" if path_key == "path" else "spec",
                "path": str(path),
                "exists": path.is_file(),
                "declared_sha256": declared,
                "actual_sha256": actual,
                "hash_match": bool(actual and declared and actual == declared),
            })
    return result


def runtime_output_audit(runtime: Path, cutoff: datetime) -> dict[str, Any]:
    root = runtime / "output" / "city_probability_shadow_v1"
    evaluations = [
        row for row in iter_jsonl(root / "evaluations.jsonl")
        if (parse_dt(row.get("decision_ts_utc")) or datetime.max.replace(tzinfo=timezone.utc)) <= cutoff
    ]
    errors = [
        row for row in iter_jsonl(root / "errors.jsonl")
        if (parse_dt(row.get("ts_utc")) or datetime.max.replace(tzinfo=timezone.utc)) <= cutoff
    ]
    legacy = [row for row in evaluations if "evaluation_status" not in row]
    current = [row for row in evaluations if "evaluation_status" in row]
    one_sided_errors = [row for row in errors if "two-sided quote" in str(row.get("error") or "")]
    one_sided_states = [
        row for row in current
        if row.get("not_scorable_reason") == "one_sided_market_probability_interval"
    ]

    def top_level_fingerprints(rows: list[dict[str, Any]]) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        for row in rows:
            shape = {
                key: type(value).__name__ if value is not None else "null"
                for key, value in row.items() if not key.startswith("_phase0_")
            }
            counts[hashlib.sha256(_stable_json(shape).encode()).hexdigest()] += 1
        return dict(sorted(counts.items()))

    return {
        "evaluation_rows": len(evaluations),
        "legacy_rows_missing_evaluation_status": len(legacy),
        "current_rows_with_evaluation_status": len(current),
        "legacy_top_level_schema_fingerprints": top_level_fingerprints(legacy),
        "current_top_level_schema_fingerprints": top_level_fingerprints(current),
        "one_sided_exception_poll_rows": len(one_sided_errors),
        "last_one_sided_exception_utc": max((str(row.get("ts_utc")) for row in one_sided_errors), default=None),
        "structured_one_sided_not_scorable_rows": len(one_sided_states),
        "structured_one_sided_distinct_source_observations": len({
            (row.get("city"), row.get("source_obs_ts_utc")) for row in one_sided_states
        }),
        "first_structured_evaluation_utc": min((str(row.get("decision_ts_utc")) for row in current), default=None),
        "last_error_utc": max((str(row.get("ts_utc")) for row in errors), default=None),
    }


def build_census(manifest: dict[str, Any], runtime: Path, prod: Path, fixtures: dict[str, dict[str, Any]], cutoff: datetime) -> dict[str, Any]:
    collector = process_matches(manifest, "weather_live_cross_observations_loop.py")
    probability = process_matches(manifest, "weather_city_probability_shadow_v1.py")
    helsinki_books = process_matches(manifest, "helsinki_pre_cross_active_ladder_shadow")
    tokyo_books = process_matches(manifest, "tokyo_current_break_active_ladder_shadow")
    knmi_processes = process_matches(manifest, "knmi")
    config = prod / "configs/weather/city_probability_shadow_v1.json"
    common_files = [
        prod / "scripts/ops/weather_live_cross_observations_loop.py",
        prod / "weather_data_feed_service/high_frequency_observations.py",
        prod / "scripts/ops/weather_fast_source_stale_book_observer.py",
        prod / "scripts/ops/weather_city_probability_shadow_v1.py",
        prod / "src/strategies/weather_city_probability_shadow/core.py",
        config,
    ]
    city_files = {
        "Helsinki": [prod / "src/strategies/weather_city_probability_shadow/helsinki.py"],
        "Tokyo": [prod / "src/strategies/weather_city_probability_shadow/tokyo.py"],
        "Amsterdam": [prod / "scripts/analysis/forecast_quality/research_knmi_eham_wu_alignment_v1.py"],
    }
    raw = runtime / "output"
    chains = {
        "Helsinki": {
            "payload_contract": "point_observation",
            "producer_processes": collector,
            "book_processes": helsinki_books,
            "model_consumer_processes": probability,
            "raw_owner": "weather_live_cross_observations_loop.py/fmi",
            "raw_paths": [str(raw / "live_cross_observations"), str(raw / "helsinki_pre_cross_active_ladder_shadow")],
            "code_and_config": [file_identity(path) for path in common_files + city_files["Helsinki"]],
            "model_artifacts": artifact_identities(config, prod, "Helsinki"),
            "fixture_ids": ["helsinki_point_observation", "helsinki_one_sided_book", "helsinki_cross_day_partition"],
        },
        "Tokyo": {
            "payload_contract": "point_observation+multi_anchor",
            "producer_processes": collector,
            "book_processes": tokyo_books,
            "model_consumer_processes": probability,
            "raw_owner": "weather_live_cross_observations_loop.py/jma_amedas",
            "raw_paths": [str(raw / "live_cross_observations"), str(raw / "tokyo_current_break_active_ladder_shadow")],
            "code_and_config": [file_identity(path) for path in common_files + city_files["Tokyo"]],
            "model_artifacts": artifact_identities(config, prod, "Tokyo"),
            "fixture_ids": ["tokyo_point_observation", "tokyo_one_sided_book", "tokyo_cross_day_partition", "tokyo_multi_anchor_mismatch"],
        },
        "Amsterdam": {
            "payload_contract": "interval_summary+revision",
            "producer_processes": knmi_processes,
            "book_processes": [],
            "model_consumer_processes": [],
            "raw_owner": "no active KNMI producer found in production manifest",
            "raw_paths": [str(raw / "knmi_open_data")],
            "code_and_config": [file_identity(path) for path in city_files["Amsterdam"]],
            "model_artifacts": [],
            "fixture_ids": ["amsterdam_interval_initial", "amsterdam_interval_revision_pair"],
            "liveness": "historical raw only; no current same-level probability runtime",
        },
    }
    validation = {fixture_id: validate_fixture(value) for fixture_id, value in fixtures.items()}
    blockers = [
        {"id": "P0-RUNTIME-IDENTITY", "fix_phase": "immediate_before_phase_a", "summary": "producer/consumer rows do not persist loaded module/config/artifact/schema fingerprints; manifest also reports checkout HEAD drift"},
        {"id": "P0-SCHEMA-VERSION", "fix_phase": "immediate_before_phase_a", "summary": "incompatible deployed/develop evaluation shapes share weather_city_probability_shadow_v1"},
        {"id": "P0-PARTITION-LOCATOR", "fix_phase": "immediate_before_phase_a", "summary": "target_date is still used as a physical shard locator across local/UTC day rollover"},
        {"id": "P0-ONE-SIDED", "status": "runtime_behavior_fixed_at_cutoff_schema_migration_pending", "fix_phase": "behavior_done_finish_in_immediate_schema_migration", "summary": "current zero-notional runtime preserves one-sided books as structured not_scorable rows; historical exception rows remain in the same v1 journal and require schema migration"},
        {"id": "P0-MULTI-ANCHOR", "fix_phase": "phase_a_contract", "summary": "Tokyo source/book and official anchors can legitimately differ; offset lacks an explicit anchor identity"},
        {"id": "P0-REVISION-EVENT", "fix_phase": "phase_a_contract", "summary": "Amsterdam interval/revision payload cannot share point-observation fold semantics"},
        {"id": "P0-CANONICAL-SOURCE-LINEAGE", "fix_phase": "phase_a_then_phase_b", "summary": "FMI/JMA information-event lineage is absent from canonical event coverage"},
        {"id": "P0-AMSTERDAM-LIVENESS", "fix_phase": "phase_d", "summary": "no active KNMI producer/model consumer is present; restore ownership only when Amsterdam adapter work starts"},
    ]
    artifact_checks = [
        artifact["hash_match"]
        for chain in chains.values() for artifact in chain["model_artifacts"]
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "evidence_cutoff_utc": cutoff.isoformat(),
        "scope": "read_only_no_fetch_no_process_change_no_execution",
        "production_manifest": {
            "path_supplied_externally": True,
            "generated_at_utc": manifest.get("generated_at_utc"),
            "status": manifest.get("status"),
            "db_route": manifest.get("db_route"),
            "checkouts": manifest.get("checkouts"),
            "relevant_findings": [finding for finding in manifest.get("findings", []) if finding.get("kind") == "process_checkout_head_drift"],
        },
        "chains": chains,
        "runtime_output_audit": runtime_output_audit(runtime, cutoff),
        "fixture_validation": validation,
        "fixture_schema_fingerprints": {key: value["record_schema_fingerprint"] for key, value in fixtures.items()},
        "artifact_hashes_all_match": bool(artifact_checks) and all(artifact_checks),
        "phase0_complete": all(not errors for errors in validation.values()) and bool(artifact_checks) and all(artifact_checks),
        "phase_a_implementation_gate_pass": False,
        "blockers": blockers,
        "loaded_module_identity_note": "on-disk file hashes were captured at census time; running processes do not report loaded module hashes, so exact in-memory identity remains a P0 blocker",
    }


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--production-manifest", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, default=DEFAULT_RUNTIME)
    parser.add_argument("--production-checkout", type=Path, default=DEFAULT_PROD)
    parser.add_argument("--evidence-cutoff", default="2026-08-01T05:48:16Z")
    parser.add_argument("--fixture-dir", type=Path, default=ROOT / "tests/fixtures/weather_city_intraday_phase0")
    parser.add_argument("--output", type=Path, default=ROOT / "docs/analysis/2026-08/generated/city_intraday_phase0_census_v1/phase0_census.json")
    args = parser.parse_args()
    cutoff = parse_dt(args.evidence_cutoff)
    if cutoff is None:
        raise SystemExit("invalid --evidence-cutoff")
    runtime = args.runtime_root
    fixtures: dict[str, dict[str, Any]] = {}
    for fixture_id, city, source in (
        ("helsinki_point_observation", "Helsinki", "fmi"),
        ("tokyo_point_observation", "Tokyo", "jma_amedas"),
    ):
        path, row = latest_point(runtime, city, source, date(2026, 8, 1), cutoff)
        compact = compact_record(row, POINT_FIELDS)
        compact["_phase0_raw_line"] = row.get("_phase0_raw_line")
        fixtures[fixture_id] = fixture_envelope("point_observation", path, compact, note="deployed point payload at Phase 0 cutoff")
    knmi_path, pair = latest_knmi_revision_pair(runtime, cutoff)
    compact_pair = []
    for row in pair:
        compact = compact_record(row, INTERVAL_FIELDS)
        compact["_phase0_raw_line"] = row.get("_phase0_raw_line")
        compact_pair.append(compact)
    fixtures["amsterdam_interval_initial"] = fixture_envelope("interval_initial", knmi_path, compact_pair[0], note="KNMI interval initial event")
    fixtures["amsterdam_interval_revision_pair"] = fixture_envelope("interval_revision_pair", knmi_path, compact_pair, note="same provider item, initial then material revision")
    for fixture_id, city, directory in (
        ("helsinki_one_sided_book", "Helsinki", "helsinki_pre_cross_active_ladder_shadow"),
        ("tokyo_one_sided_book", "Tokyo", "tokyo_current_break_active_ladder_shadow"),
    ):
        path, row = first_one_sided_book(runtime, city, directory, date(2026, 8, 1), cutoff)
        fixtures[fixture_id] = fixture_envelope("one_sided_book", path, row, note="valid book snapshot with exactly one quoted side")
    for fixture_id, city in (("helsinki_cross_day_partition", "Helsinki"), ("tokyo_cross_day_partition", "Tokyo")):
        path, row = cross_day_sample(runtime, city, date(2026, 7, 31), date(2026, 8, 1), cutoff)
        fixture = fixture_envelope("cross_day_partition", path, row, note="business target date differs from physical UTC/fetch shard")
        fixture["physical_shard"] = "2026-07-31"
        fixtures[fixture_id] = fixture
    path, mismatch = tokyo_anchor_mismatch(runtime, cutoff)
    fixtures["tokyo_multi_anchor_mismatch"] = fixture_envelope("multi_anchor_mismatch", path, mismatch, note="source/book bracket leads PIT official running-max bracket")
    for fixture_id, fixture in fixtures.items():
        write_json(args.fixture_dir / f"{fixture_id}.json", fixture)
    manifest = json.loads(args.production_manifest.read_text(encoding="utf-8"))
    census = build_census(manifest, runtime, args.production_checkout, fixtures, cutoff)
    write_json(args.output, census)
    print(json.dumps({
        "phase0_complete": census["phase0_complete"],
        "phase_a_implementation_gate_pass": census["phase_a_implementation_gate_pass"],
        "fixture_count": len(fixtures),
        "blocker_count": len(census["blockers"]),
        "output": str(args.output),
        "fixture_dir": str(args.fixture_dir),
    }, ensure_ascii=False, sort_keys=True))
    return 0 if census["phase0_complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
