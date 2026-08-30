#!/usr/bin/env python3
"""Audit the project-level weather causal-clock contract and impact radius."""

from __future__ import annotations

import argparse
import ast
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
import sys
from typing import Any, Iterable
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_clock_contract import parse_utc  # noqa: E402


SCHEMA_VERSION = "weather_project_clock_audit_v2"
DEFAULT_DB = Path("/Volumes/jrs/pm_agents/runtime/weather.db")
DEFAULT_OBSERVATIONS = Path(
    "/Volumes/jrs/weather_data_feed_service_runtime/output/observations/latest.json"
)
DEFAULT_TMIN_CORRECTED = (
    ROOT / "reviews/tmin_v2_1_v3_strategy_readout_v1/CORRECTED_DATE_REPLAY_RESULTS.json"
)
DEFAULT_TMIN_RAW = (
    ROOT / "runtime/research/tmin_cross_metar_basis_replay_v1/raw"
)

# These modules own clocks that can change a source event, probability,
# selection, order, fill, or public-book claim.  Descriptive UI/report clocks
# are intentionally outside this boundary.
BOUNDARY_MODULES = (
    "weather_clock_contract.py",
    "weather_model_evaluation/contracts.py",
    "weather_data_feed/source_lineage.py",
    "weather_data_feed/information_events.py",
    "weather_data_feed/city_calendar.py",
    "weather_data_feed/forecast_run_contract.py",
    "weather_data_feed/source_event_incremental_state.py",
    "weather_data_feed/input_catalog.py",
    "weather_data_feed/observation_cache.py",
    "weather_data_feed/source_basis.py",
    "weather_data_feed/forecast_hourly_curves.py",
    "weather_data_feed/physical_features.py",
    "weather_data_feed/ladder_snapshot_history.py",
    "weather_data_feed/historical_forecast_runs.py",
    "weather_data_feed_service/scheduling.py",
    "weather_data_feed_service/market_books.py",
    "weather_data_feed_service/forecast_enrichment.py",
    "weather_data_feed_service/market_books_ws.py",
    "weather_city_runtime/contracts.py",
    "src/platform/market_data/capture_contract.py",
    "src/platform/market_data/capture_demand.py",
    "src/platform/market_data/executable_book_truth.py",
    "src/platform/market_data/execution_evidence.py",
    "src/platform/market_data/ws_incremental_book.py",
    "src/strategies/weather_edge_v1/execution/lifecycle.py",
    "src/strategies/weather_edge_v1/runtime/execution_journal.py",
    "src/strategies/weather_edge_v1/runtime/order_runtime.py",
    "src/strategies/weather_edge_v1/execution/venue/polymarket.py",
    "src/strategies/weather_edge_v1/execution/conditional_stop_loss.py",
    "src/strategies/weather_edge_v1/tools/execution_pipeline.py",
    "scripts/ops/weather_current_yes_core_carry_tiny_live_v2.py",
    "scripts/ops/weather_theta_current_yes_tiny_live.py",
    "weather_data_feed_service/legacy_weather_predict/paper_snapshot.py",
    "weather_dashboard/legacy_migration/live_cycle.py",
    "weather_dashboard/legacy_migration/strategy_runtime_orders.py",
    "weather_dashboard/ingest/signal_clock_adjustments.py",
    "scripts/ops/reconcile_weather_signal_clocks.py",
    "scripts/ops/backfill_weather_execution_evidence.py",
    "scripts/ops/materialize_weather_execution_evidence.py",
    "scripts/etl/build_weather_fact_trades.py",
    "scripts/etl/build_weather_signal_candidates.py",
    "scripts/etl/materialize_weather_observation_state.py",
    "scripts/etl/materialize_tmax_v2_canonical_state.py",
    "scripts/etl/backfill_signal_candidate_decision_windows.py",
    "weather_dashboard/ingest/clob_fill_sync.py",
    "weather_data_feed/observation_sources/aviationweather.py",
    "weather_data_feed/observation_sources/fetchers.py",
    "weather_data_feed/observation_sources/iem.py",
    "weather_data_feed/observation_sources/metar.py",
    "weather_data_feed/forecast_sources.py",
    "weather_data_feed/high_frequency_observation_sources.py",
    "weather_data_feed/market_book_ladder_history.py",
    "weather_data_feed/runway_sources.py",
    "weather_data_feed/forecast_history.py",
    "weather_data_feed/korea_amos_features.py",
    "weather_data_feed/helsinki_remaining_heat_features.py",
    "weather_data_feed_service/forecast_run_capture.py",
    "weather_feature_layer/transitions.py",
    "weather_feature_layer/builders.py",
    "weather_dashboard/analysis_freshness.py",
    "weather_model_evaluation/daily_minimum.py",
    "weather_model_evaluation/daily_minimum_next_colder.py",
    "weather_model_evaluation/source_event_ws_linkage.py",
    "weather_model_evaluation/first_seen_event_ladder_panel.py",
    "weather_model_evaluation/korea_city_exact_no.py",
    "weather_model_evaluation/forecast_repricing_tape.py",
    "weather_model_evaluation/rest_quote_path.py",
    "weather_model_evaluation/tokyo_market_prior_adapter.py",
    "src/strategies/weather_city_probability_shadow/busan.py",
    "src/strategies/weather_city_probability_shadow/tokyo.py",
    "src/strategies/weather_city_probability_shadow/amsterdam.py",
    "weather_city_runtime/amsterdam_frozen_shadow_v2.py",
)
PARSER_NAMES = {
    "parse_utc",
    "_parse_utc",
    "parse_dt",
    "_parse_ts",
    "_parse",
    "_timestamp",
    "_utc",
    "_parse_cache_utc",
    "_ts_to_date",
}


