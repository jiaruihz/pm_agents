from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from src.platform.quote_runtime.risk.safety_guard import RiskError, SafetyGuard, SecurityError


DEFAULT_RUNTIME_ROOT = Path("runtime/weather_edge_v1")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def safe_str(value: Any) -> str:
    return "" if value is None else str(value).strip()


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def stable_hash(payload: Dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with path.expanduser().open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            if isinstance(item, dict):
                rows.append(item)
    return rows


def append_jsonl_dedup(path: Path, rows: Iterable[Dict[str, Any]], *, key_field: str) -> Dict[str, int]:
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = set()
    if path.exists():
        for row in read_jsonl(path):
            key = safe_str(row.get(key_field))
            if key:
                existing.add(key)

    written = 0
    skipped = 0
    with path.open("a", encoding="utf-8") as fh:
        for row in rows:
            key = safe_str(row.get(key_field))
            if not key:
                key = stable_hash(row)
                row = {**row, key_field: key}
            if key in existing:
                skipped += 1
                continue
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            existing.add(key)
            written += 1
    return {"written": written, "skipped_existing": skipped}


def normalize_signal(row: Dict[str, Any], *, source_system: str = "weather-predict") -> Optional[Dict[str, Any]]:
    record_type = safe_str(row.get("record_type"))
    if record_type not in {"paper_decision", "weather_edge_signal"}:
        return None

    raw_side = safe_str(row.get("side")).upper()
    if raw_side not in {"BUY_YES", "BUY_NO"}:
        return None

    token_id = safe_str(row.get("token_id"))
    market_price = to_float(row.get("market_price"), 0.0)
    edge = to_float(row.get("edge"), 0.0)
    if not token_id or market_price <= 0:
        return None

    base = {
        "source_system": source_system,
        "source_record_type": record_type,
        "source_id": safe_str(row.get("paper_id")) or safe_str(row.get("signal_id")),
        "source_run_id": safe_str(row.get("source_run_id")),
        "strategy": "weather_edge_v1",
        "profile": safe_str(row.get("profile")),
        "combo": safe_str(row.get("combo")),
        "city": safe_str(row.get("city")),
        "target_date": safe_str(row.get("target_date")),
        "unit": safe_str(row.get("unit")),
        "event_id": safe_str(row.get("event_id")),
        "event_slug": safe_str(row.get("event_slug")),
        "market_id": safe_str(row.get("market_id")),
        "market_slug": safe_str(row.get("market_slug")),
        "question": safe_str(row.get("question")),
        "bracket": safe_str(row.get("bracket")),
        "token_id": token_id,
        "signal_side": raw_side,
        "order_side": "BUY",
        "model_probability_yes": to_float(row.get("model_probability_yes"), 0.0),
        "market_price": market_price,
        "edge": edge,
        "min_edge": to_float(row.get("min_edge"), 0.0),
        "price_source": safe_str(row.get("price_source")),
        "obs_source": safe_str(row.get("obs_source")) or "obs_source_v1_iem_proxy",
        "model_version": safe_str(row.get("model_version")) or safe_str(row.get("profile")),
        "snapshot_fetched_at_utc": safe_str(row.get("snapshot_fetched_at_utc")),
    }
    signal_id = safe_str(row.get("signal_id")) or stable_hash(base)
    return {
        "record_type": "weather_edge_signal",
        "signal_id": signal_id,
        "imported_at_utc": utc_now_iso(),
        "status": "new",
        **base,
    }


def import_signals(
    *,
    input_paths: Iterable[Path],
    out_path: Path,
    source_system: str = "weather-predict",
    dry_run: bool = False,
) -> Dict[str, Any]:
    signals: List[Dict[str, Any]] = []
    seen = set()
    read_rows = 0
    for path in input_paths:
        for row in read_jsonl(path):
            read_rows += 1
            signal = normalize_signal(row, source_system=source_system)
            if not signal:
                continue
            key = signal["signal_id"]
            if key in seen:
                continue
            seen.add(key)
            signals.append(signal)
    summary = {
        "read_rows": read_rows,
        "valid_signals": len(signals),
        "out": str(out_path),
        "dry_run": dry_run,
    }
    if dry_run:
        return {**summary, "signals": signals}
    return {**summary, **append_jsonl_dedup(out_path, signals, key_field="signal_id")}


@dataclass(frozen=True)
class PlannerConfig:
    max_order_notional: float = 1.0
    min_edge: float = 0.10
    price_offset: float = 0.0
    price_floor: float = 0.01
    price_ceiling: float = 0.99
    max_position: float = 10.0
    live_enabled: bool = False


def build_trade_plan(signal: Dict[str, Any], config: PlannerConfig) -> Dict[str, Any]:
    token_id = safe_str(signal.get("token_id"))
    market_price = to_float(signal.get("market_price"), 0.0)
    edge = to_float(signal.get("edge"), 0.0)
    limit_price = max(config.price_floor, min(config.price_ceiling, market_price + config.price_offset))
    size = round(config.max_order_notional / limit_price, 6) if limit_price > 0 else 0.0
    base = {
        "signal_id": safe_str(signal.get("signal_id")),
        "strategy": "weather_edge_v1",
        "profile": safe_str(signal.get("profile")),
        "combo": safe_str(signal.get("combo")),
        "city": safe_str(signal.get("city")),
        "target_date": safe_str(signal.get("target_date")),
        "market_slug": safe_str(signal.get("market_slug")),
        "market_id": safe_str(signal.get("market_id")),
        "event_slug": safe_str(signal.get("event_slug")),
        "bracket": safe_str(signal.get("bracket")),
        "token_id": token_id,
        "signal_side": safe_str(signal.get("signal_side")),
        "order_side": "BUY",
        "market_price": round(market_price, 6),
        "limit_price": round(limit_price, 6),
        "size": size,
        "notional": round(size * limit_price, 6),
        "edge": round(edge, 6),
        "min_edge": round(config.min_edge, 6),
        "obs_source": safe_str(signal.get("obs_source")),
        "model_version": safe_str(signal.get("model_version")),
        "paper_enabled": True,
        "live_enabled": bool(config.live_enabled),
    }
    plan = {
        "record_type": "weather_edge_trade_plan",
        "plan_id": stable_hash(base),
        "created_at_utc": utc_now_iso(),
        "status": "accepted",
        "risk_status": "unchecked",
        "risk_reason": "",
        **base,
    }
    if edge < config.min_edge:
        return {**plan, "status": "rejected", "risk_status": "rejected", "risk_reason": "edge_below_min"}
    guard = SafetyGuard(
        allowed_tokens={token_id} if token_id else set(),
        max_order_value=config.max_order_notional,
        max_position=config.max_position,
        price_floor=config.price_floor,
        price_ceiling=config.price_ceiling,
    )
    try:
        guard.validate_order(token_id=token_id, price=limit_price, size=size, side="BUY", current_position=0.0)
    except (RiskError, SecurityError) as exc:
        return {
            **plan,
            "status": "rejected",
            "risk_status": "rejected",
            "risk_reason": f"{type(exc).__name__}: {exc}",
        }
    return {**plan, "risk_status": "passed"}


def plan_trades(
    *,
    signal_path: Path,
    out_path: Path,
    config: PlannerConfig,
    include_rejected: bool = True,
    dry_run: bool = False,
) -> Dict[str, Any]:
    signals = [row for row in read_jsonl(signal_path) if safe_str(row.get("record_type")) == "weather_edge_signal"]
    plans = [build_trade_plan(signal, config) for signal in signals]
    if not include_rejected:
        plans = [plan for plan in plans if safe_str(plan.get("status")) == "accepted"]
    accepted = sum(1 for plan in plans if safe_str(plan.get("status")) == "accepted")
    rejected = sum(1 for plan in plans if safe_str(plan.get("status")) == "rejected")
    summary = {
        "signals": len(signals),
        "plans": len(plans),
        "accepted": accepted,
        "rejected": rejected,
        "out": str(out_path),
        "dry_run": dry_run,
    }
    if dry_run:
        return {**summary, "plans": plans}
    return {**summary, **append_jsonl_dedup(out_path, plans, key_field="plan_id")}


LivePlaceFn = Callable[[Dict[str, Any]], Dict[str, Any]]


@dataclass(frozen=True)
class ExecutorConfig:
    live: bool = False
    confirm_live: bool = False
    cancel_after: bool = False


def _execution_id(plan: Dict[str, Any], venue: str) -> str:
    return stable_hash({"plan_id": safe_str(plan.get("plan_id")), "venue": venue})


def build_paper_order(plan: Dict[str, Any]) -> Dict[str, Any]:
    base = {
        "plan_id": safe_str(plan.get("plan_id")),
        "signal_id": safe_str(plan.get("signal_id")),
        "strategy": "weather_edge_v1",
        "venue": "paper",
        "city": safe_str(plan.get("city")),
        "target_date": safe_str(plan.get("target_date")),
        "market_slug": safe_str(plan.get("market_slug")),
        "bracket": safe_str(plan.get("bracket")),
        "token_id": safe_str(plan.get("token_id")),
        "order_side": safe_str(plan.get("order_side")) or "BUY",
        "limit_price": to_float(plan.get("limit_price"), 0.0),
        "size": to_float(plan.get("size"), 0.0),
        "notional": to_float(plan.get("notional"), 0.0),
        "source_plan_status": safe_str(plan.get("status")),
    }
    return {
        "record_type": "weather_edge_paper_order",
        "execution_id": stable_hash(base),
        "created_at_utc": utc_now_iso(),
        "status": "simulated_open",
        **base,
    }


def build_live_order_record(plan: Dict[str, Any], response: Dict[str, Any], *, status: str) -> Dict[str, Any]:
    base = {
        "plan_id": safe_str(plan.get("plan_id")),
        "signal_id": safe_str(plan.get("signal_id")),
        "strategy": "weather_edge_v1",
        "venue": "polymarket_clob",
        "city": safe_str(plan.get("city")),
        "target_date": safe_str(plan.get("target_date")),
        "market_slug": safe_str(plan.get("market_slug")),
        "bracket": safe_str(plan.get("bracket")),
        "token_id": safe_str(plan.get("token_id")),
        "order_side": safe_str(plan.get("order_side")) or "BUY",
        "limit_price": to_float(plan.get("limit_price"), 0.0),
        "size": to_float(plan.get("size"), 0.0),
        "notional": to_float(plan.get("notional"), 0.0),
    }
    return {
        "record_type": "weather_edge_live_order",
        "execution_id": stable_hash(base),
        "created_at_utc": utc_now_iso(),
        "status": status,
        "exchange_response": response,
        **base,
    }


def execute_trade_plans(
    *,
    plan_path: Path,
    paper_out: Path,
    live_out: Path,
    config: ExecutorConfig,
    live_place_fn: Optional[LivePlaceFn] = None,
) -> Dict[str, Any]:
    plans = [
        row
        for row in read_jsonl(plan_path)
        if safe_str(row.get("record_type")) == "weather_edge_trade_plan"
        and safe_str(row.get("status")) == "accepted"
        and safe_str(row.get("risk_status")) == "passed"
    ]
    paper_orders = [build_paper_order(plan) for plan in plans if bool(plan.get("paper_enabled", True))]
    paper_result = append_jsonl_dedup(paper_out, paper_orders, key_field="execution_id")

    live_orders: List[Dict[str, Any]] = []
    live_skipped = 0
    live_errors = 0
    if config.live and not config.confirm_live:
        raise RuntimeError("--live requires --confirm-live")
    if config.live and live_place_fn is None:
        raise RuntimeError("live execution requested but no live_place_fn was provided")

    for plan in plans:
        if not config.live:
            continue
        if not bool(plan.get("live_enabled", False)):
            live_skipped += 1
            continue
        try:
            assert live_place_fn is not None
            response = live_place_fn(plan)
            live_orders.append(build_live_order_record(plan, response, status="submitted"))
        except Exception as exc:
            live_errors += 1
            live_orders.append(
                build_live_order_record(
                    plan,
                    {"error": f"{type(exc).__name__}: {exc}"},
                    status="error",
                )
            )

    live_result = append_jsonl_dedup(live_out, live_orders, key_field="execution_id") if live_orders else {
        "written": 0,
        "skipped_existing": 0,
    }
    return {
        "plans_read": len(plans),
        "paper_orders": len(paper_orders),
        "paper_written": paper_result["written"],
        "paper_skipped_existing": paper_result["skipped_existing"],
        "live_requested": bool(config.live),
        "live_orders": len(live_orders),
        "live_written": live_result["written"],
        "live_skipped_disabled": live_skipped,
        "live_errors": live_errors,
        "paper_out": str(paper_out),
        "live_out": str(live_out),
    }
