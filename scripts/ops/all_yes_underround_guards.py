#!/usr/bin/env python3
"""Pure guards for all-YES underround basket execution.

No network, no signing, no order placement. The same guard is intended for
paper execution now and for a future live executor after deploy review.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class BasketGuardConfig:
    min_underround: float = 0.02
    min_legs: int = 5
    max_legs: int = 20
    shares_per_leg: float = 5.0
    max_basket_cost_usd: float = 5.0
    max_yes_spread: float = 0.05
    min_leg_ask: float = 0.001
    max_leg_ask: float = 0.97
    max_candidates_per_cycle: int = 2
    max_snapshot_age_seconds: float | None = None
    kill_switch_path: str = "runtime/weather_edge_v1/all_yes_underround_paper_v0/PAUSE"

    @classmethod
    def load(cls, path: str | Path) -> "BasketGuardConfig":
        p = Path(path)
        if not p.exists():
            return cls()
        data = json.loads(p.read_text(encoding="utf-8"))
        known = {k: data[k] for k in data if k in cls.__dataclass_fields__}
        return cls(**known)

    def dump(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(asdict(self), indent=2, sort_keys=True) + "\n", encoding="utf-8")


@dataclass(frozen=True)
class BasketGuardDecision:
    allow: bool
    reason: str
    blockers: tuple[str, ...]
    basket_cost_usd: float | None = None
    expected_profit_usd: float | None = None
    leg_orders: tuple[dict[str, Any], ...] = ()


def _kill_switch_active(cfg: BasketGuardConfig, repo_root: Path) -> bool:
    return (repo_root / cfg.kill_switch_path).exists()


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_utc(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def check_candidate(
    *,
    cfg: BasketGuardConfig,
    repo_root: Path,
    candidate: dict[str, Any],
    decision_ts_utc: str | datetime | None = None,
) -> BasketGuardDecision:
    """Validate an equal-share all-YES basket candidate.

    The guard fails closed. It rejects any basket that cannot be represented as
    complete equal-share BUY_YES orders at the quoted best asks.
    """
    blockers: list[str] = []
    if _kill_switch_active(cfg, repo_root):
        blockers.append("kill_switch_active")

    if cfg.max_snapshot_age_seconds is not None:
        snapshot_ts = _parse_utc(candidate.get("snapshot_ts_utc"))
        decision_ts = _parse_utc(decision_ts_utc) if decision_ts_utc is not None else datetime.now(timezone.utc)
        if snapshot_ts is None:
            blockers.append("missing_snapshot_ts")
        else:
            age = (decision_ts - snapshot_ts).total_seconds()
            if age < -1:
                blockers.append("snapshot_ts_in_future")
            elif age > cfg.max_snapshot_age_seconds:
                blockers.append("snapshot_too_old")

    legs = list(candidate.get("legs_detail") or [])
    if len(legs) < cfg.min_legs:
        blockers.append("leg_count_below_min")
    if len(legs) > cfg.max_legs:
        blockers.append("leg_count_above_max")

    underround = _number(candidate.get("underround"))
    if underround is None:
        blockers.append("missing_underround")
    elif underround < cfg.min_underround:
        blockers.append("underround_below_min")

    max_spread = _number(candidate.get("max_yes_spread"))
    if max_spread is None:
        blockers.append("missing_max_spread")
    elif max_spread > cfg.max_yes_spread:
        blockers.append("spread_above_max")

    seen_condition_ids: set[str] = set()
    total_cost_per_share = 0.0
    leg_orders: list[dict[str, Any]] = []
    for index, leg in enumerate(legs):
        condition_id = str(leg.get("condition_id") or "")
        bracket = leg.get("bracket")
        best_ask = _number(leg.get("best_ask"))
        best_bid = _number(leg.get("best_bid"))
        ask_size = _number(leg.get("ask_size"))

        if not condition_id:
            blockers.append(f"leg_{index}_missing_condition_id")
        elif condition_id in seen_condition_ids:
            blockers.append(f"leg_{index}_duplicate_condition_id")
        else:
            seen_condition_ids.add(condition_id)
        if bracket is None:
            blockers.append(f"leg_{index}_missing_bracket")
        if best_ask is None:
            blockers.append(f"leg_{index}_missing_best_ask")
            continue
        if best_ask < cfg.min_leg_ask or best_ask > cfg.max_leg_ask:
            blockers.append(f"leg_{index}_ask_out_of_bounds")
        if ask_size is None:
            blockers.append(f"leg_{index}_missing_ask_size")
        elif ask_size < cfg.shares_per_leg:
            blockers.append(f"leg_{index}_depth_below_shares")
        if best_bid is not None and best_ask < best_bid:
            blockers.append(f"leg_{index}_crossed_yes_book")
        total_cost_per_share += best_ask
        leg_orders.append(
            {
                "condition_id": condition_id,
                "bracket": bracket,
                "side": "BUY_YES",
                "price": round(best_ask, 6),
                "shares": cfg.shares_per_leg,
                "notional_usd": round(best_ask * cfg.shares_per_leg, 6),
                "available_ask_size": ask_size,
            }
        )

    basket_cost = round(total_cost_per_share * cfg.shares_per_leg, 6)
    expected_profit = round((1.0 - total_cost_per_share) * cfg.shares_per_leg, 6)
    computed_underround = 1.0 - total_cost_per_share
    if underround is not None and abs(underround - computed_underround) > 0.002:
        blockers.append("underround_mismatch")
    if basket_cost > cfg.max_basket_cost_usd:
        blockers.append("basket_cost_above_max")
    if total_cost_per_share >= 1.0:
        blockers.append("basket_not_underround")

    if blockers:
        return BasketGuardDecision(
            allow=False,
            reason="blocked",
            blockers=tuple(sorted(set(blockers))),
            basket_cost_usd=basket_cost,
            expected_profit_usd=expected_profit,
        )
    return BasketGuardDecision(
        allow=True,
        reason="ok_all_leg_or_none",
        blockers=(),
        basket_cost_usd=basket_cost,
        expected_profit_usd=expected_profit,
        leg_orders=tuple(leg_orders),
    )


def decision_to_dict(decision: BasketGuardDecision) -> dict[str, Any]:
    return {
        "allow": decision.allow,
        "reason": decision.reason,
        "blockers": list(decision.blockers),
        "basket_cost_usd": decision.basket_cost_usd,
        "expected_profit_usd": decision.expected_profit_usd,
        "leg_orders": list(decision.leg_orders),
    }


def _selftest() -> int:
    cfg = BasketGuardConfig()
    root = Path("/tmp/all_yes_underround_guard_test")
    root.mkdir(exist_ok=True)
    candidate = {
        "underround": 0.5,
        "max_yes_spread": 0.01,
        "legs_detail": [
            {"condition_id": f"c{i}", "bracket": str(i), "best_ask": 0.1, "best_bid": 0.09, "ask_size": 5}
            for i in range(5)
        ],
        "snapshot_ts_utc": "2026-06-13T18:00:00Z",
    }
    decision = check_candidate(cfg=cfg, repo_root=root, candidate=candidate)
    assert decision.allow, decision
    bad = dict(candidate)
    bad["underround"] = -0.01
    decision = check_candidate(cfg=cfg, repo_root=root, candidate=bad)
    assert not decision.allow and "underround_below_min" in decision.blockers, decision
    print("all_yes_underround_guards selftest: ALL PASS")
    return 0


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "test":
        raise SystemExit(_selftest())
    print("usage: all_yes_underround_guards.py test")
