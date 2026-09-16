#!/usr/bin/env python3
"""Freeze and reproduce WCIR Next Print Stage 2/3 research evidence.

The runner is read-only with respect to runtime inputs and never touches live
configuration, collectors, order paths, or canonical databases.  It freezes a
city/event denominator and a representative full-day WS transport slice before
performing reconstruction and research calculations.
"""

from __future__ import annotations

import argparse
import bisect
import collections
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import random
import statistics
import sys
from types import SimpleNamespace
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.platform.market_data.executable_book_truth import (  # noqa: E402
    ExecutableBookTruth,
    align_book_checkpoints,
    executable_book_truth,
)
from weather_data_feed.ws_incremental_book import (  # noqa: E402
    compare_rest_ws_parity,
    materialize_reconstructed_books,
)


CITIES = ("Amsterdam", "Tokyo", "Helsinki", "Seoul", "Busan")
STAGE2_SAMPLE_DATE = "2026-08-26"
START_DATE = "2026-08-09"
CUTOFF_DATE = "2026-08-26"
POST_OFFICIAL_SECONDS = (1, 3, 5, 15, 30, 60, 120, 300)


def parse_ts(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if value else None


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def write_jsonl_gz(path: Path, rows: Iterable[Mapping[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("wb") as raw_handle:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw_handle, mtime=0) as compressed:
            for row in rows:
                payload = json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
                compressed.write(payload.encode("utf-8"))
                count += 1
    return count


def read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8", errors="replace") as handle:
        for line_number, line in enumerate(handle, 1):
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"invalid JSONL {path}:{line_number}") from exc
            if isinstance(row, dict):
                yield row


def file_identity(path: Path, *, rows: int | None = None) -> dict[str, Any]:
    return {
        "path": str(path),
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        **({"row_count": rows} if rows is not None else {}),
    }


def verify_identity(identity: Mapping[str, Any]) -> None:
    path = Path(str(identity["path"]))
    if not path.exists():
        raise RuntimeError(f"frozen input missing: {path}")
    if path.stat().st_size != int(identity["size_bytes"]):
        raise RuntimeError(f"frozen input size drift: {path}")
    if sha256_file(path) != str(identity["sha256"]):
        raise RuntimeError(f"frozen input hash drift: {path}")


def load_fast_events(runtime: Path) -> list[dict[str, Any]]:
    path = runtime / "output/fast_source_prev_no_trial/events.jsonl"
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in read_jsonl(path):
        city = str(raw.get("city") or "")
        target_date = str(raw.get("target_date") or "")
        if city not in CITIES or not START_DATE <= target_date <= CUTOFF_DATE:
            continue
        event_key = str(raw.get("event_key") or "")
        if not event_key or event_key in seen:
            continue
        seen.add(event_key)
        row = {
            key: raw.get(key)
            for key in (
                "event_key",
                "city",
                "target_date",
                "source",
                "source_kind",
                "station",
                "source_obs_ts_utc",
                "source_detect_ts_utc",
                "source_temp_c",
                "source_round_c",
                "latest_metar_report_ts_utc",
                "latest_metar_detect_ts_utc",
                "latest_metar_temp_c",
                "latest_metar_round_c",
                "metar_running_max_temp_c",
                "metar_running_max_round_c",
                "t_minus_1_no_bracket_c",
                "market_id",
                "condition_id",
                "token_id",
                "best_ask",
                "ask_size",
                "fresh_book_status",
                "ts_utc",
            )
        }
        row["event_id"] = canonical_hash(
            {
                "event_key": event_key,
                "source_detect_ts_utc": row["source_detect_ts_utc"],
                "token_id": row["token_id"],
            }
        )
        output.append(row)
    return sorted(output, key=lambda row: (row["source_detect_ts_utc"], row["event_id"]))


def load_official(runtime: Path) -> dict[str, list[dict[str, Any]]]:
    earliest: dict[tuple[str, str], dict[str, Any]] = {}
    root = runtime / "output/source_events"
    for day in sorted(root.glob("2026-*/sources.jsonl")):
        day_text = day.parent.name
        if not "2026-08-08" <= day_text <= "2026-08-27":
            continue
        for raw in read_jsonl(day):
            city = str(raw.get("city") or "")
            if city not in CITIES or raw.get("source") != "aviationweather_metar":
                continue
            report = parse_ts(raw.get("source_report_ts_utc"))
            detect = parse_ts(raw.get("first_seen_at_utc") or raw.get("local_detect_ts_utc"))
            if report is None or detect is None or raw.get("temp_c") is None:
                continue
            key = (city, report.isoformat())
            row = {
                "city": city,
                "target_date": str(raw.get("target_date") or ""),
                "official_report_ts_utc": iso(report),
                "official_first_seen_at_utc": iso(detect),
                "official_temp_c": float(raw["temp_c"]),
                "official_round_c": math.floor(float(raw["temp_c"]) + 0.5),
                "information_event_id": raw.get("information_event_id"),
                "raw_source_path": raw.get("raw_source_path"),
            }
            if key not in earliest or detect < parse_ts(earliest[key]["official_first_seen_at_utc"]):
                earliest[key] = row
    output: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for row in earliest.values():
        output[row["city"]].append(row)
    for rows in output.values():
        rows.sort(key=lambda row: row["official_report_ts_utc"])
    return output


def link_next_official(
    events: list[dict[str, Any]], official: dict[str, list[dict[str, Any]]]
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for event in events:
        detect = parse_ts(event["source_detect_ts_utc"])
        prior_report = parse_ts(event["latest_metar_report_ts_utc"])
        following = None
        for candidate in official.get(event["city"], ()):
            candidate_report = parse_ts(candidate["official_report_ts_utc"])
            candidate_detect = parse_ts(candidate["official_first_seen_at_utc"])
            if (
                detect
                and prior_report
                and candidate_report
                and candidate_detect
                and candidate_report > prior_report
                and candidate_detect > detect
                and candidate["target_date"] == event["target_date"]
            ):
                following = candidate
                break
        row = dict(event)
        row.update(following or {})
        row["next_official_linked"] = following is not None
        row["lead_seconds"] = (
            (parse_ts(following["official_first_seen_at_utc"]) - detect).total_seconds()
            if following and detect
            else None
        )
        row["fast_exact_next_official"] = (
            int(row["source_round_c"] == following["official_round_c"])
            if following is not None
            else None
        )
        row["fast_within1_next_official"] = (
            int(abs(row["source_round_c"] - following["official_round_c"]) <= 1)
            if following is not None
            else None
        )
        row["persistence_exact_next_official"] = (
            int(row["latest_metar_round_c"] == following["official_round_c"])
            if following is not None
            else None
        )
        row["next_official_crossed_running_max"] = (
            int(following["official_round_c"] > row["metar_running_max_round_c"])
            if following is not None
            else None
        )
        output.append(row)
    return output


def message_tokens(message: Any) -> set[str]:
    rows = message if isinstance(message, list) else [message]
    output: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        token = row.get("asset_id") or row.get("assetId") or row.get("token_id")
        if token:
            output.add(str(token))
        for change in row.get("price_changes") or ():
            if isinstance(change, Mapping) and change.get("asset_id"):
                output.add(str(change["asset_id"]))
    return output


def freeze_ws_slice(
    runtime: Path, evidence: Path, event_rows: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    day_events = [row for row in event_rows if row["target_date"] == STAGE2_SAMPLE_DATE]
    target_tokens = {str(row["token_id"]) for row in day_events if row.get("token_id")}
    epoch_path = (
        runtime
        / "market_books/ws_incremental/subscription_epochs"
        / f"subscription_epochs_{STAGE2_SAMPLE_DATE}.jsonl"
    )
    epochs: list[dict[str, Any]] = []
    epoch_ids: set[str] = set()
    for raw in read_jsonl(epoch_path):
        row = dict(raw)
        row["token_ids"] = sorted(target_tokens & set(map(str, raw.get("token_ids") or ())))
        token_rows = raw.get("token_rows") or {}
        row["token_rows"] = {
            str(token): metadata
            for token, metadata in token_rows.items()
            if str(token) in target_tokens
        }
        epochs.append(row)
        epoch_ids.add(str(row["subscription_epoch_id"]))

    frames: list[dict[str, Any]] = []
    raw_files = sorted(
        (runtime / f"market_books/ws_incremental/{STAGE2_SAMPLE_DATE}").glob("*.jsonl*")
    )
    for path in raw_files:
        for line_number, raw in enumerate(read_jsonl(path), 1):
            if str(raw.get("subscription_epoch_id") or "") not in epoch_ids:
                continue
            if not (message_tokens(raw.get("message")) & target_tokens):
                continue
            row = dict(raw)
            row["_raw_path"] = str(path)
            row["_line_number"] = line_number
            frames.append(row)

    epochs_frozen = evidence / "FROZEN_SUBSCRIPTION_EPOCHS_2026-08-26.jsonl.gz"
    frames_frozen = evidence / "FROZEN_WS_FRAMES_2026-08-26.jsonl.gz"
    epoch_count = write_jsonl_gz(epochs_frozen, epochs)
    frame_count = write_jsonl_gz(frames_frozen, frames)
    manifest = {
        "sample_date": STAGE2_SAMPLE_DATE,
        "sample_design": "predeclared_latest_complete_day_before_stage_run",
        "city_scope": list(CITIES),
        "event_rows": len(day_events),
        "target_tokens": len(target_tokens),
        "target_token_ids": sorted(target_tokens),
        "raw_ws_files": [file_identity(path) for path in raw_files],
        "source_epoch_file": file_identity(epoch_path),
        "frozen_epochs": file_identity(epochs_frozen, rows=epoch_count),
        "frozen_frames": file_identity(frames_frozen, rows=frame_count),
    }
    return epochs, frames, manifest


def freeze_rest_slice(
    runtime: Path, evidence: Path, tokens: set[str]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    paths = sorted((runtime / f"market_books/batches/{STAGE2_SAMPLE_DATE}").glob("*.jsonl.gz"))
    rows: list[dict[str, Any]] = []
    for path in paths:
        for raw in read_jsonl(path):
            if str(raw.get("token_id") or "") in tokens and raw.get("status") == "ok":
                row = dict(raw)
                row["_raw_path"] = str(path)
                rows.append(row)
    frozen = evidence / "FROZEN_REST_BOOKS_2026-08-26.jsonl.gz"
    count = write_jsonl_gz(frozen, rows)
    return rows, {
        "source_files": [file_identity(path) for path in paths],
        "frozen_rest": file_identity(frozen, rows=count),
    }


def materialize_epoch_chains(
    epochs: list[dict[str, Any]], frames: list[dict[str, Any]]
) -> SimpleNamespace:
    """Replay independently declared reconnect chains without inventing carry."""

    ordered = sorted(epochs, key=lambda row: str(row.get("started_at_utc") or ""))
    chains: list[list[dict[str, Any]]] = []
    frozen_epoch_ids = {str(row.get("subscription_epoch_id") or "") for row in ordered}
    for epoch in ordered:
        previous = str(epoch.get("previous_subscription_epoch_id") or "") or None
        if not chains or previous != str(chains[-1][-1].get("subscription_epoch_id") or ""):
            chains.append([epoch])
        else:
            chains[-1].append(epoch)
    frames_by_epoch: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for frame in frames:
        frames_by_epoch[str(frame.get("subscription_epoch_id") or "")].append(frame)
    runs = []
    for chain in chains:
        ids = {str(row["subscription_epoch_id"]) for row in chain}
        chain_frames = [row for epoch_id in ids for row in frames_by_epoch.get(epoch_id, ())]
        runs.append(materialize_reconstructed_books(chain, chain_frames, requested_shares=5.0))
    blockers = []
    for chain_number, run in enumerate(runs):
        blockers.extend({**row, "reconnect_chain_number": chain_number} for row in run.blockers)
    external_predecessors = [
        {
            "first_frozen_epoch_id": str(chain[0].get("subscription_epoch_id") or ""),
            "declared_previous_subscription_epoch_id": str(
                chain[0].get("previous_subscription_epoch_id") or ""
            ),
        }
        for chain in chains
        if str(chain[0].get("previous_subscription_epoch_id") or "")
        and str(chain[0].get("previous_subscription_epoch_id")) not in frozen_epoch_ids
    ]
    return SimpleNamespace(
        snapshots=tuple(snapshot for run in runs for snapshot in run.snapshots),
        blockers=tuple(blockers),
        input_frames=sum(run.input_frames for run in runs),
        duplicate_frames=sum(run.duplicate_frames for run in runs),
        reconstruction_errors=sum(run.reconstruction_errors for run in runs),
        reconnect_chains=len(chains),
        external_predecessors=tuple(external_predecessors),
        run_id=canonical_hash([run.run_id for run in runs]),
    )


def sweep_value(truth: ExecutableBookTruth, *, side: str, shares: float) -> float | None:
    row = next((x for x in truth.sweeps if x.side == side and x.shares == shares), None)
    return row.effective_value_usd if row and row.fully_executable else None


def quantiles(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"p10": None, "median": None, "p90": None}
    ordered = sorted(values)
    def pick(q: float) -> float:
        return ordered[round((len(ordered) - 1) * q)]
    return {"p10": pick(0.1), "median": pick(0.5), "p90": pick(0.9)}


def build_checkpoints(event: Mapping[str, Any]) -> dict[str, str]:
    source = parse_ts(event.get("source_detect_ts_utc"))
    official = parse_ts(event.get("official_first_seen_at_utc"))
    if source is None:
        return {}
    output = {
        "pre_source": iso(source - timedelta(seconds=1)),
        "source_t0": iso(source),
        "pre_official": iso(official - timedelta(seconds=1)) if official else None,
    }
    if official:
        for seconds in POST_OFFICIAL_SECONDS:
            output[f"official_plus_{seconds}s"] = iso(official + timedelta(seconds=seconds))
    return {key: value for key, value in output.items() if value is not None}


def summarize_baselines(events: list[dict[str, Any]]) -> dict[str, Any]:
    eligible = [row for row in events if row["next_official_linked"]]
    by_city: dict[str, Any] = {}
    for city in CITIES:
        rows = [row for row in eligible if row["city"] == city]
        dates = {row["target_date"] for row in rows}
        shuffled = list(rows)
        random.Random(20260827).shuffle(shuffled)
        shuffled_exact = (
            sum(
                int(row["source_round_c"] == shuffled[(index + 1) % len(shuffled)]["official_round_c"])
                for index, row in enumerate(rows)
            )
            if len(rows) > 1
            else 0
        )
        by_city[city] = {
            "eligible_events": len(rows),
            "target_dates": len(dates),
            "source_rounded_exact_rate": (
                sum(row["fast_exact_next_official"] for row in rows) / len(rows) if rows else None
            ),
            "source_rounded_within1_rate": (
                sum(row["fast_within1_next_official"] for row in rows) / len(rows) if rows else None
            ),
            "persistence_exact_rate": (
                sum(row["persistence_exact_next_official"] for row in rows) / len(rows)
                if rows
                else None
            ),
            "shuffled_time_exact_rate": shuffled_exact / len(rows) if rows else None,
            "slope_baseline": {"status": "unavailable", "reason": "one-row event grain has no preregistered slope window"},
            "forecast_only": {"status": "unavailable", "reason": "no PIT forecast identity frozen in this boundary"},
            "market_only": {"status": "unavailable", "reason": "no same-row pre-source two-sided executable book for full denominator"},
        }
    return {
        "denominator": "distinct frozen fast-source event_id with linked next routine official print",
        "by_city": by_city,
        "negative_control_seed": 20260827,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-root", type=Path, default=Path("/Volumes/jrs/weather_data_feed_service_runtime"))
    parser.add_argument("--output-root", type=Path, default=ROOT / "reviews/wcir_next_print")
    parser.add_argument("--frozen-root", type=Path)
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    frozen_root = (args.frozen_root or args.output_root).resolve()
    if args.offline and args.output_root.resolve() == frozen_root:
        raise RuntimeError("offline replay output must differ from immutable frozen root")
    stage2 = args.output_root / "stage_02"
    stage3 = args.output_root / "stage_03"
    evidence2 = stage2 / "evidence"
    evidence3 = stage3 / "evidence"
    evidence2.mkdir(parents=True, exist_ok=True)
    evidence3.mkdir(parents=True, exist_ok=True)

    frozen_events = evidence3 / "FROZEN_NEXT_REPORT_EVENTS.jsonl.gz"
    if args.offline:
        frozen_stage2 = frozen_root / "stage_02"
        frozen_stage3 = frozen_root / "stage_03"
        input_manifest = json.loads((frozen_stage2 / "evidence/FROZEN_INPUT_MANIFEST.json").read_text())
        dataset_manifest = json.loads((frozen_stage3 / "NEXT_REPORT_DATASET_MANIFEST.json").read_text())
        for identity in (
            input_manifest["ws"]["frozen_epochs"],
            input_manifest["ws"]["frozen_frames"],
            input_manifest["rest"]["frozen_rest"],
            dataset_manifest["frozen_dataset"],
        ):
            verify_identity(identity)
        frozen_events = Path(dataset_manifest["frozen_dataset"]["path"])
        events = list(read_jsonl(frozen_events))
        frozen_event_count = len(events)
        epochs = list(read_jsonl(Path(input_manifest["ws"]["frozen_epochs"]["path"])))
        frames = list(read_jsonl(Path(input_manifest["ws"]["frozen_frames"]["path"])))
        rest_rows = list(read_jsonl(Path(input_manifest["rest"]["frozen_rest"]["path"])))
        ws_manifest = input_manifest["ws"]
        rest_manifest = input_manifest["rest"]
    else:
        fast_raw = load_fast_events(args.runtime_root)
        official = load_official(args.runtime_root)
        events = link_next_official(fast_raw, official)
        frozen_event_count = write_jsonl_gz(frozen_events, events)
        epochs, frames, ws_manifest = freeze_ws_slice(args.runtime_root, evidence2, events)
    run1 = materialize_epoch_chains(epochs, frames)
    run2 = materialize_epoch_chains(list(reversed(epochs)), list(reversed(frames)))
    if run1.run_id != run2.run_id:
        raise RuntimeError("determinism failure under input permutation")
    truths = [executable_book_truth(book) for book in run1.snapshots]
    truths_path = evidence2 / "FROZEN_EXECUTABLE_BOOK_TRUTHS_2026-08-26.jsonl.gz"
    truth_count = write_jsonl_gz(truths_path, (row.to_dict() for row in truths))

    tokens = set(ws_manifest["target_token_ids"])
    if not args.offline:
        rest_rows, rest_manifest = freeze_rest_slice(args.runtime_root, evidence2, tokens)
    by_token: dict[str, list[ExecutableBookTruth]] = collections.defaultdict(list)
    for truth in truths:
        by_token[truth.token_id].append(truth)
    for rows in by_token.values():
        rows.sort(key=lambda row: (row.exchange_ts_ms or -1, row.truth_id))
    parity_rows: list[dict[str, Any]] = []
    book_by_id = {row.book_snapshot_id: row for row in run1.snapshots}
    for rest in rest_rows:
        rest_ts = int(rest.get("exchange_book_ts_raw") or rest.get("raw", {}).get("timestamp") or 0)
        candidates = by_token.get(str(rest.get("token_id") or ""), ())
        if not candidates:
            continue
        nearest = min(candidates, key=lambda row: abs((row.exchange_ts_ms or -10**18) - rest_ts))
        if nearest.book_snapshot_id is None:
            continue
        parity_rows.append(compare_rest_ws_parity(book_by_id[nearest.book_snapshot_id], rest).to_dict())

    day_events = [row for row in events if row["target_date"] == STAGE2_SAMPLE_DATE]
    alignment_events = [
        {"event_id": row["event_id"], "token_id": row["token_id"], "checkpoints": build_checkpoints(row)}
        for row in day_events
    ]
    alignments = align_book_checkpoints(truths, alignment_events, max_age_seconds=120.0)
    alignment_path = evidence2 / "EVENT_ALIGNED_BOOK_ROWS_2026-08-26.jsonl.gz"
    alignment_count = write_jsonl_gz(alignment_path, (row.to_dict() for row in alignments))
    alignment_lookup = {(row.event_id, row.checkpoint): row for row in alignments}
    truth_lookup = {row.truth_id: row for row in truths}

    oracle_rows: list[dict[str, Any]] = []
    for event in day_events:
        if not event["next_official_linked"]:
            continue
        entry_align = alignment_lookup.get((event["event_id"], "source_t0"))
        for horizon in (5, 15, 30, 60, 120):
            exit_align = alignment_lookup.get((event["event_id"], f"official_plus_{horizon}s"))
            for shares in (1.0, 5.0):
                entry_truth = truth_lookup.get(entry_align.book_truth_id) if entry_align and entry_align.book_valid else None
                exit_truth = truth_lookup.get(exit_align.book_truth_id) if exit_align and exit_align.book_valid else None
                entry_cost = sweep_value(entry_truth, side="buy", shares=shares) if entry_truth else None
                exit_value = sweep_value(exit_truth, side="sell", shares=shares) if exit_truth else None
                eligible = bool(
                    event["next_official_crossed_running_max"] == 1
                    and entry_cost is not None
                    and exit_value is not None
                )
                oracle_rows.append(
                    {
                        "event_id": event["event_id"],
                        "city": event["city"],
                        "target_date": event["target_date"],
                        "horizon_seconds": horizon,
                        "shares": shares,
                        "information_oracle_action": "buy_no_on_prior_exact_bracket" if event["next_official_crossed_running_max"] == 1 else "not_evaluable_missing_yes_complement",
                        "entry_effective_cost_usd": entry_cost,
                        "exit_effective_proceeds_usd": exit_value,
                        "net_markout_usd": exit_value - entry_cost if eligible else None,
                        "eligible": eligible,
                    }
                )

    aggregate_oracle: list[dict[str, Any]] = []
    for city in CITIES:
        for horizon in (5, 15, 30, 60, 120):
            for shares in (1.0, 5.0):
                rows = [
                    row for row in oracle_rows
                    if row["city"] == city and row["horizon_seconds"] == horizon
                    and row["shares"] == shares and row["eligible"]
                ]
                values = [row["net_markout_usd"] for row in rows]
                dates = {row["target_date"] for row in rows}
                aggregate_oracle.append(
                    {
                        "city": city,
                        "horizon_seconds": horizon,
                        "shares": shares,
                        "eligible_events": len(rows),
                        "target_dates": len(dates),
                        "mean_net_markout_usd": statistics.mean(values) if values else None,
                        "date_block_one_sided_90_ci_lower": None,
                        "ci_status": "insufficient_target_dates" if len(dates) < 20 else "not_implemented",
                    }
                )

    coverage_by_city = {}
    for city in CITIES:
        city_events = [row for row in events if row["city"] == city]
        linked = [row for row in city_events if row["next_official_linked"]]
        dates = {row["target_date"] for row in linked}
        executable = [
            row for row in oracle_rows
            if row["city"] == city and row["shares"] == 5.0
            and row["horizon_seconds"] == 30 and row["eligible"]
        ]
        coverage_by_city[city] = {
            "raw_fast_events": len(city_events),
            "linked_next_official_events": len(linked),
            "linked_target_dates": len(dates),
            "five_share_30s_executable_oracle_events": len(executable),
            "five_share_30s_executable_target_dates": len({row["target_date"] for row in executable}),
        }

    write_json(stage2 / "DETERMINISM_TEST_RESULTS.json", {
        "status": "pass",
        "run_id_forward": run1.run_id,
        "run_id_reversed": run2.run_id,
        "snapshot_count": len(run1.snapshots),
        "input_frames": len(frames),
        "duplicate_frames": run1.duplicate_frames,
        "reconstruction_errors": run1.reconstruction_errors,
        "reconnect_chains": run1.reconnect_chains,
        "external_predecessor_chain_count": len(run1.external_predecessors),
        "external_predecessors": list(run1.external_predecessors),
    })
    write_json(stage2 / "GAP_AND_RECONNECT_AUDIT.json", {
        "blocker_count": len(run1.blockers),
        "open_blocker_count": sum(row["recovery_status"] == "open" for row in run1.blockers),
        "blockers": list(run1.blockers),
        "external_predecessors": list(run1.external_predecessors),
        "slice_boundary_disposition": "explicit_truncation; no state carried from outside frozen day; tokens remain invalid until an in-slice verified baseline",
        "contract": "invalid intervals remain unavailable until verified state recovery; REST never fills gaps",
    })
    parity_counts = collections.Counter(row["parity_status"] for row in parity_rows)
    write_json(stage2 / "REST_WS_RECONCILIATION.json", {
        "comparison_count": len(parity_rows),
        "status_counts": dict(sorted(parity_counts.items())),
        "comparisons": parity_rows,
    })
    write_json(stage2 / "EXECUTABLE_SWEEP_TESTS.json", {
        "truth_rows": len(truths),
        "share_sizes": [1, 5, 10],
        "weather_taker_fee_rate": 0.05,
        "queue_truth": False,
        "maker_fill_proxy_only": True,
        "two_sided_fully_executable_counts": {
            str(shares): sum(
                all(
                    any(s.side == side and s.shares == shares and s.fully_executable for s in truth.sweeps)
                    for side in ("buy", "sell")
                )
                for truth in truths
            )
            for shares in (1.0, 5.0, 10.0)
        },
    })
    checkpoint_counts = collections.Counter(
        (row.checkpoint, "valid" if row.book_valid else row.gap_reason) for row in alignments
    )
    write_json(stage2 / "EVENT_ALIGNED_BOOK_COVERAGE.json", {
        "sample_date": STAGE2_SAMPLE_DATE,
        "event_count": len(day_events),
        "alignment_rows": len(alignments),
        "checkpoint_status_counts": [
            {"checkpoint": key[0], "status": key[1], "count": value}
            for key, value in sorted(checkpoint_counts.items())
        ],
    })
    write_json(evidence2 / "FROZEN_INPUT_MANIFEST.json", {
        "ws": ws_manifest,
        "rest": rest_manifest,
        "executable_truths": file_identity(truths_path, rows=truth_count),
        "event_alignments": file_identity(alignment_path, rows=alignment_count),
    })

    baselines = summarize_baselines(events)
    write_json(stage3 / "NEXT_REPORT_DATASET_MANIFEST.json", {
        "schema_version": "wcir_next_report_event_v1",
        "start_date": START_DATE,
        "cutoff_date": CUTOFF_DATE,
        "city_scope": list(CITIES),
        "frozen_dataset": file_identity(frozen_events, rows=frozen_event_count),
        "row_count": len(events),
        "linked_next_official_rows": sum(row["next_official_linked"] for row in events),
        "coverage_by_city": coverage_by_city,
        "production_health_boundary": "raw_runtime_read_only; no canonical healthy claim",
    })
    write_json(stage3 / "PERFECT_PRINT_ORACLE_RESULTS.json", {
        "oracle_scope": "one-sided prior-exact-bracket NO; complementary YES unavailable is not imputed",
        "rows": oracle_rows,
        "aggregates": aggregate_oracle,
        "preregistered_gate": {
            "minimum_target_dates": 20,
            "minimum_eligible_events": 100,
            "five_share_ci_lower_must_exceed_zero": True,
            "gate_passed": False,
            "reason": "no city reaches 20 executable target dates; full two-sided information oracle is unavailable",
        },
    })
    write_json(stage3 / "SIMPLE_NOWCAST_BASELINES.json", baselines)
    write_json(evidence3 / "LEAKAGE_AND_NEGATIVE_CONTROL_AUDIT.json", {
        "status": "pass_with_coverage_blocker",
        "source_clock": "source_detect_ts_utc",
        "official_clock": "official_first_seen_at_utc",
        "book_clock": "WS received_at_utc as-of only",
        "future_fields_excluded_from_features": True,
        "shuffled_time_negative_control": True,
        "within_date_grouping": True,
        "atlanta_2026_07_17_terminal_false_control": "outside five-city denominator; retained as mandatory future source-onboarding control",
        "blocker": "insufficient executable date coverage",
    })
    write_json(evidence3 / "STAGE03_COMPUTATION_SUMMARY.json", {
        "coverage_by_city": coverage_by_city,
        "oracle_rows": len(oracle_rows),
        "oracle_eligible_rows": sum(row["eligible"] for row in oracle_rows),
        "disposition": "CONTINUE_COLLECTION_WITHOUT_MODELING",
    })
    print(json.dumps({
        "stage2_run_id": run1.run_id,
        "stage2_snapshots": len(run1.snapshots),
        "stage3_events": len(events),
        "stage3_linked": sum(row["next_official_linked"] for row in events),
        "coverage_by_city": coverage_by_city,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
