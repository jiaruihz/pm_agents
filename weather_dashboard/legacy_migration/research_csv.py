from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.strategies.weather_edge_v1.ids import (
    WeatherIdError,
    make_execution_id,
    make_fill_id,
    make_paper_order_id,
    make_plan_id,
    make_settlement_id,
    make_signal_id,
)
from weather_dashboard.contract import (
    CanonicalValidationError,
    validate_canonical_fill,
    validate_canonical_order,
    validate_canonical_plan,
    validate_canonical_settlement,
    validate_canonical_signal,
)
from weather_dashboard.ingest.canonical import (
    ingest_canonical_fills,
    ingest_canonical_orders,
    ingest_canonical_plans,
    ingest_canonical_settlements,
    ingest_canonical_signals,
    insert_code_version,
    insert_run,
    insert_strategy_config,
    insert_universe,
)


DEFAULT_RESEARCH_INPUTS = (
    (
        "runtime/weather_edge_v1/market_data/research/t24_paper_snapshot_replay_trades.csv",
        "snapshot_replay",
        "legacy_research_snapshot_replay",
    ),
    (
        "runtime/weather_edge_v1/market_data/research/t24_paper_ledger_trades.csv",
        "paper",
        "legacy_research_paper",
    ),
)


@dataclass
class MigrationReport:
    source_path: str
    run_id: str
    execution_mode: str
    input_rows: int = 0
    signals: int = 0
    plans: int = 0
    orders: int = 0
    fills: int = 0
    settlements: int = 0
    skipped_rows: int = 0
    skipped_reasons: dict[str, int] = field(default_factory=dict)
    legacy_field_uses: dict[str, int] = field(default_factory=dict)
    generated_ids: dict[str, int] = field(default_factory=dict)

    def skip(self, reason: str) -> None:
        self.skipped_rows += 1
        self.skipped_reasons[reason] = self.skipped_reasons.get(reason, 0) + 1

    def legacy(self, field_name: str) -> None:
        self.legacy_field_uses[field_name] = self.legacy_field_uses.get(field_name, 0) + 1

    def generated(self, id_name: str) -> None:
        self.generated_ids[id_name] = self.generated_ids.get(id_name, 0) + 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_path": self.source_path,
            "run_id": self.run_id,
            "execution_mode": self.execution_mode,
            "input_rows": self.input_rows,
            "inserted": {
                "signals": self.signals,
                "plans": self.plans,
                "orders": self.orders,
                "fills": self.fills,
                "settlements": self.settlements,
            },
            "skipped_rows": self.skipped_rows,
            "skipped_reasons": self.skipped_reasons,
            "legacy_field_uses": self.legacy_field_uses,
            "generated_ids": self.generated_ids,
        }


def _get(row: dict[str, str], report: MigrationReport, canonical: str, *legacy: str) -> str:
    value = (row.get(canonical) or "").strip()
    if value:
        return value
    for field in legacy:
        value = (row.get(field) or "").strip()
        if value:
            report.legacy(field)
            return value
    return ""


def _float(value: Any, field_name: str) -> float:
    if value is None or str(value).strip() == "":
        raise ValueError(f"missing numeric field: {field_name}")
    return float(value)


def _side_pair(raw_side: str) -> tuple[str, str]:
    side = raw_side.strip().upper()
    if side == "BUY_YES":
        return "YES", "BUY_YES"
    if side == "BUY_NO":
        return "NO", "BUY_NO"
    raise ValueError(f"unsupported side: {raw_side!r}")


def _final_price(row: dict[str, str], report: MigrationReport) -> float | None:
    raw = _get(row, report, "final_price", "final_yes")
    if not raw:
        return None
    value = float(raw)
    if value in (0.0, 1.0):
        return value
    return value


def _source_run_id(row: dict[str, str], source_path: Path) -> str:
    return (row.get("source_run_id") or row.get("snapshot_file") or source_path.stem).strip()


