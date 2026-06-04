from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any, Iterable

from src.strategies.weather_edge_v1.ids import make_fill_id, make_paper_order_id, make_plan_id, make_signal_id
from weather_dashboard.contract import CanonicalValidationError
from weather_dashboard.ingest.canonical import (
    ingest_canonical_fills,
    ingest_canonical_orders,
    ingest_canonical_plans,
    ingest_canonical_signals,
    insert_code_version,
    insert_run,
    insert_strategy_config,
    insert_universe,
)


CITY_ICAO = {
    "Amsterdam": "EHAM",
    "Ankara": "LTAC",
    "Atlanta": "KATL",
    "Austin": "KAUS",
    "Beijing": "ZBAA",
    "BuenosAires": "SAEZ",
    "Busan": "RKPK",
    "CapeTown": "FACT",
    "Chengdu": "ZUUU",
    "Chicago": "KMDW",
    "Chongqing": "ZUCK",
    "Dallas": "KDAL",
    "Denver": "KBKF",
    "Guangzhou": "ZGGG",
    "Helsinki": "EFHK",
    "HongKong": "VHHH",
    "Houston": "KHOU",
    "Istanbul": "LTFM",
    "Jakarta": "WIII",
    "Jeddah": "OEJN",
    "Karachi": "OPKC",
    "KualaLumpur": "WMSA",
    "LA": "KLAX",
    "Lagos": "DNMM",
    "London": "EGLL",
    "Lucknow": "VILK",
    "Madrid": "LEMD",
    "Manila": "RPLL",
    "Miami": "KMIA",
    "Milan": "LIML",
    "Moscow": "UUWW",
    "Munich": "EDDM",
    "NYC": "KLGA",
    "PanamaCity": "MPTO",
    "Paris": "LFPG",
    "SanFrancisco": "KSFO",
    "SaoPaulo": "SBGR",
    "Seattle": "KSEA",
    "Seoul": "RKSI",
    "Shanghai": "ZSPD",
    "Shenzhen": "ZGSZ",
    "Singapore": "WSSS",
    "Taipei": "RCSS",
    "TelAviv": "LLBG",
    "Tokyo": "RJTT",
    "Warsaw": "EPWA",
    "Wellington": "NZWN",
    "Wuhan": "ZHHH",
}


