from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from src.platform.quote_runtime.risk.safety_guard import RiskError, SafetyGuard, SecurityError
from src.strategies.weather_edge_v1.tools.execution_policy import (
    ExecutionPolicyConfig,
    build_execution_quote,
    build_execution_quotes,
)


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
    expanded = path.expanduser()
    if not expanded.exists():
        return rows
    with expanded.open("r", encoding="utf-8") as fh:
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

    raw_side = (safe_str(row.get("side")) or safe_str(row.get("signal_side"))).upper()
    if raw_side not in {"BUY_YES", "BUY_NO"}:
        return None

    token_id = safe_str(row.get("token_id"))
    market_price = to_float(row.get("market_price"), 0.0)
    best_bid = to_float(row.get("best_bid"), 0.0)
    best_ask = to_float(row.get("best_ask"), 0.0)
    spread = to_float(row.get("spread"), max(0.0, best_ask - best_bid) if best_bid > 0 and best_ask > 0 else 0.0)
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
        "city_pool": safe_str(row.get("city_pool")),
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
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread": spread,
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
    strategy_instance: str = ""
    max_order_notional: float = 1.0
    sizing_mode: str = "notional"
    fixed_order_shares: float = 10.0
    max_order_shares: Optional[float] = None
    min_order_shares: float = 5.0
    min_edge: float = 0.10
    min_entry_price: float = 0.25
    max_entry_price: float = 0.75
    # Per-side entry band / edge overrides. None -> fall back to the global
    # min_edge / min_entry_price / max_entry_price above. Lets BUY_YES and
    # BUY_NO use different gates without changing flat sizing or pricing.
    yes_min_entry_price: Optional[float] = None
    yes_max_entry_price: Optional[float] = None
    yes_min_edge: Optional[float] = None
    no_min_entry_price: Optional[float] = None
    no_max_entry_price: Optional[float] = None
    no_min_edge: Optional[float] = None
    price_offset: float = 0.0
    price_floor: float = 0.01
    price_ceiling: float = 0.99
    max_position: float = 25.0
    live_enabled: bool = False
    execution_policy: str = "mid_price_core_v1"
    tick_size: float = 0.01
    min_quote_edge: float = 0.03
    max_quote_spread: float = 0.12
    max_mid_drift: float = 0.10
    quote_improvement_ticks: int = 1
    wide_spread_shade_ticks: int = 1
    narrow_quote_spread: float = 0.03
    adverse_selection_spread_fraction: float = 0.50
    low_band_ceiling: float = 0.40
    high_band_floor: float = 0.55
    split_enabled: bool = True
    taker_fraction: float = 0.50
    split_min_edge: float = 0.10
    high_band_shade_narrow: int = 1
    high_band_shade_wide: int = 2
    high_band_min_edge: float = 0.15
    high_band_size_mult: float = 0.60


def _policy_config(config: PlannerConfig) -> ExecutionPolicyConfig:
    return ExecutionPolicyConfig(
        policy_name=config.execution_policy,
        price_offset=config.price_offset,
        price_floor=config.price_floor,
        price_ceiling=config.price_ceiling,
        tick_size=config.tick_size,
        min_quote_edge=config.min_quote_edge,
        max_quote_spread=config.max_quote_spread,
        max_mid_drift=config.max_mid_drift,
        quote_improvement_ticks=config.quote_improvement_ticks,
        wide_spread_shade_ticks=config.wide_spread_shade_ticks,
        narrow_spread=config.narrow_quote_spread,
        adverse_selection_spread_fraction=config.adverse_selection_spread_fraction,
        low_band_ceiling=config.low_band_ceiling,
        high_band_floor=config.high_band_floor,
        split_enabled=config.split_enabled,
        taker_fraction=config.taker_fraction,
        split_min_edge=config.split_min_edge,
        high_band_shade_narrow=config.high_band_shade_narrow,
        high_band_shade_wide=config.high_band_shade_wide,
        high_band_min_edge=config.high_band_min_edge,
        high_band_size_mult=config.high_band_size_mult,
    )