def _build_rows(
    raw: dict[str, str],
    *,
    source_path: Path,
    run_id: str,
    config_id: str,
    execution_mode: str,
    report: MigrationReport,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any] | None]:
    target_date = _get(raw, report, "target_date", "event_date")
    model_version = _get(raw, report, "model_version", "model")
    model_p_yes = _float(_get(raw, report, "model_p_yes", "model_prob", "model_probability_yes"), "model_p_yes")
    market_price = _float(_get(raw, report, "market_price", "market_yes_price"), "market_price")
    signal_side, order_side = _side_pair(_get(raw, report, "side", "signal_side"))
    forecast_source = _get(raw, report, "forecast_source", "profile")
    condition_id = _get(raw, report, "condition_id")
    market_id = _get(raw, report, "market_id")
    bracket = _get(raw, report, "bracket")
    snapshot_ts_utc = _get(raw, report, "snapshot_ts_utc")

    signal_id = _get(raw, report, "signal_id")
    if not signal_id:
        signal_id = make_signal_id(
            target_date=target_date,
            city=_get(raw, report, "city"),
            bracket=bracket,
            signal_side=signal_side,
            model_version=model_version,
            forecast_source=forecast_source,
            snapshot_ts_utc=snapshot_ts_utc,
            condition_id=condition_id,
        )
        report.generated("signal_id")

    execution_policy = _get(raw, report, "execution_policy") or "legacy_research_csv"
    plan_id = _get(raw, report, "plan_id")
    if not plan_id:
        plan_id = make_plan_id(
            run_id=run_id,
            signal_id=signal_id,
            order_side=order_side,
            execution_policy=execution_policy,
        )
        report.generated("plan_id")

    venue = "paper" if execution_mode == "paper" else "snapshot_replay"
    execution_id = _get(raw, report, "execution_id")
    if not execution_id:
        execution_id = make_execution_id(
            run_id=run_id,
            plan_id=plan_id,
            venue=venue,
            attempt_index=0,
        )
        report.generated("execution_id")

    order_id = _get(raw, report, "order_id")
    if not order_id:
        order_id = make_paper_order_id(execution_id=execution_id)
        report.generated("order_id")

    shares = _float(_get(raw, report, "shares"), "shares")
    entry_price = _float(_get(raw, report, "entry_price"), "entry_price")
    cost_usd = _float(_get(raw, report, "cost_usd"), "cost_usd")
    created_at_utc = _get(raw, report, "created_at_utc", "snapshot_ts_utc") or snapshot_ts_utc

    signal = {
        "signal_id": signal_id,
        "producer_system": "legacy_migration",
        "producer_run_id": _source_run_id(raw, source_path),
        "snapshot_ts_utc": snapshot_ts_utc,
        "snapshot_file": _get(raw, report, "snapshot_file"),
        "target_date": target_date,
        "city": _get(raw, report, "city"),
        "city_pool": _get(raw, report, "city_pool"),
        "icao": _get(raw, report, "icao"),
        "bracket": bracket,
        "unit": _get(raw, report, "unit"),
        "signal_side": signal_side,
        "model_version": model_version,
        "model_p_yes": model_p_yes,
        "forecast_source": forecast_source,
        "market_price": market_price,
        "edge": _float(_get(raw, report, "edge"), "edge"),
        "abs_edge": _float(_get(raw, report, "abs_edge"), "abs_edge"),
        "condition_id": condition_id,
        "market_id": market_id,
        "token_id": _get(raw, report, "token_id") or None,
        "hours_to_settle": _float(_get(raw, report, "hours_to_settle"), "hours_to_settle"),
    }
    plan = {
        "plan_id": plan_id,
        "run_id": run_id,
        "signal_id": signal_id,
        "config_id": config_id,
        "order_side": order_side,
        "notional": cost_usd,
        "desired_shares": shares,
        "sizing_mode": _get(raw, report, "sizing_mode") or "legacy_cost",
        "entry_price_window": _get(raw, report, "entry_price_window") or None,
        "execution_policy": execution_policy,
        "limit_price": entry_price,
        "skip_reason": None,
        "status": "accepted",
    }
    order = {
        "execution_id": execution_id,
        "order_id": order_id,
        "run_id": run_id,
        "plan_id": plan_id,
        "venue": venue,
        "order_side": order_side,
        "limit_price": entry_price,
        "entry_price": entry_price,
        "shares": shares,
        "cost_usd": cost_usd,
        "notional": cost_usd,
        "status": "filled",
        "exchange_response": None,
        "placed_at_utc": created_at_utc,
    }
    fill = {
        "fill_id": make_fill_id(execution_id=execution_id),
        "execution_id": execution_id,
        "order_id": order_id,
        "filled_shares": shares,
        "filled_price": entry_price,
        "fees_usd": 0.0,
        "status": "filled",
        "filled_at_utc": created_at_utc,
    }

    settlement = None
    settlement_status = _get(raw, report, "settlement_status")
    final_price = _final_price(raw, report)
    if settlement_status == "settled" and final_price is not None:
        settlement = {
            "settlement_id": make_settlement_id(
                target_date=target_date,
                condition_id=condition_id,
                market_id=market_id,
                bracket=bracket,
            ),
            "target_date": target_date,
            "condition_id": condition_id,
            "market_id": market_id,
            "bracket": bracket,
            "token_id": _get(raw, report, "token_id") or None,
            "final_price": final_price,
            "settlement_status": settlement_status,
        }

    validate_canonical_signal(signal)
    validate_canonical_plan(plan)
    validate_canonical_order(order)
    validate_canonical_fill(fill)
    if settlement is not None:
        validate_canonical_settlement(settlement)

    return signal, plan, order, fill, settlement