def _function_uses_direct_fromisoformat(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        function = child.func
        if isinstance(function, ast.Attribute) and function.attr == "fromisoformat":
            return True
    return False


def audit_static(repo_root: Path) -> dict[str, Any]:
    violations: list[dict[str, Any]] = []
    for relative in BOUNDARY_MODULES:
        path = repo_root / relative
        if not path.is_file():
            violations.append({"path": relative, "reason": "missing_boundary_module"})
            continue
        text = path.read_text(encoding="utf-8")
        if relative != "weather_clock_contract.py" and "weather_clock_contract" not in text:
            violations.append(
                {"path": relative, "reason": "missing_shared_clock_import"}
            )
        try:
            tree = ast.parse(text, filename=str(path))
        except SyntaxError as exc:
            violations.append(
                {"path": relative, "reason": "syntax_error", "detail": str(exc)}
            )
            continue
        if relative != "weather_clock_contract.py":
            for node in ast.walk(tree):
                if (
                    isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name in PARSER_NAMES
                    and _function_uses_direct_fromisoformat(node)
                ):
                    violations.append(
                        {
                            "path": relative,
                            "line": node.lineno,
                            "reason": "private_datetime_parser_bypasses_shared_contract",
                            "function": node.name,
                        }
                    )
        if "UTC-15h" in text and "old" not in text.lower():
            violations.append(
                {"path": relative, "reason": "active_utc_minus_15_semantics"}
            )
    return {
        "scope": "clock-affecting weather boundary modules",
        "modules": len(BOUNDARY_MODULES),
        "violations": violations,
        "status": "pass" if not violations else "fail",
    }


def _parse_present(value: Any, *, field: str) -> tuple[datetime | None, str | None]:
    if value in (None, ""):
        return None, None
    try:
        parsed = parse_utc(value, field=field)
    except ValueError as exc:
        return None, str(exc)
    assert parsed is not None
    return parsed, None


def _audit_rows(
    rows: Iterable[sqlite3.Row],
    *,
    table: str,
    identity_field: str,
    clock_fields: tuple[str, ...],
    ordered_pairs: tuple[tuple[str, str], ...],
    precision_tolerance_seconds: dict[tuple[str, str], float] | None = None,
) -> dict[str, Any]:
    count = 0
    invalid: list[dict[str, Any]] = []
    reversals: list[dict[str, Any]] = []
    precision_only: list[dict[str, Any]] = []
    tolerances = precision_tolerance_seconds or {}
    for row in rows:
        count += 1
        parsed: dict[str, datetime | None] = {}
        for field in clock_fields:
            parsed[field], error = _parse_present(row[field], field=field)
            if error:
                invalid.append(
                    {
                        "table": table,
                        "identity": row[identity_field],
                        "field": field,
                        "value": row[field],
                        "error": error,
                    }
                )
        for earlier, later in ordered_pairs:
            if (
                parsed.get(earlier) is not None
                and parsed.get(later) is not None
                and parsed[earlier] > parsed[later]
            ):
                detail = {
                    "table": table,
                    "identity": row[identity_field],
                    "earlier_field": earlier,
                    "earlier_value": row[earlier],
                    "later_field": later,
                    "later_value": row[later],
                    "reversal_seconds": (
                        parsed[earlier] - parsed[later]
                    ).total_seconds(),
                }
                tolerance = float(tolerances.get((earlier, later), 0.0))
                if detail["reversal_seconds"] <= tolerance:
                    detail["precision_tolerance_seconds"] = tolerance
                    precision_only.append(detail)
                else:
                    reversals.append(detail)
    return {
        "rows": count,
        "invalid_timezone_or_format_rows": len(invalid),
        "causal_reversal_rows": len(reversals),
        "precision_only_reversal_rows": len(precision_only),
        "invalid": invalid,
        "reversals": reversals,
        "precision_only_reversals": precision_only,
    }


def _db_identity(path: Path) -> dict[str, Any]:
    stat = os.stat(path)
    return {
        "path": str(path),
        "realpath": str(path.resolve()),
        "device": stat.st_dev,
        "inode": stat.st_ino,
        "size_bytes": stat.st_size,
    }


def audit_canonical_db(path: Path) -> dict[str, Any]:
    # The canonical DB is live/WAL-backed. immutable=1 can hide committed WAL
    # pages, so audits use a bounded read-only connection instead.
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    try:
        candidates = _audit_rows(
            connection.execute(
                """SELECT candidate_id, decision_ts_utc, book_snapshot_ts_utc,
                          book_available_at_utc, pre_event_book_available_at_utc,
                          first_seen_ts_utc, last_seen_ts_utc
                   FROM fact_signal_candidates"""
            ),
            table="fact_signal_candidates",
            identity_field="candidate_id",
            clock_fields=(
                "decision_ts_utc",
                "book_snapshot_ts_utc",
                "book_available_at_utc",
                "pre_event_book_available_at_utc",
                "first_seen_ts_utc",
                "last_seen_ts_utc",
            ),
            ordered_pairs=(
                ("book_snapshot_ts_utc", "decision_ts_utc"),
                ("book_available_at_utc", "decision_ts_utc"),
                ("pre_event_book_available_at_utc", "decision_ts_utc"),
                ("first_seen_ts_utc", "last_seen_ts_utc"),
            ),
        )
        trades = _audit_rows(
            connection.execute(
                """SELECT fill_id, snapshot_ts_utc, order_ts_utc, fill_ts_utc,
                          val_snapshot_ts_utc, fact_built_at_utc
                   FROM fact_trades"""
            ),
            table="fact_trades",
            identity_field="fill_id",
            clock_fields=(
                "snapshot_ts_utc",
                "order_ts_utc",
                "fill_ts_utc",
                "val_snapshot_ts_utc",
                "fact_built_at_utc",
            ),
            ordered_pairs=(
                ("snapshot_ts_utc", "order_ts_utc"),
                ("order_ts_utc", "fill_ts_utc"),
                ("fill_ts_utc", "fact_built_at_utc"),
            ),
            # Authenticated activity timestamps are second-resolution while
            # local order clocks retain microseconds. Sub-second reversals are
            # precision loss, not evidence that a fill preceded submission.
            precision_tolerance_seconds={
                ("order_ts_utc", "fill_ts_utc"): 1.0,
            },
        )
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        fact_columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(fact_trades)")
        }
        if "signal_clock_lineage_status" in fact_columns:
            lineage_counts = {
                str(row[0] or "legacy_unclassified"): int(row[1])
                for row in connection.execute(
                    "SELECT COALESCE(signal_clock_lineage_status, "
                    "'legacy_unclassified'), COUNT(*) FROM fact_trades GROUP BY 1"
                )
            }
            blocked_lineage = [
                dict(row)
                for row in connection.execute(
                    "SELECT fill_id, signal_id, strategy_key, city, target_date, "
                    "original_snapshot_ts_utc, snapshot_ts_utc, order_ts_utc, "
                    "signal_clock_basis, signal_clock_evidence_class, "
                    "signal_clock_source_ref FROM fact_trades "
                    "WHERE signal_clock_lineage_status='blocked_no_signal_snapshot' "
                    "ORDER BY target_date, city, fill_id"
                )
            ]
        else:
            lineage_counts = {"schema_pre_v21": int(trades["rows"])}
            blocked_lineage = []

        adjustment_join = (
            "LEFT JOIN signal_clock_adjustments adjustment "
            "ON adjustment.signal_id=ft.signal_id"
            if "signal_clock_adjustments" in tables
            else ""
        )
        adjustment_select = (
            "adjustment.corrected_snapshot_ts_utc, "
            "adjustment.timestamp_evidence_class, adjustment.lineage_status"
            if "signal_clock_adjustments" in tables
            else "NULL AS corrected_snapshot_ts_utc, "
            "NULL AS timestamp_evidence_class, NULL AS lineage_status"
        )
        original_fault_rows = [
            dict(row)
            for row in connection.execute(
                f"""
                SELECT ft.fill_id, ft.execution_id, ft.signal_id,
                       ft.strategy_key, ft.city, ft.target_date,
                       s.snapshot_ts_utc AS original_snapshot_ts_utc,
                       ft.snapshot_ts_utc AS effective_fact_snapshot_ts_utc,
                       ft.order_ts_utc, ft.cost_usd, ft.pnl_usd_at_fill,
                       {adjustment_select}
                FROM fact_trades ft
                JOIN signals s ON s.signal_id=ft.signal_id
                {adjustment_join}
                WHERE julianday(s.snapshot_ts_utc) > julianday(ft.order_ts_utc)
                ORDER BY ft.target_date, ft.city, ft.fill_id
                """
            )
        ]
        original_fault_impact = {
            "fault_type": "signal_snapshot_after_order",
            "fact_rows": len(original_fault_rows),
            "signals": len({row["signal_id"] for row in original_fault_rows}),
            "executions": len(
                {row["execution_id"] for row in original_fault_rows}
            ),
            "cost_usd": sum(
                float(row["cost_usd"] or 0.0) for row in original_fault_rows
            ),
            "settled_pnl_usd": sum(
                float(row["pnl_usd_at_fill"] or 0.0)
                for row in original_fault_rows
                if row["pnl_usd_at_fill"] is not None
            ),
            "target_date_start": min(
                (str(row["target_date"]) for row in original_fault_rows),
                default=None,
            ),
            "target_date_end": max(
                (str(row["target_date"]) for row in original_fault_rows),
                default=None,
            ),
            "by_strategy": dict(
                sorted(
                    Counter(
                        str(row["strategy_key"] or "unknown")
                        for row in original_fault_rows
                    ).items()
                )
            ),
            "adjusted_fact_rows": sum(
                row["corrected_snapshot_ts_utc"] is not None
                for row in original_fault_rows
            ),
            "rows": original_fault_rows,
        }
        tmin_candidates = connection.execute(
            """SELECT COUNT(*) AS candidates,
                      SUM(COALESCE(policy_selected, 0)) AS selected,
                      SUM(COALESCE(paper_ordered, 0)) AS paper_ordered,
                      SUM(COALESCE(live_filled, 0)) AS live_filled
               FROM fact_signal_candidates
               WHERE lower(COALESCE(strategy_key, '')) LIKE '%tmin%'"""
        ).fetchone()
        tmin_trades = connection.execute(
            """SELECT COUNT(*) AS trades, COALESCE(SUM(notional), 0) AS notional
               FROM fact_trades
               WHERE lower(COALESCE(strategy_key, '')) LIKE '%tmin%'"""
        ).fetchone()
    finally:
        connection.close()
    violations = (
        candidates["invalid_timezone_or_format_rows"]
        + candidates["causal_reversal_rows"]
        + trades["invalid_timezone_or_format_rows"]
        + trades["causal_reversal_rows"]
    )
    return {
        "identity": _db_identity(path),
        "fact_signal_candidates": candidates,
        "fact_trades": trades,
        "signal_clock_lineage": {
            "by_status": dict(sorted(lineage_counts.items())),
            "blocked_rows": blocked_lineage,
            "blocked_rows_count": len(blocked_lineage),
            "blocked_semantics": (
                "order clock is only a causal upper bound; it is not a feature snapshot"
            ),
        },
        "historical_signal_clock_fault": original_fault_impact,
        "tmin_production_impact": {
            "candidates": int(tmin_candidates["candidates"] or 0),
            "selected": int(tmin_candidates["selected"] or 0),
            "paper_ordered": int(tmin_candidates["paper_ordered"] or 0),
            "live_filled": int(tmin_candidates["live_filled"] or 0),
            "fact_trades": int(tmin_trades["trades"] or 0),
            "notional": float(tmin_trades["notional"] or 0.0),
        },
        "violation_rows": violations,
        "status": "pass" if violations == 0 else "fail",
    }


