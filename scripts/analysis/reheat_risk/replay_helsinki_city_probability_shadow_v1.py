#!/usr/bin/env python3
"""PIT replay of Helsinki city-probability shadow checkpoints.

The replay writes to a research output directory, never to the live shadow journal.
It is primarily used to recover structured one-sided-book coverage after runtime fixes.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.strategies.weather_city_probability_shadow import ShadowRuntime
from src.strategies.weather_city_probability_shadow.helsinki import HelsinkiRemainingHeatAdapter


def _rows(path: Path) -> list[dict]:
    output = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                output.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return output


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-date", default="2026-07-31")
    parser.add_argument("--config", default="configs/weather/city_probability_shadow_v1.json")
    parser.add_argument(
        "--runtime-root", default="/Volumes/jrs/weather_data_feed_service_runtime"
    )
    parser.add_argument(
        "--start-utc",
        help="Override the profile forward clock for a retrospective PIT replay.",
    )
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    config = json.loads((ROOT / args.config).read_text(encoding="utf-8"))
    profile = copy.deepcopy(next(p for p in config["profiles"] if p["city"] == "Helsinki"))
    runtime_root = Path(args.runtime_root)
    book_path = (
        runtime_root / "output/helsinki_pre_cross_active_ladder_shadow/active_bracket_books"
        / f"{args.target_date}.jsonl"
    )
    official_path = (
        runtime_root / "output/observations" / args.target_date / "observations.jsonl"
    )
    forward_start = _utc(args.start_utc or profile["forward_start_utc"])
    if args.start_utc:
        profile["forward_start_utc"] = forward_start.isoformat()

    books = [
        row for row in _rows(book_path)
        if row.get("target_date") == args.target_date
        and row.get("outcome") == "no"
        and row.get("book_status") == "ok"
        and _utc(row["book_fetched_at_utc"]) >= forward_start
    ]
    books_by_source: dict[str, list[dict]] = {}
    for row in books:
        source_ts = str(row.get("source_obs_ts_utc"))
        books_by_source.setdefault(source_ts, []).append(row)

    official_rows = [
        row for row in _rows(official_path)
        if row.get("city") == "Helsinki" and row.get("target_date") == args.target_date
    ]
    adapter = HelsinkiRemainingHeatAdapter()
    replay_config = {
        "execution_mode": "zero_notional_shadow",
        "orders_submitted": 0,
        "output_dir": str(output_dir),
        "profiles": [profile],
    }

    with tempfile.TemporaryDirectory() as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        temp_book_dir = temp_dir / "books"
        temp_book_dir.mkdir()
        temp_cache = temp_dir / "latest.json"
        profile["book_dir"] = str(temp_book_dir)
        profile["observation_cache"] = str(temp_cache)

        for source_ts, source_books in sorted(books_by_source.items()):
            selected = None
            for book in sorted(source_books, key=lambda row: _utc(row["book_fetched_at_utc"])):
                decision = _utc(book["book_fetched_at_utc"])
                official = [
                    row for row in official_rows
                    if _utc(row["fetched_at_utc"]) <= decision
                    and _utc(row["last_obs_utc"]) <= decision
                ]
                if not official:
                    continue
                selected_official = max(official, key=lambda row: _utc(row["fetched_at_utc"]))
                expected_bracket = int(round(float(selected_official["running_max_c"])))
                if str(book.get("bracket")) == str(expected_bracket):
                    selected = (book, decision, selected_official)
                    break
            if selected is None:
                raise RuntimeError(f"no PIT current-bracket book for source checkpoint {source_ts}")
            book, decision, selected_official = selected
            temp_cache.write_text(
                json.dumps({"records": [selected_official]}) + "\n", encoding="utf-8"
            )
            (temp_book_dir / f"{args.target_date}.jsonl").write_text(
                json.dumps(book) + "\n", encoding="utf-8"
            )
            ShadowRuntime(replay_config, {"helsinki_remaining_heat_v1": adapter}).run_once(
                decision
            )

    evaluations = _rows(output_dir / "evaluations.jsonl")
    errors = _rows(output_dir / "errors.jsonl") if (output_dir / "errors.jsonl").exists() else []
    checkpoint_status: dict[str, str] = {}
    for row in evaluations:
        checkpoint_status[row["source_obs_ts_utc"]] = row["market"]["quote_state"]
    summary = {
        "schema_version": "helsinki_city_probability_shadow_replay_v1",
        "target_date": args.target_date,
        "forward_start_utc": profile["forward_start_utc"],
        "raw_book_rows": len(books),
        "source_checkpoints": len(books_by_source),
        "replayed_checkpoints": len(checkpoint_status),
        "evaluation_rows": len(evaluations),
        "scored_rows": sum(r["evaluation_status"] == "scored" for r in evaluations),
        "not_scorable_rows": sum(r["evaluation_status"] == "not_scorable" for r in evaluations),
        "quote_state_checkpoints": dict(sorted(Counter(checkpoint_status.values()).items())),
        "would_enter_rows": sum(bool(r["would_enter"]) for r in evaluations),
        "paper_intents": len(_rows(output_dir / "paper_intents.jsonl"))
        if (output_dir / "paper_intents.jsonl").exists() else 0,
        "orders_submitted": sum(int(r["orders_submitted"]) for r in evaluations),
        "errors": len(errors),
        "evidence_origin": "offline_pit_replay_from_immutable_raw_journals",
    }
    (output_dir / "replay_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