def _run_id_for(source_path: Path, execution_mode: str, run_name: str) -> str:
    return f"legacy_{execution_mode}_{run_name}_{source_path.stem}"


def migrate_research_csv(
    conn,
    *,
    csv_path: str | Path,
    execution_mode: str,
    run_name: str,
) -> MigrationReport:
    source_path = Path(csv_path)
    run_id = _run_id_for(source_path, execution_mode, run_name)
    config_id = "legacy_research_weather_edge"
    report = MigrationReport(str(source_path), run_id, execution_mode)

    if not source_path.exists():
        report.skip("source_missing")
        return report

    signals: list[dict[str, Any]] = []
    plans: list[dict[str, Any]] = []
    orders: list[dict[str, Any]] = []
    fills: list[dict[str, Any]] = []
    settlements: list[dict[str, Any]] = []

    with source_path.open(newline="", encoding="utf-8") as fh:
        for raw in csv.DictReader(fh):
            report.input_rows += 1
            try:
                signal, plan, order, fill, settlement = _build_rows(
                    raw,
                    source_path=source_path,
                    run_id=run_id,
                    config_id=config_id,
                    execution_mode=execution_mode,
                    report=report,
                )
            except (ValueError, WeatherIdError, CanonicalValidationError) as exc:
                report.skip(str(exc))
                continue

            signals.append(signal)
            plans.append(plan)
            orders.append(order)
            fills.append(fill)
            if settlement is not None:
                settlements.append(settlement)

    cities = sorted({row["city"] for row in signals})
    models = sorted({row["model_version"] for row in signals})
    insert_strategy_config(conn, config_id, "legacy_research_weather_edge", {"source": "legacy_research_csv"})
    insert_universe(conn, f"{run_id}_universe", run_name, cities=cities, models=models)
    insert_code_version(conn, "legacy-migration")
    insert_run(
        conn,
        {
            "run_id": run_id,
            "producer_system": "legacy_migration",
            "producer_run_id": source_path.stem,
            "config_id": config_id,
            "universe_id": f"{run_id}_universe",
            "code_version": "legacy-migration",
            "execution_mode": execution_mode,
            "date_range_start": min((row["target_date"] for row in signals), default=None),
            "date_range_end": max((row["target_date"] for row in signals), default=None),
            "started_at_utc": datetime.now(timezone.utc).isoformat(),
            "state": "paper" if execution_mode == "paper" else "explore",
            "source_root": str(source_path.parent),
            "repro_key": f"legacy:{source_path.name}",
            "tags": ["legacy_migration", run_name],
            "metrics": None,
            "notes": f"Migrated from {source_path}",
        },
    )

    report.signals = ingest_canonical_signals(conn, signals, str(source_path))
    report.plans = ingest_canonical_plans(conn, plans, str(source_path))
    report.orders = ingest_canonical_orders(conn, orders, str(source_path))
    report.fills = ingest_canonical_fills(conn, fills, str(source_path))
    report.settlements = ingest_canonical_settlements(conn, settlements, str(source_path))
    return report


def write_report(reports: list[MigrationReport], report_dir: str | Path) -> Path:
    out_dir = Path(report_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = out_dir / f"legacy_research_migration_{ts}.json"
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "reports": [report.as_dict() for report in reports],
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return out_path
