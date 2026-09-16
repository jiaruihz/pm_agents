#!/usr/bin/env python3
"""Attribute recent CrossNO accuracy across signals, fills, policy, and weather regime.

The event journal is the first-seen signal evidence for this legacy strategy.  It
is intentionally reported as raw event-grain evidence, not as canonical
``fact_signal_candidates``.  Settlements and actual fill performance come from
the canonical DB.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sqlite3
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DB = ROOT / "runtime/weather.db"
DEFAULT_EVENTS = Path(
    "/Volumes/jrs/weather_data_feed_service_runtime/output/fast_source_prev_no_trial/events.jsonl"
)
DEFAULT_OUTPUT = (
    ROOT
    / "docs/analysis/2026-08/generated/cross_no_accuracy_attribution_20260812_v1"
)
STRATEGY_ID = "live_weather_edge_v1_c16645cc1165"

CITY_TIMEZONES = {
    "Amsterdam": "Europe/Amsterdam",
    "Ankara": "Europe/Istanbul",
    "Atlanta": "America/New_York",
    "Busan": "Asia/Seoul",
    "Helsinki": "Europe/Helsinki",
    "Istanbul": "Europe/Istanbul",
    "Miami": "America/New_York",
    "SanFrancisco": "America/Los_Angeles",
    "Seoul": "Asia/Seoul",
    "Singapore": "Asia/Singapore",
    "TelAviv": "Asia/Jerusalem",
    "Tokyo": "Asia/Tokyo",
}

WINDOWS = {
    "launch_legacy_era": ("2026-07-09", "2026-07-14"),
    "hardening_transition_era": ("2026-07-15", "2026-07-22"),
    "prior_10d": ("2026-07-23", "2026-08-01"),
    "recent_10d": ("2026-08-02", "2026-08-11"),
    "preceding_5d": ("2026-08-02", "2026-08-06"),
    "latest_5d": ("2026-08-07", "2026-08-11"),
    "all_before_latest_5d": ("2026-07-09", "2026-08-06"),
}


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def number(value: Any) -> float | None:
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    size = path.stat().st_size
    with path.open("rb") as handle:
        while handle.tell() < size:
            raw = handle.readline()
            if not raw:
                break
            try:
                row = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if isinstance(row, dict):
                yield row


def event_expression_key(row: dict[str, Any]) -> tuple[str, ...] | None:
    condition_id = str(row.get("condition_id") or "").strip()
    city = str(row.get("city") or "").strip()
    target_date = str(row.get("target_date") or "").strip()
    bracket = row.get("t_minus_1_no_market_bracket")
    if bracket in (None, ""):
        bracket = row.get("t_minus_1_no_bracket_c")
    if bracket in (None, ""):
        return None
    bracket_text = str(bracket)
    if condition_id:
        return ("condition", condition_id)
    if city and target_date:
        return ("fallback", city, target_date, bracket_text)
    return None


def dedupe_event_rows(
    rows: Iterable[dict[str, Any]], start_date: str, end_date: str
) -> list[dict[str, Any]]:
    first: dict[tuple[str, ...], dict[str, Any]] = {}
    for raw in rows:
        if raw.get("status") != "cross_candidate":
            continue
        target_date = str(raw.get("target_date") or "")
        if not (start_date <= target_date <= end_date):
            continue
        key = event_expression_key(raw)
        detected = parse_dt(raw.get("source_detect_ts_utc") or raw.get("ts_utc"))
        if key is None or detected is None:
            continue
        row = dict(raw)
        row["_expression_key"] = "|".join(key)
        row["_detected_at_utc"] = detected.isoformat()
        previous = number(
            raw.get("t_minus_1_no_bracket_c")
            if raw.get("t_minus_1_no_bracket_c") not in (None, "")
            else raw.get("t_minus_1_no_bracket")
        )
        source_temp = number(raw.get("source_market_temp"))
        row["_cross_margin_native"] = (
            source_temp - previous
            if source_temp is not None and previous is not None
            else None
        )
        zone_name = CITY_TIMEZONES.get(str(raw.get("city") or ""))
        row["_local_hour"] = (
            detected.astimezone(ZoneInfo(zone_name)).hour
            + detected.astimezone(ZoneInfo(zone_name)).minute / 60.0
            if zone_name
            else None
        )
        if key not in first or row["_detected_at_utc"] < first[key]["_detected_at_utc"]:
            first[key] = row
    return sorted(
        first.values(),
        key=lambda row: (str(row.get("target_date")), row["_detected_at_utc"], row["_expression_key"]),
    )


def connect_ro(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=3.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=3000")
    conn.row_factory = sqlite3.Row
    return conn


def chunks(values: list[str], size: int = 500) -> Iterable[list[str]]:
    for offset in range(0, len(values), size):
        yield values[offset : offset + size]


def normalize_bracket(value: Any) -> str:
    text = str(value or "").strip()
    try:
        parsed = float(text)
    except ValueError:
        return text
    return str(int(parsed)) if parsed.is_integer() else str(parsed)


def load_settlements(
    conn: sqlite3.Connection,
    events: list[dict[str, Any]],
    start_date: str,
    end_date: str,
) -> tuple[dict[str, dict[str, Any]], dict[tuple[str, str, str], dict[str, Any]]]:
    ids = sorted({str(row.get("condition_id") or "") for row in events if row.get("condition_id")})
    by_condition: dict[str, dict[str, Any]] = {}
    for batch in chunks(ids):
        placeholders = ",".join("?" for _ in batch)
        sql = f"""
            SELECT condition_id, city, target_date, bracket, final_price,
                   settlement_status, created_at_utc, source_payload_hash
            FROM settlement_outcomes
            WHERE source_system='pm_history'
              AND condition_id IN ({placeholders})
        """
        for row in conn.execute(sql, batch):
            condition_id = str(row["condition_id"] or "")
            if not condition_id:
                continue
            candidate = dict(row)
            existing = by_condition.get(condition_id)
            if existing is None or str(candidate["created_at_utc"]) > str(existing["created_at_utc"]):
                by_condition[condition_id] = candidate
    by_city_date_bracket: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in conn.execute(
        """
        SELECT condition_id, city, target_date, bracket, final_price,
               settlement_status, created_at_utc, source_payload_hash
        FROM settlement_outcomes
        WHERE source_system='pm_history' AND target_date BETWEEN ? AND ?
        """,
        (start_date, end_date),
    ):
        candidate = dict(row)
        key = (
            str(row["city"] or ""),
            str(row["target_date"] or ""),
            normalize_bracket(row["bracket"]),
        )
        existing = by_city_date_bracket.get(key)
        if existing is None or str(candidate["created_at_utc"]) > str(existing["created_at_utc"]):
            by_city_date_bracket[key] = candidate
    return by_condition, by_city_date_bracket


def load_cross_fills(
    conn: sqlite3.Connection, start_date: str, end_date: str
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT target_date, city, bracket, condition_id,
               MAX(final_yes) AS final_yes,
               MAX(CASE WHEN side='BUY_NO' THEN 1 ELSE 0 END) AS has_buy,
               SUM(CASE WHEN side='BUY_NO' THEN cost_usd ELSE 0 END) AS buy_cost_usd,
               SUM(CASE WHEN side='BUY_NO' THEN fees_usd ELSE 0 END) AS buy_fees_usd,
               SUM(pnl_usd_at_fill) AS net_pnl_usd,
               SUM(CASE WHEN side='BUY_NO' THEN fill_qty ELSE 0 END) AS buy_fill_qty,
               COUNT(*) AS fact_rows,
               SUM(CASE WHEN instance_id IS NULL THEN 1 ELSE 0 END) AS instance_null_rows,
               MIN(order_ts_utc) AS first_order_ts_utc
        FROM fact_trades
        WHERE strategy_id=? AND trade_class='live_real'
          AND settlement_status='settled'
          AND target_date BETWEEN ? AND ?
        GROUP BY target_date, city, bracket, condition_id
        HAVING has_buy=1
        ORDER BY target_date, city, bracket
        """,
        (STRATEGY_ID, start_date, end_date),
    ).fetchall()
    return [
        {
            **dict(row),
            "correct": int(float(row["final_yes"]) < 0.001),
        }
        for row in rows
    ]


