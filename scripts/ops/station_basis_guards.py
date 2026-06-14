#!/usr/bin/env python3
"""Risk / capital guards for the station-basis weather strategy.

Pure decision logic — no network, no order placement, no money. Both the
shadow tracker (for parity logging) and the future live executor call the same
``RiskGuard.check`` so the boundary that protects real capital is identical to
what shadow validates.

Design intent (see docs/analysis/2026-06/2026-06-13-station-basis-execution-design.md):
- Small-capital strategy: caps are deliberately tight. Loosen only with
  explicit user sign-off recorded in the design doc.
- Fail closed: any ambiguity (missing day-state, unparsable input, kill switch
  present) denies the entry.
- Every deny carries a machine-readable reason for the audit log.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict, field
from pathlib import Path


@dataclass(frozen=True)
class RiskConfig:
    # Hard ceilings (USD notional = ask * shares).
    per_trade_max_notional_usd: float = 5.0
    per_city_daily_max_notional_usd: float = 15.0
    daily_max_deployed_usd: float = 50.0
    # Loss circuit breaker: once today's realized loss is at/below this, halt
    # ALL new entries for the rest of the local day.
    daily_loss_stop_usd: float = -20.0
    # Concurrency.
    max_open_positions: int = 40
    max_open_positions_per_city: int = 8
    # Price sanity (defense in depth; strategy also bounds asks).
    min_ask: float = 0.02
    max_ask: float = 0.97
    # Kill switch: if this file exists, deny everything.
    kill_switch_path: str = "runtime/weather_edge_v1/station_basis_shadow/PAUSE"

    @classmethod
    def load(cls, path: str | Path) -> "RiskConfig":
        p = Path(path)
        if not p.exists():
            return cls()
        data = json.loads(p.read_text())
        known = {k: data[k] for k in data if k in cls.__dataclass_fields__}
        return cls(**known)

    def dump(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(asdict(self), indent=2) + "\n")


@dataclass
class DayState:
    """Mutable running totals for one local trading day, per executor process.

    Rebuilt from the orders ledger on startup so restarts don't reset caps.
    """

    deployed_usd: float = 0.0
    realized_pnl_usd: float = 0.0
    open_positions: int = 0
    per_city_deployed: dict[str, float] = field(default_factory=dict)
    per_city_open: dict[str, int] = field(default_factory=dict)

    def record_fill(self, city: str, notional: float) -> None:
        self.deployed_usd += notional
        self.open_positions += 1
        self.per_city_deployed[city] = self.per_city_deployed.get(city, 0.0) + notional
        self.per_city_open[city] = self.per_city_open.get(city, 0) + 1


@dataclass(frozen=True)
class GuardDecision:
    allow: bool
    reason: str
    capped_shares: float | None = None  # if a cap shrank the order rather than rejecting


def _kill_switch_active(cfg: RiskConfig, repo_root: Path) -> bool:
    return (repo_root / cfg.kill_switch_path).exists()


def check(
    *,
    cfg: RiskConfig,
    state: DayState,
    repo_root: Path,
    city: str,
    ask: float,
    shares: float,
) -> GuardDecision:
    """Decide whether a proposed entry may proceed.

    Returns allow=True with possibly ``capped_shares`` < requested when a
    notional cap can be satisfied by shrinking the order; allow=False when a
    hard limit (concurrency, daily loss, kill switch, price sanity) blocks it.
    """
    if _kill_switch_active(cfg, repo_root):
        return GuardDecision(False, "kill_switch_active")
    if not (cfg.min_ask <= ask <= cfg.max_ask):
        return GuardDecision(False, f"ask_out_of_bounds:{ask}")
    if state.realized_pnl_usd <= cfg.daily_loss_stop_usd:
        return GuardDecision(False, f"daily_loss_stop:{state.realized_pnl_usd:.2f}")
    if state.open_positions >= cfg.max_open_positions:
        return GuardDecision(False, f"max_open_positions:{state.open_positions}")
    if state.per_city_open.get(city, 0) >= cfg.max_open_positions_per_city:
        return GuardDecision(False, f"max_open_positions_per_city:{city}")

    notional = ask * shares
    capped = shares

    def cap_to(budget_left: float, label: str) -> GuardDecision | None:
        nonlocal capped, notional
        if budget_left <= 0:
            return GuardDecision(False, label)
        max_shares = budget_left / ask
        if capped > max_shares:
            capped = max_shares
            notional = ask * capped
        return None

    # per-trade cap
    if notional > cfg.per_trade_max_notional_usd:
        capped = cfg.per_trade_max_notional_usd / ask
        notional = ask * capped
    # per-city daily cap
    d = cap_to(
        cfg.per_city_daily_max_notional_usd - state.per_city_deployed.get(city, 0.0),
        f"per_city_daily_cap_full:{city}",
    )
    if d is not None:
        return d
    # global daily deployed cap
    d = cap_to(cfg.daily_max_deployed_usd - state.deployed_usd, "daily_deployed_cap_full")
    if d is not None:
        return d

    if capped <= 0:
        return GuardDecision(False, "capped_to_zero")
    reason = "ok" if abs(capped - shares) < 1e-9 else "ok_capped"
    return GuardDecision(True, reason, capped_shares=round(capped, 4))


def maker_resting_price(
    best_bid: float, best_ask: float, tick: float = 0.01
) -> float | None:
    """Validated maker rule (D5): post a resting BUY at best_bid + 1 tick,
    but never cross — cap at best_ask - 1 tick so the order stays maker.

    This is the calibrated rule from the maker backtest (M1 = best_bid+1tick,
    basis +8%), replacing the earlier arbitrary ask*(1-discount) placeholder.
    Computed from the LIVE book at placement time, not stale plan-time quotes.

    Returns None when no valid maker price exists (no bid, locked/crossed book,
    or 1-tick spread leaving no room to rest below the ask).
    """
    if best_bid <= 0 or best_ask <= 0 or best_ask <= best_bid:
        return None
    target = best_bid + tick
    # improve by a tick if it still rests below the ask; otherwise the spread
    # is too tight to improve, so join the queue at best_bid (still maker).
    price = target if target < best_ask else best_bid
    return round(price, 4)


# ----------------------------------------------------------------------------
# Self-test: python scripts/ops/station_basis_guards.py test
# ----------------------------------------------------------------------------
def _selftest() -> int:
    root = Path("/tmp/station_basis_guard_test")
    root.mkdir(exist_ok=True)
    cfg = RiskConfig(kill_switch_path="PAUSE")

    # 1. normal small order passes unchanged
    st = DayState()
    d = check(cfg=cfg, state=st, repo_root=root, city="Paris", ask=0.5, shares=8)
    assert d.allow and d.reason == "ok" and d.capped_shares == 8, d

    # 2. per-trade notional cap shrinks order: ask 0.9 * 10 = 9 > 5 -> 5/0.9
    d = check(cfg=cfg, state=st, repo_root=root, city="Paris", ask=0.9, shares=10)
    assert d.allow and d.reason == "ok_capped" and abs(d.capped_shares - 5 / 0.9) < 1e-3, d

    # 3. price sanity
    d = check(cfg=cfg, state=st, repo_root=root, city="Paris", ask=0.005, shares=10)
    assert not d.allow and d.reason.startswith("ask_out_of_bounds"), d

    # 4. daily loss stop
    st2 = DayState(realized_pnl_usd=-25.0)
    d = check(cfg=cfg, state=st2, repo_root=root, city="Paris", ask=0.5, shares=2)
    assert not d.allow and d.reason.startswith("daily_loss_stop"), d

    # 5. per-city concurrency
    st3 = DayState(per_city_open={"Paris": 8})
    d = check(cfg=cfg, state=st3, repo_root=root, city="Paris", ask=0.5, shares=2)
    assert not d.allow and d.reason.startswith("max_open_positions_per_city"), d

    # 6. per-city daily notional already full
    st4 = DayState(per_city_deployed={"Paris": 15.0})
    d = check(cfg=cfg, state=st4, repo_root=root, city="Paris", ask=0.5, shares=2)
    assert not d.allow and d.reason.startswith("per_city_daily_cap_full"), d

    # 7. global daily deployed cap shrinks last order
    st5 = DayState(deployed_usd=48.0)
    d = check(cfg=cfg, state=st5, repo_root=root, city="Tokyo", ask=0.5, shares=10)
    assert d.allow and abs(d.capped_shares - 2 / 0.5) < 1e-6, d  # only $2 of budget left

    # 8. kill switch
    (root / "PAUSE").write_text("halt")
    d = check(cfg=cfg, state=st, repo_root=root, city="Paris", ask=0.5, shares=2)
    assert not d.allow and d.reason == "kill_switch_active", d
    (root / "PAUSE").unlink()

    # 9. maker improves by 1 tick when spread allows
    assert maker_resting_price(0.80, 0.84) == 0.81, maker_resting_price(0.80, 0.84)
    # 10. 1-tick spread: can't improve without crossing -> join at best_bid
    assert maker_resting_price(0.83, 0.84) == 0.83, maker_resting_price(0.83, 0.84)
    # 11. sub-tick spread: still join at best_bid (never below it, never cross)
    assert maker_resting_price(0.83, 0.835, tick=0.01) == 0.83, maker_resting_price(0.83, 0.835)
    # 12. no bid / crossed book -> None
    assert maker_resting_price(0.0, 0.84) is None
    assert maker_resting_price(0.85, 0.84) is None

    print("station_basis_guards selftest: ALL PASS (8 guard + 5 maker cases)")
    return 0


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "test":
        sys.exit(_selftest())
    print("usage: station_basis_guards.py test")
