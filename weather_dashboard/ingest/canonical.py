from __future__ import annotations

import json
from typing import Any, Callable, Iterable, Mapping

from weather_dashboard.contract import (
    validate_canonical_fill,
    validate_canonical_order,
    validate_canonical_plan,
    validate_canonical_settlement,
    validate_canonical_signal,
)
from weather_dashboard.ingest.common import already_ingested, record_ingestion, row_hash


Validator = Callable[[Mapping[str, Any]], None]


SIGNAL_COLUMNS = (
    "signal_id",
    "producer_system",
    "producer_run_id",
    "snapshot_ts_utc",
    "snapshot_file",
    "target_date",
    "city",
    "city_pool",
    "icao",
    "bracket",
    "unit",
    "signal_side",
    "model_version",
    "model_p_yes",
    "forecast_source",
    "forecast_max_f",
    "forecast_max_native",
    "forecast_peak_hour_local",
    "forecast_peak_time_local",
    "forecast_peak_hour_utc",
    "forecast_peak_time_utc",
    "forecast_hourly_count",
    "forecast_values_hash",
    "forecast_peak_source",
    "forecast_timezone",
    "forecast_utc_offset_seconds",
    "forecast_peak_delta_hours_local",
    "forecast_max_in_bracket",
    "forecast_max_above_bracket_f",
    "forecast_max_below_bracket_f",
    "forecast_max_above_metar_max_f",
    "market_price",
    "edge",
    "abs_edge",
    "condition_id",
    "market_id",
    "token_id",
    "hours_to_settle",
)

PLAN_COLUMNS = (
    "plan_id",
    "run_id",
    "signal_id",
    "config_id",
    "order_side",
    "notional",
    "desired_shares",
    "sizing_mode",
    "entry_price_window",
    "execution_policy",
    "limit_price",
    "skip_reason",
    "status",
)

ORDER_COLUMNS = (
    "execution_id",
    "order_id",
    "run_id",
    "plan_id",
    "venue",
    "order_side",
    "limit_price",
    "entry_price",
    "shares",
    "cost_usd",
    "notional",
    "status",
    "clob_status",
    "quote_status",
    "quote_mode",
    "quote_reason",
    "quote_tick_size",
    "risk_status",
    "risk_reason",
    "error_classification",
    "error_reason",
    "execution_action",
    "child_order_role",
    "source_order_id",
    "cancel_before_order_id",
    "maker_only",
    "sizing_policy",
    "score_dist_sizing_model",
    "score_dist_probability",
    "score_dist_tier",
    "score_dist_multiplier",
    "requested_price",
    "posted_price",
    "posted_notional",
    "best_bid",
    "best_ask",
    "spread",
    "model_p_yes_used",
    "market_implied_p_yes",
    "quote_edge",
    "fee_adjusted_edge",
    "source_order_age_min",
    "exchange_response",
    "order_payload",
    "placed_at_utc",
)

FILL_COLUMNS = (
    "fill_id",
    "execution_id",
    "order_id",
    "filled_shares",
    "filled_price",
    "fees_usd",
    "status",
    "filled_at_utc",
)

SETTLEMENT_COLUMNS = (
    "settlement_id",
    "target_date",
    "condition_id",
    "market_id",
    "bracket",
    "token_id",
    "final_price",
    "settlement_status",
)

RUN_COLUMNS = (
    "run_id",
    "producer_system",
    "producer_run_id",
    "config_id",
    "universe_id",
    "code_version",
    "execution_mode",
    "date_range_start",
    "date_range_end",
    "started_at_utc",
    "ended_at_utc",
    "state",
    "source_root",
    "repro_key",
    "parent_run_id",
    "tags",
    "metrics",
    "metrics_at_utc",
    "notes",
)