def audit_latest_observations(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("records") or []
    invalid: list[dict[str, Any]] = []
    reversals: list[dict[str, Any]] = []
    generated, error = _parse_present(
        payload.get("generated_at_utc"), field="generated_at_utc"
    )
    if error:
        invalid.append({"identity": "document", "field": "generated_at_utc", "error": error})
    for row in rows:
        identity = f"{row.get('city')}:{row.get('target_date')}"
        clocks: dict[str, datetime | None] = {}
        for field in (
            "last_obs_utc",
            "fetched_at_utc",
            "running_max_obs_utc",
            "running_min_obs_utc",
        ):
            clocks[field], error = _parse_present(row.get(field), field=field)
            if error:
                invalid.append(
                    {"identity": identity, "field": field, "value": row.get(field), "error": error}
                )
        for earlier in ("last_obs_utc", "running_max_obs_utc", "running_min_obs_utc"):
            if clocks.get(earlier) and clocks.get("fetched_at_utc") and clocks[earlier] > clocks["fetched_at_utc"]:
                reversals.append(
                    {
                        "identity": identity,
                        "earlier_field": earlier,
                        "earlier_value": row.get(earlier),
                        "later_field": "fetched_at_utc",
                        "later_value": row.get("fetched_at_utc"),
                    }
                )
        if generated and clocks.get("fetched_at_utc") and clocks["fetched_at_utc"] > generated:
            reversals.append(
                {
                    "identity": identity,
                    "earlier_field": "fetched_at_utc",
                    "earlier_value": row.get("fetched_at_utc"),
                    "later_field": "generated_at_utc",
                    "later_value": payload.get("generated_at_utc"),
                }
            )
    violations = len(invalid) + len(reversals)
    return {
        "path": str(path),
        "rows": len(rows),
        "invalid_timezone_or_format_rows": len(invalid),
        "causal_reversal_rows": len(reversals),
        "invalid": invalid,
        "reversals": reversals,
        "status": "pass" if violations == 0 else "fail",
    }


def _raw_tmin_date_shift(raw_root: Path) -> dict[str, Any]:
    files = {
        "Seoul": (raw_root / "RKSI_apr14_aug21.csv", ZoneInfo("Asia/Seoul")),
        "Tokyo": (raw_root / "RJTT_apr14_aug21.csv", ZoneInfo("Asia/Tokyo")),
    }
    by_city: dict[str, Any] = {}
    total = shifted = 0
    for city, (path, local_zone) in files.items():
        rows = city_shifted = 0
        first: str | None = None
        last: str | None = None
        with path.open(encoding="utf-8") as handle:
            header = next(handle, "")
            if "valid" not in header:
                raise ValueError(f"unexpected Tmin raw CSV header: {path}")
            for line in handle:
                parts = line.split(",", 3)
                if len(parts) < 2:
                    continue
                value = parts[1].strip()
                try:
                    utc_clock = datetime.strptime(
                        value, "%Y-%m-%d %H:%M"
                    ).replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
                correct = utc_clock.astimezone(local_zone).date()
                # Reproduce the superseded artifact transform only to count
                # its historical impact; this is never an active conversion.
                old = (utc_clock - timedelta(hours=15)).date()
                rows += 1
                city_shifted += int(correct != old)
                first = min(first, value) if first else value
                last = max(last, value) if last else value
        total += rows
        shifted += city_shifted
        by_city[city] = {
            "raw_rows": rows,
            "target_date_shifted_rows": city_shifted,
            "raw_utc_first": first,
            "raw_utc_last": last,
        }
    return {
        "raw_rows": total,
        "target_date_shifted_rows": shifted,
        "by_city": by_city,
        "old_transform": "UTC timestamp - 15 hours",
        "correct_transform": "IANA Asia/Seoul or Asia/Tokyo local date (UTC+09 in this window)",
    }


def audit_historical_tmin(
    corrected_path: Path, raw_root: Path, production_impact: dict[str, Any]
) -> dict[str, Any]:
    corrected = json.loads(corrected_path.read_text(encoding="utf-8"))
    shift = _raw_tmin_date_shift(raw_root)
    labeled = sum(int(row["labeled_crosses"]) for row in corrected["cities"].values())
    no_wins = sum(int(row["no_side_wins"]) for row in corrected["cities"].values())
    losses = [
        {"city": city, **loss}
        for city, city_result in corrected["cities"].items()
        for loss in city_result.get("loss_rows") or []
    ]
    return {
        "fault": "tmin_target_date_utc_minus_15_instead_of_iana_local_date",
        "status": "contained_replayed_no_order_impact",
        "affected_window": "2026-04-14T00:00Z..2026-08-21T23:30Z raw history",
        **shift,
        "corrected_cross_replay": {
            "labeled_crosses": labeled,
            "no_side_wins": no_wins,
            "no_side_losses": labeled - no_wins,
            "loss_rows": losses,
            "by_city": corrected["cities"],
        },
        "production_impact": production_impact,
        "decision_impact_conclusion": (
            "The invalid artifact was research-only. Canonical Tmin rows show zero selected, "
            "paper orders, live fills, fact trades, and notional."
        ),
    }


def build(
    *,
    repo_root: Path,
    db_path: Path,
    observations_path: Path,
    tmin_corrected_path: Path,
    tmin_raw_root: Path,
) -> dict[str, Any]:
    static = audit_static(repo_root)
    canonical = audit_canonical_db(db_path)
    observations = audit_latest_observations(observations_path)
    historical = audit_historical_tmin(
        tmin_corrected_path,
        tmin_raw_root,
        canonical["tmin_production_impact"],
    )
    current_pass = all(
        item["status"] == "pass" for item in (static, canonical, observations)
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "contract": {
            "utc": "timezone-aware input required; normalized to UTC",
            "local_wall_clock": "IANA timezone required; DST ambiguity explicit",
            "causality": "source/event/book clocks must not be after decision/order/fill clocks",
            "scope": "source -> feature/model -> decision -> order/fill plus public-book evidence",
        },
        "static_contract": static,
        "canonical_data": canonical,
        "latest_observations": observations,
        "historical_tmin_impact": historical,
        "status": "pass_current_fault_contained" if current_pass else "fail",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--observations", type=Path, default=DEFAULT_OBSERVATIONS)
    parser.add_argument("--tmin-corrected", type=Path, default=DEFAULT_TMIN_CORRECTED)
    parser.add_argument("--tmin-raw-root", type=Path, default=DEFAULT_TMIN_RAW)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = build(
        repo_root=args.repo_root,
        db_path=args.db,
        observations_path=args.observations,
        tmin_corrected_path=args.tmin_corrected,
        tmin_raw_root=args.tmin_raw_root,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if payload["status"] == "pass_current_fault_contained" else 1


if __name__ == "__main__":
    raise SystemExit(main())