def attach_settlements_and_fills(
    events: list[dict[str, Any]],
    settlements_by_condition: dict[str, dict[str, Any]],
    settlements_by_city_date_bracket: dict[tuple[str, str, str], dict[str, Any]],
    fills: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    fills_by_condition = {
        str(row.get("condition_id") or ""): row for row in fills if row.get("condition_id")
    }
    output: list[dict[str, Any]] = []
    for raw in events:
        condition_id = str(raw.get("condition_id") or "")
        previous_bracket = str(
            raw.get("t_minus_1_no_market_bracket")
            or raw.get("t_minus_1_no_bracket_c")
            or ""
        )
        settlement = settlements_by_condition.get(condition_id)
        if settlement is None:
            settlement = settlements_by_city_date_bracket.get(
                (
                    str(raw.get("city") or ""),
                    str(raw.get("target_date") or ""),
                    normalize_bracket(previous_bracket),
                )
            )
        fill = fills_by_condition.get(condition_id)
        row = {
            "expression_key": raw["_expression_key"],
            "event_key": str(raw.get("event_key") or ""),
            "condition_id": condition_id,
            "city": str(raw.get("city") or ""),
            "target_date": str(raw.get("target_date") or ""),
            "source": str(raw.get("source") or ""),
            "mode": str(raw.get("mode") or ""),
            "schema_version": str(raw.get("schema_version") or ""),
            "policy": str(raw.get("source_cross_policy") or "legacy_unlabeled"),
            "previous_bracket": previous_bracket,
            "detected_at_utc": raw["_detected_at_utc"],
            "local_hour": raw["_local_hour"],
            "cross_margin_native": raw["_cross_margin_native"],
            "required_distinct_observations": number(
                raw.get("source_cross_required_distinct_observations")
            ),
            "qualifying_distinct_observations": number(
                raw.get("source_cross_qualifying_distinct_observations")
            ),
            "strong_observation_seen": raw.get("source_cross_strong_observation_seen"),
            "source_age_min": number(raw.get("source_age_min")),
            "source_obs_lag_min": number(raw.get("source_obs_lag_min")),
            "source_detect_to_runner_sec": number(raw.get("source_detect_to_runner_sec")),
            "fresh_book_status": str(raw.get("fresh_book_status") or ""),
            "best_ask": number(raw.get("best_ask")),
            "ask_size": number(raw.get("ask_size")),
            "max_no_ask": number(raw.get("max_no_ask")),
            "live_requested": bool(raw.get("live_requested")),
            "settled": int(settlement is not None and settlement.get("settlement_status") == "settled"),
            "final_yes": number(settlement.get("final_price")) if settlement else None,
            "correct": (
                int(float(settlement["final_price"]) < 0.001)
                if settlement and settlement.get("settlement_status") == "settled"
                else None
            ),
            "actual_fill": int(fill is not None),
            "fill_buy_cost_usd": number(fill.get("buy_cost_usd")) if fill else None,
            "fill_net_pnl_usd": number(fill.get("net_pnl_usd")) if fill else None,
        }
        row["book_present"] = int(row["best_ask"] is not None)
        row["price_eligible"] = int(
            row["best_ask"] is not None
            and row["max_no_ask"] is not None
            and float(row["best_ask"]) <= float(row["max_no_ask"]) + 1e-12
        )
        output.append(row)
    return output


def median_or_none(values: Iterable[float | None]) -> float | None:
    clean = [float(value) for value in values if value is not None]
    return statistics.median(clean) if clean else None


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    settled = [row for row in rows if row.get("correct") is not None]
    fills = [row for row in settled if row.get("actual_fill") == 1]
    city_days = {(str(row["city"]), str(row["target_date"])) for row in settled}
    expressions_by_city_day = Counter((str(row["city"]), str(row["target_date"])) for row in settled)
    false_city_days = {
        (str(row["city"]), str(row["target_date"]))
        for row in settled
        if int(row["correct"]) == 0
    }
    return {
        "signals": len(rows),
        "settled_signals": len(settled),
        "settled_dates": len({str(row["target_date"]) for row in settled}),
        "city_days": len(city_days),
        "terminal_false_city_days": len(false_city_days),
        "terminal_false_city_day_share": (
            len(false_city_days) / len(city_days) if city_days else None
        ),
        "wins": sum(int(row["correct"]) for row in settled),
        "accuracy": (
            sum(int(row["correct"]) for row in settled) / len(settled) if settled else None
        ),
        "actual_fill_expressions": len(fills),
        "actual_fill_wins": sum(int(row["correct"]) for row in fills),
        "actual_fill_accuracy": (
            sum(int(row["correct"]) for row in fills) / len(fills) if fills else None
        ),
        "filled_cross_margin_native_p50": median_or_none(
            row.get("cross_margin_native") for row in fills
        ),
        "filled_local_hour_p50": median_or_none(row.get("local_hour") for row in fills),
        "schema_v2_share": (
            sum(row["schema_version"] == "fast_source_prev_no_trial_v2" for row in settled)
            / len(settled)
            if settled
            else None
        ),
        "book_coverage": (
            sum(int(row["book_present"]) for row in settled) / len(settled) if settled else None
        ),
        "price_eligible_share": (
            sum(int(row["price_eligible"]) for row in settled) / len(settled) if settled else None
        ),
        "strong_observation_share": (
            sum(row.get("strong_observation_seen") is True for row in settled) / len(settled)
            if settled
            else None
        ),
        "cross_margin_native_p50": median_or_none(row.get("cross_margin_native") for row in settled),
        "local_hour_p50": median_or_none(row.get("local_hour") for row in settled),
        "source_detect_to_runner_sec_p50": median_or_none(
            row.get("source_detect_to_runner_sec") for row in settled
        ),
        "source_obs_lag_min_p50": median_or_none(row.get("source_obs_lag_min") for row in settled),
        "expressions_per_city_day_mean": (
            sum(expressions_by_city_day.values()) / len(expressions_by_city_day)
            if expressions_by_city_day
            else None
        ),
        "multi_cross_city_day_share": (
            sum(count >= 2 for count in expressions_by_city_day.values()) / len(expressions_by_city_day)
            if expressions_by_city_day
            else None
        ),
        "by_policy": dict(sorted(Counter(str(row["policy"]) for row in settled).items())),
        "by_city": {
            city: {
                "n": len(city_rows),
                "wins": sum(int(row["correct"]) for row in city_rows),
                "accuracy": sum(int(row["correct"]) for row in city_rows) / len(city_rows),
                "filled": sum(int(row["actual_fill"]) for row in city_rows),
            }
            for city in sorted({str(row["city"]) for row in settled})
            if (city_rows := [row for row in settled if row["city"] == city])
        },
    }


def summarize_fills(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"expressions": 0}
    cost = sum(float(row.get("buy_cost_usd") or 0) for row in rows)
    pnl = sum(float(row.get("net_pnl_usd") or 0) for row in rows)
    return {
        "expressions": len(rows),
        "wins": sum(int(row["correct"]) for row in rows),
        "accuracy": sum(int(row["correct"]) for row in rows) / len(rows),
        "active_dates": len({str(row["target_date"]) for row in rows}),
        "buy_cost_usd": cost,
        "net_pnl_usd": pnl,
        "net_roi": pnl / cost if cost else None,
        "fact_rows": sum(int(row.get("fact_rows") or 0) for row in rows),
        "instance_null_rows": sum(int(row.get("instance_null_rows") or 0) for row in rows),
    }


def block_bootstrap_delta(
    earlier: list[dict[str, Any]],
    later: list[dict[str, Any]],
    *,
    iterations: int = 20_000,
    seed: int = 20260812,
) -> dict[str, Any]:
    def blocks(rows: list[dict[str, Any]]) -> dict[str, list[int]]:
        out: dict[str, list[int]] = defaultdict(list)
        for row in rows:
            if row.get("correct") is not None:
                out[str(row["target_date"])].append(int(row["correct"]))
        return out

    early_blocks, late_blocks = blocks(earlier), blocks(later)
    if not early_blocks or not late_blocks:
        return {"delta": None, "ci95": [None, None], "iterations": 0}
    early_dates, late_dates = sorted(early_blocks), sorted(late_blocks)
    observed_early = sum(map(sum, early_blocks.values())) / sum(map(len, early_blocks.values()))
    observed_late = sum(map(sum, late_blocks.values())) / sum(map(len, late_blocks.values()))
    rng = random.Random(seed)
    deltas: list[float] = []
    for _ in range(iterations):
        early_draw = [rng.choice(early_dates) for _ in early_dates]
        late_draw = [rng.choice(late_dates) for _ in late_dates]
        early_values = [value for day in early_draw for value in early_blocks[day]]
        late_values = [value for day in late_draw for value in late_blocks[day]]
        deltas.append(sum(late_values) / len(late_values) - sum(early_values) / len(early_values))
    deltas.sort()
    low = deltas[int(0.025 * (iterations - 1))]
    high = deltas[int(0.975 * (iterations - 1))]
    return {
        "delta": observed_late - observed_early,
        "ci95": [low, high],
        "iterations": iterations,
        "seed": seed,
        "block": "target_date",
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def db_identity(conn: sqlite3.Connection, db: Path) -> dict[str, Any]:
    stat = db.stat()
    fact = conn.execute(
        "SELECT MAX(fact_built_at_utc), MAX(target_date), COUNT(*) FROM fact_trades"
    ).fetchone()
    settlement = conn.execute(
        "SELECT MAX(target_date), COUNT(*) FROM settlement_outcomes WHERE source_system='pm_history'"
    ).fetchone()
    return {
        "path": str(db),
        "realpath": str(db.resolve()),
        "device": stat.st_dev,
        "inode": stat.st_ino,
        "mtime_utc": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        "max_fact_built_at_utc": fact[0],
        "max_fact_target_date": fact[1],
        "fact_rows": fact[2],
        "max_pm_history_target_date": settlement[0],
        "pm_history_rows": settlement[1],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--events", type=Path, default=DEFAULT_EVENTS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--start-date", default="2026-07-09")
    parser.add_argument("--end-date", default="2026-08-11")
    args = parser.parse_args()

    conn = connect_ro(args.db)
    events = dedupe_event_rows(iter_jsonl(args.events), args.start_date, args.end_date)
    settlements_by_condition, settlements_by_city_date_bracket = load_settlements(
        conn, events, args.start_date, args.end_date
    )
    fills = load_cross_fills(conn, args.start_date, args.end_date)
    detail = attach_settlements_and_fills(
        events, settlements_by_condition, settlements_by_city_date_bracket, fills
    )

    window_signal_rows: dict[str, list[dict[str, Any]]] = {}
    window_fill_rows: dict[str, list[dict[str, Any]]] = {}
    window_summaries: dict[str, Any] = {}
    for name, (start, end) in WINDOWS.items():
        signal_rows = [row for row in detail if start <= row["target_date"] <= end]
        fill_rows = [row for row in fills if start <= row["target_date"] <= end]
        window_signal_rows[name] = signal_rows
        window_fill_rows[name] = fill_rows
        window_summaries[name] = {
            "window": [start, end],
            "signals": summarize(signal_rows),
            "fills": summarize_fills(fill_rows),
        }

    latest_vs_preceding_signals = block_bootstrap_delta(
        window_signal_rows["preceding_5d"], window_signal_rows["latest_5d"]
    )
    latest_vs_preceding_fills = block_bootstrap_delta(
        window_fill_rows["preceding_5d"], window_fill_rows["latest_5d"]
    )
    recent_vs_prior_signals = block_bootstrap_delta(
        window_signal_rows["prior_10d"], window_signal_rows["recent_10d"]
    )
    recent_vs_prior_fills = block_bootstrap_delta(
        window_fill_rows["prior_10d"], window_fill_rows["recent_10d"]
    )
    latest_vs_all_before_signals = block_bootstrap_delta(
        window_signal_rows["all_before_latest_5d"], window_signal_rows["latest_5d"]
    )
    latest_vs_all_before_fills = block_bootstrap_delta(
        window_fill_rows["all_before_latest_5d"], window_fill_rows["latest_5d"]
    )

    payload = {
        "schema_version": "weather_cross_no_accuracy_attribution_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "denominator_scope": (
            "fast_source_prev_no_trial emitted first cross expression, deduped by condition_id; "
            "raw event journal for signal coverage, canonical settlement_outcomes for labels, "
            "canonical fact_trades for live_real fills"
        ),
        "strategy_id": STRATEGY_ID,
        "candidate_grain": "first city-target_date-previous-bracket expression",
        "source_event_path": str(args.events),
        "source_event_mtime_utc": datetime.fromtimestamp(
            args.events.stat().st_mtime, timezone.utc
        ).isoformat(),
        "db_identity": db_identity(conn, args.db),
        "raw_event_rows": sum(1 for _ in iter_jsonl(args.events)),
        "deduped_signal_rows": len(events),
        "settled_signal_rows": sum(row["settled"] for row in detail),
        "canonical_fill_expressions": len(fills),
        "windows": window_summaries,
        "comparisons": {
            "latest_5d_minus_preceding_5d_signals": latest_vs_preceding_signals,
            "latest_5d_minus_preceding_5d_fills": latest_vs_preceding_fills,
            "recent_10d_minus_prior_10d_signals": recent_vs_prior_signals,
            "recent_10d_minus_prior_10d_fills": recent_vs_prior_fills,
            "latest_5d_minus_all_before_signals": latest_vs_all_before_signals,
            "latest_5d_minus_all_before_fills": latest_vs_all_before_fills,
        },
        "data_quality": {
            "cross_fill_fact_rows_after_2026_08_04": conn.execute(
                "SELECT COUNT(*) FROM fact_trades WHERE strategy_id=? AND trade_class='live_real' AND target_date>'2026-08-04'",
                (STRATEGY_ID,),
            ).fetchone()[0],
            "cross_fill_instance_null_rows_after_2026_08_04": conn.execute(
                "SELECT COUNT(*) FROM fact_trades WHERE strategy_id=? AND trade_class='live_real' AND target_date>'2026-08-04' AND instance_id IS NULL",
                (STRATEGY_ID,),
            ).fetchone()[0],
        },
        "settled_fill_losses_2026_08_02_11": [
            {
                "target_date": row["target_date"],
                "city": row["city"],
                "bracket": row["bracket"],
                "condition_id": row["condition_id"],
                "buy_cost_usd": row["buy_cost_usd"],
                "net_pnl_usd": row["net_pnl_usd"],
            }
            for row in fills
            if "2026-08-02" <= str(row["target_date"]) <= "2026-08-11"
            and int(row["correct"]) == 0
        ],
    }
    conn.close()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    write_csv(args.output_dir / "signal_expressions.csv", detail)
    write_csv(args.output_dir / "fill_expressions.csv", fills)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