def _effective_band(config: PlannerConfig, side: str) -> tuple[float, float, float]:
    """Resolve (min_entry_price, max_entry_price, min_edge) for a signal side.

    Per-side overrides win when set; otherwise fall back to the global config.
    """
    s = (side or "").upper()
    if s == "BUY_YES":
        lo = config.yes_min_entry_price if config.yes_min_entry_price is not None else config.min_entry_price
        hi = config.yes_max_entry_price if config.yes_max_entry_price is not None else config.max_entry_price
        ed = config.yes_min_edge if config.yes_min_edge is not None else config.min_edge
    elif s == "BUY_NO":
        lo = config.no_min_entry_price if config.no_min_entry_price is not None else config.min_entry_price
        hi = config.no_max_entry_price if config.no_max_entry_price is not None else config.max_entry_price
        ed = config.no_min_edge if config.no_min_edge is not None else config.min_edge
    else:
        lo, hi, ed = config.min_entry_price, config.max_entry_price, config.min_edge
    return float(lo), float(hi), float(ed)


def build_trade_plan(
    signal: Dict[str, Any],
    config: PlannerConfig,
    *,
    quote: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    token_id = safe_str(signal.get("token_id"))
    strategy_instance = (
        safe_str(config.strategy_instance)
        or safe_str(signal.get("strategy_instance"))
        or safe_str(config.execution_policy)
    )
    eff_min_entry, eff_max_entry, eff_min_edge = _effective_band(
        config, safe_str(signal.get("signal_side"))
    )
    market_price = to_float(signal.get("market_price"), 0.0)
    best_bid = to_float(signal.get("best_bid"), 0.0)
    best_ask = to_float(signal.get("best_ask"), 0.0)
    spread = to_float(signal.get("spread"), max(0.0, best_ask - best_bid) if best_bid > 0 and best_ask > 0 else 0.0)
    edge = to_float(signal.get("edge"), 0.0)
    quote = quote or build_execution_quote(signal, _policy_config(config))
    if (
        safe_str(config.execution_policy) == "maker_queue_v1"
        and safe_str(quote.get("quote_status")) == "rejected"
        and safe_str(quote.get("quote_reason")) == "missing_two_sided_book"
    ):
        quote = {
            **quote,
            "quote_status": "accepted",
            "quote_reason": "defer_to_executor_missing_two_sided_book",
            "limit_price": round(market_price, 6),
            "quote_edge": round(to_float(quote.get("model_token_probability"), 0.0) - market_price, 6),
            "quote_mode": "defer_to_executor",
        }
    limit_price = to_float(quote.get("limit_price"), 0.0)
    notional_fraction = max(0.0, min(1.0, to_float(quote.get("notional_fraction"), 1.0)))
    size_multiplier = max(0.0, to_float(quote.get("size_multiplier"), 1.0))
    order_budget = round(float(config.max_order_notional) * notional_fraction * size_multiplier, 6)
    sizing_mode = safe_str(config.sizing_mode) or "notional"
    if sizing_mode == "fixed_shares":
        size = round(max(0.0, float(config.fixed_order_shares) * notional_fraction * size_multiplier), 6)
    elif sizing_mode == "notional":
        size = round(order_budget / limit_price, 6) if limit_price > 0 else 0.0
    else:
        size = 0.0
    # Exchange enforces a minimum order size (Polymarket: 5 shares). Reduced-size
    # legs (e.g. high-band size_mult) can fall below it and get rejected pre-fill.
    # Floor up to the minimum so the order is executable; this raises the leg's
    # notional above order_budget, so reflect that in order_notional_cap to keep
    # the live-contract notional check consistent.
    min_order_shares = max(0.0, float(config.min_order_shares))
    size_floored_to_min = False
    if min_order_shares > 0 and 0.0 < size < min_order_shares:
        size = round(min_order_shares, 6)
        size_floored_to_min = True
    order_notional_cap = round(size * limit_price, 6) if size_floored_to_min else order_budget
    max_order_shares = float(config.max_order_shares if config.max_order_shares is not None else config.max_position)
    base = {
        "signal_id": safe_str(signal.get("signal_id")),
        "strategy": "weather_edge_v1",
        "strategy_instance": strategy_instance,
        "source_strategy_instance": safe_str(signal.get("strategy_instance")),
        "profile": safe_str(signal.get("profile")),
        "combo": safe_str(signal.get("combo")),
        "city": safe_str(signal.get("city")),
        "city_pool": safe_str(signal.get("city_pool")),
        "target_date": safe_str(signal.get("target_date")),
        "market_slug": safe_str(signal.get("market_slug")),
        "market_id": safe_str(signal.get("market_id")),
        "event_slug": safe_str(signal.get("event_slug")),
        "bracket": safe_str(signal.get("bracket")),
        "token_id": token_id,
        "signal_side": safe_str(signal.get("signal_side")),
        "order_side": "BUY",
        "market_price": round(market_price, 6),
        "best_bid": round(best_bid, 6),
        "best_ask": round(best_ask, 6),
        "spread": round(spread, 6),
        "limit_price": round(limit_price, 6),
        "quote_status": safe_str(quote.get("quote_status")),
        "quote_reason": safe_str(quote.get("quote_reason")),
        "quote_edge": to_float(quote.get("quote_edge"), 0.0),
        "required_quote_edge": to_float(quote.get("required_quote_edge"), 0.0),
        "model_token_probability": to_float(quote.get("model_token_probability"), 0.0),
        "quote_best_bid": to_float(quote.get("quote_best_bid"), best_bid),
        "quote_best_ask": to_float(quote.get("quote_best_ask"), best_ask),
        "quote_spread": to_float(quote.get("quote_spread"), spread),
        "quote_tick_size": to_float(quote.get("quote_tick_size"), config.tick_size),
        "quote_mode": safe_str(quote.get("quote_mode")),
        "child_order_role": safe_str(quote.get("child_order_role")) or "single",
        "maker_only": bool(quote.get("maker_only", True)),
        "notional_fraction": notional_fraction,
        "size_multiplier": size_multiplier,
        "order_notional_cap": order_notional_cap,
        "entry_price_min": round(eff_min_entry, 6),
        "entry_price_max": round(eff_max_entry, 6),
        "entry_price_window": f"{eff_min_entry:.2f}-{eff_max_entry:.2f}",
        "execution_policy": safe_str(config.execution_policy),
        "tick_size": round(float(config.tick_size), 6),
        "min_quote_edge": round(float(config.min_quote_edge), 6),
        "max_quote_spread": round(float(config.max_quote_spread), 6),
        "max_mid_drift": round(float(config.max_mid_drift), 6),
        "quote_improvement_ticks": int(config.quote_improvement_ticks),
        "wide_spread_shade_ticks": int(config.wide_spread_shade_ticks),
        "narrow_quote_spread": round(float(config.narrow_quote_spread), 6),
        "adverse_selection_spread_fraction": round(float(config.adverse_selection_spread_fraction), 6),
        "low_band_ceiling": round(float(config.low_band_ceiling), 6),
        "high_band_floor": round(float(config.high_band_floor), 6),
        "split_enabled": bool(config.split_enabled),
        "taker_fraction": round(float(config.taker_fraction), 6),
        "split_min_edge": round(float(config.split_min_edge), 6),
        "high_band_shade_narrow": int(config.high_band_shade_narrow),
        "high_band_shade_wide": int(config.high_band_shade_wide),
        "high_band_min_edge": round(float(config.high_band_min_edge), 6),
        "high_band_size_mult": round(float(config.high_band_size_mult), 6),
        "sizing_mode": sizing_mode,
        "fixed_order_shares": round(float(config.fixed_order_shares), 6),
        "max_order_shares": round(max_order_shares, 6),
        "size": size,
        "notional": round(size * limit_price, 6),
        "edge": round(edge, 6),
        "min_edge": round(eff_min_edge, 6),
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
    if market_price < eff_min_entry:
        return {
            **plan,
            "status": "rejected",
            "risk_status": "rejected",
            "risk_reason": "entry_price_below_min",
        }
    if market_price >= eff_max_entry:
        return {
            **plan,
            "status": "rejected",
            "risk_status": "rejected",
            "risk_reason": "entry_price_at_or_above_max",
        }
    if edge < eff_min_edge:
        return {**plan, "status": "rejected", "risk_status": "rejected", "risk_reason": "edge_below_min"}
    if safe_str(quote.get("quote_status")) != "accepted":
        reason = safe_str(quote.get("quote_reason")) or "execution_quote_rejected"
        return {**plan, "status": "rejected", "risk_status": "rejected", "risk_reason": reason}
    if sizing_mode not in {"notional", "fixed_shares"}:
        return {**plan, "status": "rejected", "risk_status": "rejected", "risk_reason": "bad_sizing_mode"}
    guard = SafetyGuard(
        allowed_tokens={token_id} if token_id else set(),
        max_order_value=config.max_order_notional,
        max_position=max_order_shares,
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


def build_trade_plans_for_signal(signal: Dict[str, Any], config: PlannerConfig) -> List[Dict[str, Any]]:
    quotes = build_execution_quotes(signal, _policy_config(config))
    return [build_trade_plan(signal, config, quote=quote) for quote in quotes]


def plan_trades(
    *,
    signal_path: Path,
    out_path: Path,
    config: PlannerConfig,
    include_rejected: bool = True,
    dry_run: bool = False,
) -> Dict[str, Any]:
    signals = [row for row in read_jsonl(signal_path) if safe_str(row.get("record_type")) == "weather_edge_signal"]
    all_plans = [plan for signal in signals for plan in build_trade_plans_for_signal(signal, config)]
    accepted_total = sum(1 for plan in all_plans if safe_str(plan.get("status")) == "accepted")
    rejected_total = sum(1 for plan in all_plans if safe_str(plan.get("status")) == "rejected")
    plans = all_plans
    if not include_rejected:
        plans = [plan for plan in plans if safe_str(plan.get("status")) == "accepted"]
    summary = {
        "signals": len(signals),
        "plans": len(plans),
        "all_plans": len(all_plans),
        "accepted": accepted_total,
        "rejected": rejected_total,
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
        "strategy_instance": safe_str(plan.get("strategy_instance")),
        "source_strategy_instance": safe_str(plan.get("source_strategy_instance")),
        "venue": "paper",
        "city": safe_str(plan.get("city")),
        "city_pool": safe_str(plan.get("city_pool")),
        "target_date": safe_str(plan.get("target_date")),
        "market_slug": safe_str(plan.get("market_slug")),
        "market_id": safe_str(plan.get("market_id")),
        "bracket": safe_str(plan.get("bracket")),
        "token_id": safe_str(plan.get("token_id")),
        "signal_side": safe_str(plan.get("signal_side")),
        "order_side": safe_str(plan.get("order_side")) or "BUY",
        "limit_price": to_float(plan.get("limit_price"), 0.0),
        "quote_status": safe_str(plan.get("quote_status")),
        "quote_reason": safe_str(plan.get("quote_reason")),
        "quote_edge": to_float(plan.get("quote_edge"), 0.0),
        "required_quote_edge": to_float(plan.get("required_quote_edge"), 0.0),
        "model_token_probability": to_float(plan.get("model_token_probability"), 0.0),
        "quote_best_bid": to_float(plan.get("quote_best_bid"), 0.0),
        "quote_best_ask": to_float(plan.get("quote_best_ask"), 0.0),
        "quote_spread": to_float(plan.get("quote_spread"), 0.0),
        "quote_tick_size": to_float(plan.get("quote_tick_size"), 0.0),
        "quote_mode": safe_str(plan.get("quote_mode")),
        "child_order_role": safe_str(plan.get("child_order_role")) or "single",
        "maker_only": bool(plan.get("maker_only", True)),
        "notional_fraction": to_float(plan.get("notional_fraction"), 1.0),
        "size_multiplier": to_float(plan.get("size_multiplier"), 1.0),
        "order_notional_cap": to_float(plan.get("order_notional_cap"), 0.0),
        "best_bid": to_float(plan.get("best_bid"), 0.0),
        "best_ask": to_float(plan.get("best_ask"), 0.0),
        "spread": to_float(plan.get("spread"), 0.0),
        "size": to_float(plan.get("size"), 0.0),
        "notional": to_float(plan.get("notional"), 0.0),
        "execution_policy": safe_str(plan.get("execution_policy")),
        "tick_size": to_float(plan.get("tick_size"), 0.0),
        "min_quote_edge": to_float(plan.get("min_quote_edge"), 0.0),
        "max_quote_spread": to_float(plan.get("max_quote_spread"), 0.0),
        "max_mid_drift": to_float(plan.get("max_mid_drift"), 0.0),
        "quote_improvement_ticks": to_float(plan.get("quote_improvement_ticks"), 0.0),
        "wide_spread_shade_ticks": to_float(plan.get("wide_spread_shade_ticks"), 0.0),
        "narrow_quote_spread": to_float(plan.get("narrow_quote_spread"), 0.0),
        "adverse_selection_spread_fraction": to_float(plan.get("adverse_selection_spread_fraction"), 0.0),
        "low_band_ceiling": to_float(plan.get("low_band_ceiling"), 0.0),
        "high_band_floor": to_float(plan.get("high_band_floor"), 0.0),
        "split_enabled": bool(plan.get("split_enabled", False)),
        "taker_fraction": to_float(plan.get("taker_fraction"), 0.0),
        "split_min_edge": to_float(plan.get("split_min_edge"), 0.0),
        "high_band_shade_narrow": to_float(plan.get("high_band_shade_narrow"), 0.0),
        "high_band_shade_wide": to_float(plan.get("high_band_shade_wide"), 0.0),
        "high_band_min_edge": to_float(plan.get("high_band_min_edge"), 0.0),
        "high_band_size_mult": to_float(plan.get("high_band_size_mult"), 0.0),
        "entry_price_window": safe_str(plan.get("entry_price_window")),
        "sizing_mode": safe_str(plan.get("sizing_mode")),
        "fixed_order_shares": to_float(plan.get("fixed_order_shares"), 0.0),
        "max_order_shares": to_float(plan.get("max_order_shares"), 0.0),
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
    best_bid = to_float(response.get("best_bid"), to_float(plan.get("best_bid"), 0.0))
    best_ask = to_float(response.get("best_ask"), to_float(plan.get("best_ask"), 0.0))
    spread = max(0.0, best_ask - best_bid) if best_bid > 0 and best_ask > 0 else to_float(plan.get("spread"), 0.0)
    base = {
        "plan_id": safe_str(plan.get("plan_id")),
        "signal_id": safe_str(plan.get("signal_id")),
        "strategy": "weather_edge_v1",
        "strategy_instance": safe_str(plan.get("strategy_instance")),
        "source_strategy_instance": safe_str(plan.get("source_strategy_instance")),
        "venue": "polymarket_clob",
        "city": safe_str(plan.get("city")),
        "city_pool": safe_str(plan.get("city_pool")),
        "target_date": safe_str(plan.get("target_date")),
        "market_slug": safe_str(plan.get("market_slug")),
        "market_id": safe_str(plan.get("market_id")),
        "bracket": safe_str(plan.get("bracket")),
        "token_id": safe_str(plan.get("token_id")),
        "signal_side": safe_str(plan.get("signal_side")),
        "order_side": safe_str(plan.get("order_side")) or "BUY",
        "limit_price": to_float(plan.get("limit_price"), 0.0),
        "requested_price": to_float(response.get("requested_price"), to_float(plan.get("limit_price"), 0.0)),
        "posted_price": to_float(response.get("posted_price"), 0.0),
        "quote_status": safe_str(response.get("quote_status")) or safe_str(plan.get("quote_status")),
        "quote_reason": safe_str(response.get("quote_reason")) or safe_str(plan.get("quote_reason")),
        "quote_edge": to_float(response.get("quote_edge"), to_float(plan.get("quote_edge"), 0.0)),
        "required_quote_edge": to_float(
            response.get("required_quote_edge"),
            to_float(plan.get("required_quote_edge"), 0.0),
        ),
        "model_token_probability": to_float(
            response.get("model_token_probability"),
            to_float(plan.get("model_token_probability"), 0.0),
        ),
        "quote_best_bid": to_float(response.get("quote_best_bid"), to_float(plan.get("quote_best_bid"), 0.0)),
        "quote_best_ask": to_float(response.get("quote_best_ask"), to_float(plan.get("quote_best_ask"), 0.0)),
        "quote_spread": to_float(response.get("quote_spread"), to_float(plan.get("quote_spread"), 0.0)),
        "quote_tick_size": to_float(response.get("quote_tick_size"), to_float(plan.get("quote_tick_size"), 0.0)),
        "quote_mode": safe_str(response.get("quote_mode")) or safe_str(plan.get("quote_mode")),
        "child_order_role": safe_str(plan.get("child_order_role")) or "single",
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread": spread,
        "maker_only": bool(response.get("maker_only", plan.get("maker_only", False))),
        "clob_client": safe_str(response.get("clob_client")),
        "size": to_float(plan.get("size"), 0.0),
        "notional": to_float(plan.get("notional"), 0.0),
        "posted_notional": round(to_float(response.get("posted_price"), 0.0) * to_float(plan.get("size"), 0.0), 6),
        "execution_policy": safe_str(plan.get("execution_policy")),
        "tick_size": to_float(plan.get("tick_size"), 0.0),
        "min_quote_edge": to_float(plan.get("min_quote_edge"), 0.0),
        "max_quote_spread": to_float(plan.get("max_quote_spread"), 0.0),
        "max_mid_drift": to_float(plan.get("max_mid_drift"), 0.0),
        "quote_improvement_ticks": to_float(plan.get("quote_improvement_ticks"), 0.0),
        "wide_spread_shade_ticks": to_float(plan.get("wide_spread_shade_ticks"), 0.0),
        "narrow_quote_spread": to_float(plan.get("narrow_quote_spread"), 0.0),
        "adverse_selection_spread_fraction": to_float(plan.get("adverse_selection_spread_fraction"), 0.0),
        "low_band_ceiling": to_float(plan.get("low_band_ceiling"), 0.0),
        "high_band_floor": to_float(plan.get("high_band_floor"), 0.0),
        "split_enabled": bool(plan.get("split_enabled", False)),
        "taker_fraction": to_float(plan.get("taker_fraction"), 0.0),
        "split_min_edge": to_float(plan.get("split_min_edge"), 0.0),
        "high_band_shade_narrow": to_float(plan.get("high_band_shade_narrow"), 0.0),
        "high_band_shade_wide": to_float(plan.get("high_band_shade_wide"), 0.0),
        "high_band_min_edge": to_float(plan.get("high_band_min_edge"), 0.0),
        "high_band_size_mult": to_float(plan.get("high_band_size_mult"), 0.0),
        "entry_price_window": safe_str(plan.get("entry_price_window")),
        "sizing_mode": safe_str(plan.get("sizing_mode")),
        "fixed_order_shares": to_float(plan.get("fixed_order_shares"), 0.0),
        "max_order_shares": to_float(plan.get("max_order_shares"), 0.0),
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
            response = getattr(exc, "weather_execution_response", None)
            if not isinstance(response, dict):
                response = {}
            response = {
                **response,
                "error": f"{type(exc).__name__}: {exc}",
            }
            live_orders.append(
                build_live_order_record(
                    plan,
                    response,
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