@dataclass
class LiveCycleMigrationReport:
    source_path: str
    run_id: str
    producer_system: str
    input_signals: int = 0
    input_plans: int = 0
    input_orders: int = 0
    signals: int = 0
    plans: int = 0
    orders: int = 0
    fills: int = 0
    skipped_rows: int = 0
    skipped_reasons: dict[str, int] = field(default_factory=dict)

    def skip(self, reason: str) -> None:
        self.skipped_rows += 1
        self.skipped_reasons[reason] = self.skipped_reasons.get(reason, 0) + 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_path": self.source_path,
            "run_id": self.run_id,
            "producer_system": self.producer_system,
            "input": {
                "signals": self.input_signals,
                "plans": self.input_plans,
                "orders": self.input_orders,
            },
            "inserted": {
                "signals": self.signals,
                "plans": self.plans,
                "orders": self.orders,
                "fills": self.fills,
            },
            "skipped_rows": self.skipped_rows,
            "skipped_reasons": self.skipped_reasons,
        }


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _json_text(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _float(value: Any, default: float | None = None) -> float | None:
    if value is None or str(value).strip() == "":
        return default
    return float(value)


def _condition_id(row: dict[str, Any]) -> str:
    raw = str(row.get("condition_id") or "").strip()
    if raw:
        return raw
    source_id = str(row.get("source_id") or "").strip()
    if "|" in source_id:
        candidate = source_id.split("|", 1)[0].strip()
        if candidate:
            return candidate
    return ""


def _signal_side(row: dict[str, Any]) -> str:
    raw = str(row.get("signal_side") or row.get("side") or "").strip().upper()
    if raw in {"BUY_YES", "YES"}:
        return "YES"
    if raw in {"BUY_NO", "NO"}:
        return "NO"
    raise ValueError(f"unsupported signal_side: {raw!r}")


def _order_side(row: dict[str, Any]) -> str:
    raw = str(row.get("order_side") or "").strip().upper()
    if raw in {"BUY_YES", "BUY_NO"}:
        return raw
    side = _signal_side(row)
    return f"BUY_{side}"


def _snapshot_ts(row: dict[str, Any]) -> str:
    return str(
        row.get("snapshot_ts_utc")
        or row.get("snapshot_fetched_at_utc")
        or row.get("imported_at_utc")
        or row.get("created_at_utc")
        or ""
    ).strip()


def _parse_dt(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _hours_to_settle(row: dict[str, Any]) -> float:
    raw = _float(row.get("hours_to_settle"))
    if raw is not None:
        return raw
    snapshot_dt = _parse_dt(_snapshot_ts(row))
    target_date = str(row.get("target_date") or "").strip()
    if snapshot_dt and target_date:
        settle_dt = datetime.combine(
            datetime.fromisoformat(target_date).date(),
            time(hour=23, minute=59, second=59),
            tzinfo=timezone.utc,
        )
        return round(max((settle_dt - snapshot_dt).total_seconds() / 3600, 0.0), 4)
    return 0.0


def _run_id(cycle_id: str, producer_system: str) -> str:
    return f"{producer_system}_live_{cycle_id}"


def _producer_system(cycle_path: Path) -> str:
    return "n100" if "remote_pm_agent" in cycle_path.parts else "pm_agent_local"


def _local_path_from_summary(root: Path, raw_path: str | None, subdir: str, fallback: Path) -> Path:
    if not raw_path:
        return fallback
    name = Path(raw_path).name
    return root / subdir / name


def _cycle_paths(cycle_path: Path, cycle_id: str, summary: dict[str, Any] | None = None) -> tuple[Path, Path, Path, Path]:
    root = cycle_path.parent.parent
    paths = (summary or {}).get("paths") or {}
    signal_fallback = root / "signals" / f"live_{cycle_id}_signals.jsonl"
    plan_fallback = root / "plans" / f"live_{cycle_id}_trade_plans.jsonl"
    live_fallback = root / "live" / f"live_{cycle_id}_orders.jsonl"
    paper_fallback = root / "paper" / f"live_{cycle_id}_paper_orders.jsonl"
    return (
        _local_path_from_summary(root, paths.get("signal"), "signals", signal_fallback),
        _local_path_from_summary(root, paths.get("plan"), "plans", plan_fallback),
        _local_path_from_summary(root, paths.get("live"), "live", live_fallback),
        _local_path_from_summary(root, paths.get("paper"), "paper", paper_fallback),
    )


def _strategy_params(summary: dict[str, Any], raw_plans: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the canonical strategy-config params blob from a live-cycle summary + plans.

    Design rules:
    - Every field that affects trading behaviour must be included so that two configs
      with different parameters always produce different config_id hashes.
    - Fields that are purely administrative (paper_enabled, live_enabled, source)
      are included for observability but don't meaningfully distinguish strategies.
    - execution_policy is a top-level field (not aliased to algorithm_version).
    - maker_queue_v1 params are only included when execution_policy == "maker_queue_v1"
      so that existing mid_price_core_v1 config_ids are not invalidated.
    """
    config = dict(summary.get("config") or {})
    first_plan = raw_plans[0] if raw_plans else {}
    execution_policy = str(
        first_plan.get("execution_policy")
        or first_plan.get("combo")
        or config.get("execution_policy")
        or "mid_price_core_v1"
    ).strip() or "mid_price_core_v1"

    params: dict[str, Any] = {
        "strategy_family": "weather_edge_v1",
        "algorithm_version": "mid_price_core_v1",         # base signal/sizing algorithm (stable)
        "execution_policy": execution_policy,              # order-placement policy
        "signal_builder_version": "weather_snapshot_signal_builder",
        "trade_planner_version": "weather_trade_planner",
        "city_pool": config.get("city_pool") or first_plan.get("city_pool") or "t1_trading",
        "universe_scope": config.get("universe_scope") or "configured_live_pool",
        "sizing_mode": config.get("sizing_mode") or first_plan.get("sizing_mode") or "notional",
        "max_order_notional": _float(config.get("max_order_notional"), _float(first_plan.get("notional"))),
        "fixed_order_shares": _float(config.get("fixed_order_shares"), _float(first_plan.get("fixed_order_shares"))),
        "max_order_shares": _float(config.get("max_order_shares"), _float(first_plan.get("max_order_shares"))),
        "min_edge": _float(config.get("min_edge"), _float(first_plan.get("min_edge"))),
        "min_entry_price": _float(config.get("min_entry_price"), _float(first_plan.get("entry_price_min"))),
        "max_entry_price": _float(config.get("max_entry_price"), _float(first_plan.get("entry_price_max"))),
        "entry_price_window": first_plan.get("entry_price_window") or (
            f"{config.get('min_entry_price')}-{config.get('max_entry_price')}"
            if config.get("min_entry_price") is not None and config.get("max_entry_price") is not None
            else "full_range"
        ),
        "paper_enabled": bool(first_plan.get("paper_enabled", True)),
        "live_enabled": bool(first_plan.get("live_enabled", False)),
        "source": "live_cycle",
    }

    # maker_queue_v1 adds order-book-aware params to the config fingerprint.
    # These are only included for maker_queue_v1 so existing mid_price_core_v1
    # config_ids are not invalidated.
    if execution_policy == "maker_queue_v1":
        params["tick_size"] = _float(
            first_plan.get("tick_size"), _float(config.get("tick_size"), 0.01)
        )
        params["min_quote_edge"] = _float(
            first_plan.get("min_quote_edge"), _float(config.get("min_quote_edge"), 0.03)
        )
        params["max_quote_spread"] = _float(
            first_plan.get("max_quote_spread"), _float(config.get("max_quote_spread"), 0.12)
        )
        params["max_mid_drift"] = _float(
            first_plan.get("max_mid_drift"), _float(config.get("max_mid_drift"), 0.10)
        )
        params["quote_improvement_ticks"] = int(
            first_plan.get("quote_improvement_ticks") or config.get("quote_improvement_ticks") or 1
        )
        params["wide_spread_shade_ticks"] = int(
            first_plan.get("wide_spread_shade_ticks") or config.get("wide_spread_shade_ticks") or 1
        )
        params["narrow_quote_spread"] = _float(
            first_plan.get("narrow_quote_spread"), _float(config.get("narrow_quote_spread"), 0.03)
        )
        params["adverse_selection_spread_fraction"] = _float(
            first_plan.get("adverse_selection_spread_fraction"),
            _float(config.get("adverse_selection_spread_fraction"), 0.50),
        )

    return params


# Fields that actually identify a strategy. Anything not in this list is
# observability or runtime toggle and must NOT affect config_id, otherwise
# one logical strategy gets fragmented into multiple config rows (see
# docs/WEATHER_DATA_PIPELINE.md "unified PnL caliber").
_STRATEGY_IDENTITY_FIELDS: tuple[str, ...] = (
    "strategy_family",
    "algorithm_version",
    "execution_policy",
    "signal_builder_version",
    "trade_planner_version",
    "city_pool",
    "universe_scope",
    "sizing_mode",
    "max_order_notional",
    "fixed_order_shares",
    "min_edge",
    "min_entry_price",
    "max_entry_price",
    "entry_price_window",
)
_MAKER_QUEUE_IDENTITY_FIELDS: tuple[str, ...] = (
    "tick_size",
    "min_quote_edge",
    "max_quote_spread",
    "max_mid_drift",
    "quote_improvement_ticks",
    "wide_spread_shade_ticks",
    "narrow_quote_spread",
    "adverse_selection_spread_fraction",
)
# Explicitly excluded from identity: paper_enabled, live_enabled, source
# (runtime/observability) and max_order_shares (per-order safety cap, not
# strategy intent — was added later and would have fragmented old configs).


# Default-equivalence map: a missing or empty value in any of these identity
# fields hashes the same as the explicit default. Without this, old records
# (where the field hadn't been added to the dict yet) and new records (where
# the fallback fills it in) would collide on intent but split on hash.
_IDENTITY_DEFAULTS: dict[str, Any] = {
    "strategy_family": "weather_edge_v1",
    "algorithm_version": "mid_price_core_v1",
    "execution_policy": "mid_price_core_v1",
    "signal_builder_version": "weather_snapshot_signal_builder",
    "trade_planner_version": "weather_trade_planner",
    "sizing_mode": "notional",
    "universe_scope": "configured_live_pool",
}


def _strategy_identity(params: dict[str, Any]) -> dict[str, Any]:
    fields = list(_STRATEGY_IDENTITY_FIELDS)
    if params.get("execution_policy") == "maker_queue_v1":
        fields.extend(_MAKER_QUEUE_IDENTITY_FIELDS)
    identity: dict[str, Any] = {}
    for k in fields:
        v = params.get(k)
        if v in (None, ""):
            v = _IDENTITY_DEFAULTS.get(k)
        identity[k] = v
    return identity


def _strategy_config_id(params: dict[str, Any]) -> str:
    identity = _strategy_identity(params)
    payload = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"live_weather_edge_v1_{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:12]}"


def _strategy_config_name(params: dict[str, Any]) -> str:
    pool = params.get("city_pool") or "pool"
    mode = params.get("sizing_mode") or "sizing"
    notional = params.get("max_order_notional")
    shares = params.get("fixed_order_shares")
    window = params.get("entry_price_window") or "range"
    # Use execution_policy for display; fall back to algorithm_version for legacy records.
    policy = params.get("execution_policy") or params.get("algorithm_version") or "algo"
    name = f"{pool}_{policy}_{mode}_${notional}_shares_{shares}_entry_{window}"
    if params.get("execution_policy") == "maker_queue_v1":
        mqe = params.get("min_quote_edge")
        name += f"_mqe_{mqe}"
    return name


def _canonical_signal(raw: dict[str, Any], *, producer_system: str, cycle_id: str) -> dict[str, Any]:
    model_p_yes = _float(raw.get("model_p_yes"), _float(raw.get("model_probability_yes")))
    if model_p_yes is None:
        raise ValueError("missing model_p_yes")
    market_price = _float(raw.get("market_price"))
    if market_price is None:
        raise ValueError("missing market_price")
    city = str(raw.get("city") or "").strip()
    signal_side = _signal_side(raw)
    condition_id = _condition_id(raw)
    snapshot_ts_utc = _snapshot_ts(raw)
    signal_id = make_signal_id(
        target_date=str(raw.get("target_date") or "").strip(),
        city=city,
        bracket=str(raw.get("bracket") or "").strip(),
        signal_side=signal_side,
        model_version=str(raw.get("model_version") or "").strip(),
        forecast_source=str(raw.get("forecast_source") or raw.get("profile") or "").strip(),
        snapshot_ts_utc=snapshot_ts_utc,
        condition_id=condition_id,
    )
    return {
        "signal_id": signal_id,
        "producer_system": producer_system,
        "producer_run_id": str(raw.get("source_run_id") or cycle_id).strip(),
        "snapshot_ts_utc": snapshot_ts_utc,
        "snapshot_file": str(raw.get("source_run_id") or "").strip() or None,
        "target_date": str(raw.get("target_date") or "").strip(),
        "city": city,
        "city_pool": str(raw.get("city_pool") or "t1_trading").strip(),
        "icao": str(raw.get("icao") or CITY_ICAO.get(city, "")).strip(),
        "bracket": str(raw.get("bracket") or "").strip(),
        "unit": str(raw.get("unit") or "F").strip(),
        "signal_side": signal_side,
        "model_version": str(raw.get("model_version") or "").strip(),
        "model_p_yes": model_p_yes,
        "forecast_source": str(raw.get("forecast_source") or raw.get("profile") or "").strip(),
        "market_price": market_price,
        "edge": _float(raw.get("edge"), 0.0),
        "abs_edge": abs(_float(raw.get("edge"), 0.0) or 0.0),
        "condition_id": condition_id,
        "market_id": str(raw.get("market_id") or "").strip(),
        "token_id": str(raw.get("token_id") or "").strip() or None,
        "hours_to_settle": _hours_to_settle(raw),
    }


def _canonical_plan(
    raw: dict[str, Any],
    *,
    run_id: str,
    config_id: str,
    signal_id: str,
) -> dict[str, Any]:
    shares = _float(raw.get("desired_shares"), _float(raw.get("size")))
    notional = _float(raw.get("notional"), _float(raw.get("posted_notional")))
    limit_price = _float(raw.get("limit_price"), _float(raw.get("market_price")))
    order_side = _order_side(raw)
    execution_policy = str(raw.get("execution_policy") or raw.get("combo") or "live_cycle").strip()
    plan_id = make_plan_id(
        run_id=run_id,
        signal_id=signal_id,
        order_side=order_side,
        execution_policy=execution_policy,
    )
    return {
        "plan_id": plan_id,
        "run_id": run_id,
        "signal_id": signal_id,
        "config_id": config_id,
        "order_side": order_side,
        "notional": notional,
        "desired_shares": shares,
        "sizing_mode": str(raw.get("sizing_mode") or "").strip() or None,
        "entry_price_window": str(raw.get("entry_price_window") or "").strip() or None,
        "execution_policy": execution_policy,
        "limit_price": limit_price,
        "skip_reason": str(raw.get("risk_reason") or "").strip() or None,
        "status": str(raw.get("status") or raw.get("risk_status") or "").strip() or None,
    }


def _canonical_order(raw: dict[str, Any], *, run_id: str, plan_id: str) -> dict[str, Any]:
    venue = str(raw.get("venue") or "paper").strip()
    entry_price = _float(raw.get("posted_price"), _float(raw.get("limit_price"), _float(raw.get("market_price"))))
    shares = _float(raw.get("shares"), _float(raw.get("size")))
    cost_usd = _float(raw.get("cost_usd"), _float(raw.get("posted_notional"), _float(raw.get("notional"))))
    exchange_response = raw.get("exchange_response")
    order_id = str(raw.get("order_id") or "").strip()
    if not order_id and isinstance(exchange_response, dict):
        place = exchange_response.get("place")
        if isinstance(place, dict):
            order_id = str(place.get("orderID") or "").strip()
    execution_id = str(raw.get("execution_id") or "").strip()
    if not order_id and venue == "paper" and execution_id:
        order_id = make_paper_order_id(execution_id=execution_id)
    return {
        "execution_id": execution_id,
        "order_id": order_id or execution_id,
        "run_id": run_id,
        "plan_id": plan_id,
        "venue": venue,
        "order_side": _order_side(raw),
        "limit_price": _float(raw.get("limit_price"), entry_price),
        "entry_price": entry_price,
        "shares": shares,
        "cost_usd": cost_usd if cost_usd is not None else ((shares or 0.0) * (entry_price or 0.0)),
        "notional": _float(raw.get("notional"), _float(raw.get("posted_notional"))),
        "status": str(raw.get("status") or "").strip() or "submitted",
        "exchange_response": _json_text(exchange_response),
        "placed_at_utc": str(raw.get("placed_at_utc") or raw.get("created_at_utc") or "").strip() or None,
    }


def _canonical_fill(order: dict[str, Any]) -> dict[str, Any]:
    return {
        "fill_id": make_fill_id(execution_id=order["execution_id"]),
        "execution_id": order["execution_id"],
        "order_id": order["order_id"],
        "filled_shares": order["shares"],
        "filled_price": order["entry_price"],
        "fees_usd": 0.0,
        "status": "simulated",
        "filled_at_utc": order["placed_at_utc"],
    }


def _insert_artifact(conn, *, run_id: str, kind: str, path: Path, row_count: int) -> None:
    if not path.exists():
        return
    artifact_id = f"{run_id}:{kind}"
    conn.execute(
        """
        INSERT OR IGNORE INTO run_artifacts (artifact_id, run_id, artifact_kind, source_path, row_count)
        VALUES (?, ?, ?, ?, ?)
        """,
        (artifact_id, run_id, kind, str(path), row_count),
    )
    conn.commit()


def migrate_live_cycle(conn, *, cycle_path: str | Path) -> LiveCycleMigrationReport:
    cycle_path = Path(cycle_path)
    cycle_id = cycle_path.stem
    producer_system = _producer_system(cycle_path)
    run_id = _run_id(cycle_id, producer_system)
    report = LiveCycleMigrationReport(str(cycle_path), run_id, producer_system)

    if not cycle_path.exists():
        report.skip("source_missing")
        return report

    try:
        summary = json.loads(cycle_path.read_text(encoding="utf-8"))
    except Exception:
        summary = {}

    signal_path, plan_path, live_order_path, paper_order_path = _cycle_paths(cycle_path, cycle_id, summary)
    raw_signals = _read_jsonl(signal_path)
    raw_plans = _read_jsonl(plan_path)
    raw_orders = [* _read_jsonl(live_order_path), * _read_jsonl(paper_order_path)]
    strategy_params = _strategy_params(summary, raw_plans)
    config_id = _strategy_config_id(strategy_params)
    report.input_signals = len(raw_signals)
    report.input_plans = len(raw_plans)
    report.input_orders = len(raw_orders)

    signals = []
    plans = []
    orders = []
    fills = []

    signal_id_map: dict[str, str] = {}
    plan_id_map: dict[str, str] = {}

    for raw in raw_signals:
        try:
            signal = _canonical_signal(raw, producer_system=producer_system, cycle_id=cycle_id)
            signals.append(signal)
            legacy_signal_id = str(raw.get("signal_id") or "").strip()
            if legacy_signal_id:
                signal_id_map[legacy_signal_id] = signal["signal_id"]
        except (ValueError, CanonicalValidationError) as exc:
            report.skip(f"signal:{exc}")

    signal_ids = {row["signal_id"] for row in signals}
    for raw in raw_plans:
        signal_id = signal_id_map.get(str(raw.get("signal_id") or "").strip())
        if signal_id not in signal_ids:
            report.skip("plan:missing_signal")
            continue
        try:
            plan = _canonical_plan(raw, run_id=run_id, config_id=config_id, signal_id=signal_id)
            plans.append(plan)
            legacy_plan_id = str(raw.get("plan_id") or "").strip()
            if legacy_plan_id:
                plan_id_map[legacy_plan_id] = plan["plan_id"]
        except (ValueError, CanonicalValidationError) as exc:
            report.skip(f"plan:{exc}")

    plan_ids = {row["plan_id"] for row in plans}
    for raw in raw_orders:
        plan_id = plan_id_map.get(str(raw.get("plan_id") or "").strip())
        if plan_id not in plan_ids:
            report.skip("order:missing_plan")
            continue
        try:
            order = _canonical_order(raw, run_id=run_id, plan_id=plan_id)
            orders.append(order)
            if order["venue"] == "paper":
                fills.append(_canonical_fill(order))
        except (ValueError, CanonicalValidationError) as exc:
            report.skip(f"order:{exc}")

    cities = sorted({row["city"] for row in signals})
    models = sorted({row["model_version"] for row in signals})
    started = min((_snapshot_ts(row) for row in raw_signals if _snapshot_ts(row)), default=None)
    ended = max((str(row.get("created_at_utc") or "") for row in raw_orders if row.get("created_at_utc")), default=started)

    insert_strategy_config(conn, config_id, _strategy_config_name(strategy_params), strategy_params)
    insert_universe(conn, f"{run_id}_universe", f"live_cycle_{producer_system}_{cycle_id}", cities=cities, models=models)
    insert_code_version(conn, "live-cycle-migration")
    insert_run(
        conn,
        {
            "run_id": run_id,
            "producer_system": producer_system,
            "producer_run_id": cycle_id,
            "config_id": config_id,
            "universe_id": f"{run_id}_universe",
            "code_version": "live-cycle-migration",
            "execution_mode": "live",
            "date_range_start": min((row["target_date"] for row in signals), default=None),
            "date_range_end": max((row["target_date"] for row in signals), default=None),
            "started_at_utc": started,
            "ended_at_utc": ended,
            "state": "live",
            "source_root": str(cycle_path.parent.parent),
            "repro_key": f"live_cycle:{cycle_id}:{producer_system}",
            "tags": ["live_cycle", producer_system],
            "metrics": None,
            "notes": f"Migrated from {cycle_path}",
        },
    )

    report.signals = ingest_canonical_signals(conn, signals, str(signal_path))
    report.plans = ingest_canonical_plans(conn, plans, str(plan_path))
    report.orders = ingest_canonical_orders(conn, orders, f"{live_order_path};{paper_order_path}")
    report.fills = ingest_canonical_fills(conn, fills, str(paper_order_path))
    _insert_artifact(conn, run_id=run_id, kind="live_cycle_summary", path=cycle_path, row_count=1)
    _insert_artifact(conn, run_id=run_id, kind="signals_jsonl", path=signal_path, row_count=len(raw_signals))
    _insert_artifact(conn, run_id=run_id, kind="plans_jsonl", path=plan_path, row_count=len(raw_plans))
    _insert_artifact(conn, run_id=run_id, kind="live_orders_jsonl", path=live_order_path, row_count=len(_read_jsonl(live_order_path)))
    _insert_artifact(conn, run_id=run_id, kind="paper_orders_jsonl", path=paper_order_path, row_count=len(_read_jsonl(paper_order_path)))
    return report


def iter_live_cycle_paths(roots: Iterable[str | Path]) -> list[Path]:
    paths: list[Path] = []
    for root in roots:
        live_cycle_dir = Path(root) / "live_cycle"
        if live_cycle_dir.exists():
            paths.extend(sorted(live_cycle_dir.glob("*.json")))
    return paths


def write_report(reports: list[LiveCycleMigrationReport], report_dir: str | Path) -> Path:
    out_dir = Path(report_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = out_dir / f"live_cycle_migration_{ts}.json"
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "reports": [report.as_dict() for report in reports],
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return out_path