def _json_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _insert_or_ignore(conn, table: str, row: Mapping[str, Any], columns: tuple[str, ...]) -> int:
    placeholders = ", ".join("?" for _ in columns)
    column_sql = ", ".join(columns)
    cur = conn.execute(
        f"INSERT OR IGNORE INTO {table} ({column_sql}) VALUES ({placeholders})",
        tuple(row.get(column) for column in columns),
    )
    return cur.rowcount


def insert_code_version(conn, code_version: str, **fields: Any) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO code_versions (
            code_version, branch, commit_subject, commit_at_utc, deployed_at_utc, notes
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            code_version,
            fields.get("branch"),
            fields.get("commit_subject"),
            fields.get("commit_at_utc"),
            fields.get("deployed_at_utc"),
            fields.get("notes"),
        ),
    )
    conn.commit()


def insert_strategy_config(conn, config_id: str, name: str, params: Mapping[str, Any] | str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO strategy_config (config_id, name, params) VALUES (?, ?, ?)",
        (config_id, name, _json_text(params)),
    )
    conn.commit()


def insert_universe(
    conn,
    universe_id: str,
    name: str,
    *,
    cities: Iterable[str],
    models: Iterable[str],
    description: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO universes (universe_id, name, description, cities, models)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            universe_id,
            name,
            description,
            _json_text(list(cities)),
            _json_text(list(models)),
        ),
    )
    conn.commit()


def insert_run(conn, row: Mapping[str, Any]) -> None:
    payload = dict(row)
    for field in ("tags", "metrics"):
        if field in payload and payload[field] is not None:
            payload[field] = _json_text(payload[field])
    _insert_or_ignore(conn, "runs", payload, RUN_COLUMNS)
    conn.commit()


def _ingest_rows(
    conn,
    *,
    rows: Iterable[Mapping[str, Any]],
    source_path: str,
    target_table: str,
    id_field: str,
    columns: tuple[str, ...],
    validator: Validator,
) -> int:
    inserted = 0
    for row in rows:
        validator(row)
        h = row_hash(dict(row))
        if already_ingested(conn, source_path, h, target_table):
            continue

        inserted_now = _insert_or_ignore(conn, target_table, row, columns)
        record_ingestion(conn, source_path, h, target_table, str(row[id_field]))
        inserted += inserted_now

    conn.commit()
    return inserted


def ingest_canonical_signals(conn, rows: Iterable[Mapping[str, Any]], source_path: str) -> int:
    return _ingest_rows(
        conn,
        rows=rows,
        source_path=source_path,
        target_table="signals",
        id_field="signal_id",
        columns=SIGNAL_COLUMNS,
        validator=validate_canonical_signal,
    )


def ingest_canonical_plans(conn, rows: Iterable[Mapping[str, Any]], source_path: str) -> int:
    return _ingest_rows(
        conn,
        rows=rows,
        source_path=source_path,
        target_table="plans",
        id_field="plan_id",
        columns=PLAN_COLUMNS,
        validator=validate_canonical_plan,
    )


def ingest_canonical_orders(conn, rows: Iterable[Mapping[str, Any]], source_path: str) -> int:
    return _ingest_rows(
        conn,
        rows=rows,
        source_path=source_path,
        target_table="orders",
        id_field="execution_id",
        columns=ORDER_COLUMNS,
        validator=validate_canonical_order,
    )


def ingest_canonical_fills(conn, rows: Iterable[Mapping[str, Any]], source_path: str) -> int:
    return _ingest_rows(
        conn,
        rows=rows,
        source_path=source_path,
        target_table="fills",
        id_field="fill_id",
        columns=FILL_COLUMNS,
        validator=validate_canonical_fill,
    )


def ingest_canonical_settlements(conn, rows: Iterable[Mapping[str, Any]], source_path: str) -> int:
    return _ingest_rows(
        conn,
        rows=rows,
        source_path=source_path,
        target_table="settlements",
        id_field="settlement_id",
        columns=SETTLEMENT_COLUMNS,
        validator=validate_canonical_settlement,
    )
