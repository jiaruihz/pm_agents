#!/usr/bin/env python3
"""
Build weather_live_order_events.

Grain: one strategy-local live order journal row. This is submitted-order /
blocked-order / lifecycle-action lineage, not fill-grain PnL truth. Filled PnL
stays in fact_trades.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "runtime" / "weather.db"
DEFAULT_ORDER_FILES = (
    ROOT / "runtime/weather_edge_v1/live/low_price_yes_lottery_tiny_live_v1_orders.jsonl",
    ROOT / "runtime/weather_edge_v1/live/low_price_yes_take_profit_exit_v1_orders.jsonl",
    ROOT / "runtime/weather_edge_v1/regime_routed_no_tiny_live_v1/live_orders.jsonl",
)


DDL = """
DROP TABLE IF EXISTS weather_live_order_events;
CREATE TABLE weather_live_order_events (
    event_id TEXT PRIMARY KEY,
    source_path TEXT NOT NULL,
    source_line_no INTEGER NOT NULL,
    source_mtime_utc TEXT,
    record_type TEXT,
    created_at_utc TEXT,
    strategy_instance TEXT,
    strategy_id TEXT,
    strategy_family TEXT,
    decision_mode TEXT,
    execution_mode TEXT,
    execution_policy TEXT,
    execution_action TEXT,
    child_order_role TEXT,
    profile TEXT,
    combo TEXT,
    signal_id TEXT,
    plan_id TEXT,
    execution_id TEXT,
    order_id TEXT,
    source_order_id TEXT,
    cancel_before_order_id TEXT,
    city TEXT,
    city_pool TEXT,
    icao TEXT,
    target_date TEXT,
    bracket TEXT,
    unit TEXT,
    signal_side TEXT,
    order_side TEXT,
    token_id TEXT,
    condition_id TEXT,
    market_id TEXT,
    status TEXT,
    clob_status TEXT,
    quote_status TEXT,
    quote_reason TEXT,
    error_classification TEXT,
    error_reason TEXT,
    order_kind TEXT,
    maker_only INTEGER,
    sizing_policy TEXT,
    score_dist_sizing_model TEXT,
    score_dist_probability REAL,
    score_dist_tier TEXT,
    score_dist_multiplier REAL,
    limit_price REAL,
    requested_price REAL,
    posted_price REAL,
    best_bid REAL,
    best_ask REAL,
    spread REAL,
    shares REAL,
    source_remaining_shares REAL,
    source_filled_shares REAL,
    notional REAL,
    posted_notional REAL,
    order_notional_cap REAL,
    model_p_yes REAL,
    edge REAL,
    fee_adjusted_edge REAL,
    forecast_source TEXT,
    model_version TEXT,
    forecast_max_native REAL,
    forecast_max_f REAL,
    forecast_peak_time_local TEXT,
    decision_snapshot_ts_utc TEXT,
    snapshot_ts_utc TEXT,
    source_order_age_min REAL,
    final_yes REAL,
    settlement_status TEXT,
    settled INTEGER,
    contract_won INTEGER,
    payload TEXT NOT NULL,
    built_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_weather_live_order_events_strategy_date
    ON weather_live_order_events(strategy_instance, target_date, city);
CREATE INDEX IF NOT EXISTS idx_weather_live_order_events_signal
    ON weather_live_order_events(signal_id);
CREATE INDEX IF NOT EXISTS idx_weather_live_order_events_status
    ON weather_live_order_events(status, clob_status, quote_status);
CREATE INDEX IF NOT EXISTS idx_weather_live_order_events_sizing
    ON weather_live_order_events(sizing_policy, score_dist_tier);
"""


COLUMNS = [
    "event_id",
    "source_path",
    "source_line_no",
    "source_mtime_utc",
    "record_type",
    "created_at_utc",
    "strategy_instance",
    "strategy_id",
    "strategy_family",
    "decision_mode",
    "execution_mode",
    "execution_policy",
    "execution_action",
    "child_order_role",
    "profile",
    "combo",
    "signal_id",
    "plan_id",
    "execution_id",
    "order_id",
    "source_order_id",
    "cancel_before_order_id",
    "city",
    "city_pool",
    "icao",
    "target_date",
    "bracket",
    "unit",
    "signal_side",
    "order_side",
    "token_id",
    "condition_id",
    "market_id",
    "status",
    "clob_status",
    "quote_status",
    "quote_reason",
    "error_classification",
    "error_reason",
    "order_kind",
    "maker_only",
    "sizing_policy",
    "score_dist_sizing_model",
    "score_dist_probability",
    "score_dist_tier",
    "score_dist_multiplier",
    "limit_price",
    "requested_price",
    "posted_price",
    "best_bid",
    "best_ask",
    "spread",
    "shares",
    "source_remaining_shares",
    "source_filled_shares",
    "notional",
    "posted_notional",
    "order_notional_cap",
    "model_p_yes",
    "edge",
    "fee_adjusted_edge",
    "forecast_source",
    "model_version",
    "forecast_max_native",
    "forecast_max_f",
    "forecast_peak_time_local",
    "decision_snapshot_ts_utc",
    "snapshot_ts_utc",
    "source_order_age_min",
    "final_yes",
    "settlement_status",
    "settled",
    "contract_won",
    "payload",
]


def safe_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def safe_int_bool(value: Any) -> int | None:
    if value is None:
        return None
    return 1 if bool(value) else 0


def utc_mtime(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
    except OSError:
        return None


def stable_hash(parts: list[Any]) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def read_jsonl(path: Path) -> list[tuple[int, dict[str, Any]]]:
    rows: list[tuple[int, dict[str, Any]]] = []
    if not path.exists():
        return rows
    for idx, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append((idx, payload))
    return rows


def nested(row: dict[str, Any], *path: str) -> Any:
    cur: Any = row
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def first_value(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def order_id_from(row: dict[str, Any]) -> str | None:
    return safe_str(
        first_value(
            row,
            "order_id",
            "clob_order_id",
            "source_order_id",
        )
        or nested(row, "exchange_response", "place", "orderID")
        or nested(row, "exchange_response", "place", "orderId")
    )


def clob_status_from(row: dict[str, Any]) -> str | None:
    return safe_str(
        first_value(row, "clob_status")
        or nested(row, "exchange_response", "place", "status")
        or nested(row, "exchange_response", "status")
    )


def error_classification_from(row: dict[str, Any]) -> str | None:
    return safe_str(
        first_value(row, "error_classification")
        or nested(row, "exchange_response", "error_classification")
    )


def error_reason_from(row: dict[str, Any]) -> str | None:
    return safe_str(
        first_value(row, "error_reason", "error")
        or nested(row, "exchange_response", "error_reason")
        or nested(row, "exchange_response", "error")
        or nested(row, "exchange_response", "place", "errorMsg")
    )


def order_kind(row: dict[str, Any]) -> str | None:
    action = safe_str(row.get("execution_action"))
    if action:
        return action
    if row.get("maker_only") is True:
        return "maker"
    if row.get("maker_only") is False:
        return "taker"
    role = safe_str(row.get("child_order_role"))
    return role


def load_settlement_lookup(conn: sqlite3.Connection) -> tuple[dict[str, sqlite3.Row], dict[tuple[str, str, str], sqlite3.Row]]:
    by_token: dict[str, sqlite3.Row] = {}
    by_city_date_bracket: dict[tuple[str, str, str], sqlite3.Row] = {}
    conn.row_factory = sqlite3.Row
    for row in conn.execute(
        """
        SELECT city, target_date, bracket, token_id, final_price, settlement_status
        FROM settlement_outcomes
        """
    ):
        if row["token_id"]:
            by_token[str(row["token_id"])] = row
        by_city_date_bracket[(str(row["city"]), str(row["target_date"]), str(row["bracket"]))] = row
    return by_token, by_city_date_bracket


def settlement_for(
    row: dict[str, Any],
    by_token: dict[str, sqlite3.Row],
    by_city_date_bracket: dict[tuple[str, str, str], sqlite3.Row],
) -> sqlite3.Row | None:
    token = safe_str(row.get("token_id"))
    if token and token in by_token:
        return by_token[token]
    key = (safe_str(row.get("city")) or "", safe_str(row.get("target_date")) or "", safe_str(row.get("bracket")) or "")
    return by_city_date_bracket.get(key)


def contract_won(row: dict[str, Any], final_yes: float | None) -> int | None:
    if final_yes is None:
        return None
    side = (safe_str(row.get("signal_side")) or safe_str(row.get("order_side")) or "").upper()
    if side in {"BUY_YES", "YES"}:
        return 1 if final_yes >= 0.99 else 0
    if side in {"BUY_NO", "NO"}:
        return 1 if final_yes <= 0.01 else 0
    if side == "SELL_YES":
        return 1 if final_yes <= 0.01 else 0
    if side == "SELL_NO":
        return 1 if final_yes >= 0.99 else 0
    return None


def normalize_row(
    *,
    path: Path,
    line_no: int,
    row: dict[str, Any],
    mtime_utc: str | None,
    by_token: dict[str, sqlite3.Row],
    by_city_date_bracket: dict[tuple[str, str, str], sqlite3.Row],
) -> dict[str, Any]:
    settlement = settlement_for(row, by_token, by_city_date_bracket)
    final_yes = safe_float(settlement["final_price"]) if settlement else None
    event = {
        "event_id": stable_hash([str(path), line_no, row.get("execution_id"), row.get("created_at_utc")]),
        "source_path": str(path),
        "source_line_no": line_no,
        "source_mtime_utc": mtime_utc,
        "record_type": safe_str(row.get("record_type")),
        "created_at_utc": safe_str(row.get("created_at_utc")),
        "strategy_instance": safe_str(row.get("strategy_instance")),
        "strategy_id": safe_str(row.get("strategy_id")),
        "strategy_family": safe_str(row.get("strategy_family")),
        "decision_mode": safe_str(row.get("decision_mode")),
        "execution_mode": safe_str(row.get("execution_mode")),
        "execution_policy": safe_str(row.get("execution_policy")),
        "execution_action": safe_str(row.get("execution_action")),
        "child_order_role": safe_str(row.get("child_order_role")),
        "profile": safe_str(row.get("profile")),
        "combo": safe_str(row.get("combo")),
        "signal_id": safe_str(row.get("signal_id")),
        "plan_id": safe_str(row.get("plan_id")),
        "execution_id": safe_str(row.get("execution_id")),
        "order_id": order_id_from(row),
        "source_order_id": safe_str(row.get("source_order_id")),
        "cancel_before_order_id": safe_str(row.get("cancel_before_order_id")),
        "city": safe_str(row.get("city")),
        "city_pool": safe_str(row.get("city_pool")),
        "icao": safe_str(row.get("icao")),
        "target_date": safe_str(row.get("target_date") or row.get("event_date")),
        "bracket": safe_str(row.get("bracket")),
        "unit": safe_str(row.get("unit")),
        "signal_side": safe_str(row.get("signal_side") or row.get("side")),
        "order_side": safe_str(row.get("order_side")),
        "token_id": safe_str(row.get("token_id")),
        "condition_id": safe_str(row.get("condition_id") or row.get("market_id")),
        "market_id": safe_str(row.get("market_id") or row.get("condition_id")),
        "status": safe_str(row.get("status")),
        "clob_status": clob_status_from(row),
        "quote_status": safe_str(row.get("quote_status") or nested(row, "exchange_response", "quote_status")),
        "quote_reason": safe_str(row.get("quote_reason") or nested(row, "exchange_response", "quote_reason")),
        "error_classification": error_classification_from(row),
        "error_reason": error_reason_from(row),
        "order_kind": order_kind(row),
        "maker_only": safe_int_bool(row.get("maker_only")),
        "sizing_policy": safe_str(first_value(row, "sizing_policy", "live_sizing_policy", "sizing_mode")),
        "score_dist_sizing_model": safe_str(row.get("score_dist_sizing_model")),
        "score_dist_probability": safe_float(row.get("score_dist_probability")),
        "score_dist_tier": safe_str(row.get("score_dist_tier")),
        "score_dist_multiplier": safe_float(row.get("score_dist_multiplier")),
        "limit_price": safe_float(row.get("limit_price")),
        "requested_price": safe_float(row.get("requested_price") or nested(row, "exchange_response", "requested_price")),
        "posted_price": safe_float(row.get("posted_price") or nested(row, "exchange_response", "posted_price")),
        "best_bid": safe_float(first_value(row, "best_bid", "quote_best_bid") or nested(row, "exchange_response", "best_bid")),
        "best_ask": safe_float(first_value(row, "best_ask", "quote_best_ask") or nested(row, "exchange_response", "best_ask")),
        "spread": safe_float(first_value(row, "spread", "quote_spread") or nested(row, "exchange_response", "quote_spread")),
        "shares": safe_float(first_value(row, "shares", "size", "fixed_order_shares", "planned_shares")),
        "source_remaining_shares": safe_float(row.get("source_remaining_shares")),
        "source_filled_shares": safe_float(row.get("source_filled_shares")),
        "notional": safe_float(row.get("notional") or row.get("planned_notional_usd")),
        "posted_notional": safe_float(row.get("posted_notional")),
        "order_notional_cap": safe_float(row.get("order_notional_cap")),
        "model_p_yes": safe_float(first_value(row, "model_p_yes", "model_p_yes_used", "model_token_probability")),
        "edge": safe_float(first_value(row, "edge", "edge_used_yes", "quote_edge")),
        "fee_adjusted_edge": safe_float(first_value(row, "fee_adjusted_edge", "required_quote_edge")),
        "forecast_source": safe_str(row.get("forecast_source")),
        "model_version": safe_str(row.get("model_version")),
        "forecast_max_native": safe_float(row.get("forecast_max_native")),
        "forecast_max_f": safe_float(row.get("forecast_max_f")),
        "forecast_peak_time_local": safe_str(row.get("forecast_peak_time_local")),
        "decision_snapshot_ts_utc": safe_str(row.get("decision_snapshot_ts_utc")),
        "snapshot_ts_utc": safe_str(row.get("snapshot_ts_utc")),
        "source_order_age_min": safe_float(row.get("source_order_age_min")),
        "final_yes": final_yes,
        "settlement_status": safe_str(settlement["settlement_status"]) if settlement else None,
        "settled": 1 if settlement and settlement["settlement_status"] == "settled" and final_yes in {0.0, 1.0} else 0,
        "contract_won": contract_won(row, final_yes),
        "payload": json.dumps(row, ensure_ascii=False, sort_keys=True),
    }
    return event


def build(conn: sqlite3.Connection, order_files: list[Path]) -> dict[str, Any]:
    conn.executescript(DDL)
    by_token, by_city_date_bracket = load_settlement_lookup(conn)
    inserted = 0
    input_rows = 0
    files_seen: list[str] = []
    placeholders = ",".join("?" for _ in COLUMNS)
    sql = f"INSERT INTO weather_live_order_events ({','.join(COLUMNS)}) VALUES ({placeholders})"
    for path in order_files:
        rows = read_jsonl(path)
        if not rows:
            continue
        files_seen.append(str(path))
        mtime_utc = utc_mtime(path)
        for line_no, row in rows:
            input_rows += 1
            event = normalize_row(
                path=path,
                line_no=line_no,
                row=row,
                mtime_utc=mtime_utc,
                by_token=by_token,
                by_city_date_bracket=by_city_date_bracket,
            )
            conn.execute(sql, [event.get(col) for col in COLUMNS])
            inserted += 1
    conn.commit()
    summary = {
        "input_rows": input_rows,
        "inserted_rows": inserted,
        "files": files_seen,
    }
    summary["by_strategy_status"] = [
        dict(row)
        for row in conn.execute(
            """
            SELECT strategy_instance, status, clob_status, COUNT(*) AS rows
            FROM weather_live_order_events
            GROUP BY strategy_instance, status, clob_status
            ORDER BY strategy_instance, status, clob_status
            """
        )
    ]
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Build weather_live_order_events from strategy live order JSONL")
    parser.add_argument("--db-path", default=str(DB_PATH))
    parser.add_argument("--order-file", action="append", help="Specific live order JSONL. Can be repeated.")
    args = parser.parse_args()

    order_files = [Path(p) for p in args.order_file] if args.order_file else [p for p in DEFAULT_ORDER_FILES if p.exists()]
    conn = sqlite3.connect(args.db_path)
    conn.row_factory = sqlite3.Row
    try:
        summary = build(conn, order_files)
    finally:
        conn.close()
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
