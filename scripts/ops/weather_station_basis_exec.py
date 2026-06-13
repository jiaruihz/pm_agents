#!/usr/bin/env python3
"""Execution layer for the station-basis strategy.

Architecture: the shadow tracker (weather_station_basis_shadow.py) owns signal
generation and logs each intended entry to entries.jsonl. THIS module is the
downstream executor: it consumes those entries, applies RiskGuard (capital /
loss / concurrency / kill-switch), and records what would actually be deployed.

Execution modes (env STATION_BASIS_EXEC_MODE, default dry_run):
- dry_run : risk-adjust and log to orders ledger; NO order placement. Safe.
- live    : would place real CLOB orders — HARD GATED and intentionally NOT
            wired this turn. Requires explicit arming + real-money review.

Real money is never touched unless someone deliberately implements and arms
_place_live_order. By default this module is a paper/parity layer that lets us
validate the exact risk boundary on the same signals shadow validates.

Commands:
  run     process new shadow entries once (intended for a periodic loop)
  status  show today's deployed / realized / open vs caps
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OPS = ROOT / "scripts/ops"
if str(OPS) not in sys.path:
    sys.path.insert(0, str(OPS))

from station_basis_guards import RiskConfig, DayState, check  # noqa: E402

DATA_ROOT = Path(os.environ.get("STATION_BASIS_DATA_ROOT") or os.environ.get("DATA_PROJECT_DIR") or ROOT)
SHADOW = DATA_ROOT / "runtime/weather_edge_v1/station_basis_shadow"
EXEC_DIR = DATA_ROOT / "runtime/weather_edge_v1/station_basis_exec"
ENTRIES = SHADOW / "entries.jsonl"
SETTLEMENTS = SHADOW / "settlements.jsonl"
ORDERS = EXEC_DIR / "orders.jsonl"
CURSOR = EXEC_DIR / "cursor.json"
RISK_CONFIG_PATH = EXEC_DIR / "risk_config.json"

MODE = os.environ.get("STATION_BASIS_EXEC_MODE", "dry_run")
LIVE_ARMED = os.environ.get("STATION_BASIS_LIVE_ARMED", "") == "I_UNDERSTAND_REAL_MONEY"


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def utc_day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def rebuild_day_state(day: str) -> DayState:
    """Reconstruct today's caps from the orders ledger + settlements so a
    restart never resets the risk budget."""
    st = DayState()
    for o in read_jsonl(ORDERS):
        if o.get("exec_day") == day and o.get("allow"):
            st.record_fill(o["city"], o.get("notional", 0.0))
    # realized today: settlements whose settled_at_utc is today (approximate
    # day attribution; documented limitation for the scaffold)
    for s in read_jsonl(SETTLEMENTS):
        sa = s.get("settled_at_utc", "")
        if sa[:10] == day:
            st.realized_pnl_usd += s.get("pnl", 0.0)
    return st


def load_cursor() -> int:
    if CURSOR.exists():
        return json.loads(CURSOR.read_text()).get("processed", 0)
    return 0


def save_cursor(n: int) -> None:
    CURSOR.parent.mkdir(parents=True, exist_ok=True)
    CURSOR.write_text(json.dumps({"processed": n}))


def _place_live_order(entry: dict, shares: float) -> dict:
    """HARD GATE. Real CLOB placement is intentionally NOT implemented here.

    Wiring py_clob_client to spend real USDC is a separate, explicitly
    confirmed step (see CLAUDE.md money-safety boundary). This stub guarantees
    no accidental real order can fire from this scaffold.
    """
    raise NotImplementedError(
        "live order placement is not wired. Real-money execution requires a "
        "deliberate implementation step + user confirmation; do not enable here."
    )


def run_once() -> None:
    EXEC_DIR.mkdir(parents=True, exist_ok=True)
    cfg = RiskConfig.load(RISK_CONFIG_PATH)
    if not RISK_CONFIG_PATH.exists():
        cfg.dump(RISK_CONFIG_PATH)  # materialize defaults for visibility/editing

    entries = read_jsonl(ENTRIES)
    cursor = load_cursor()
    new = entries[cursor:]
    if not new:
        print(f"no new shadow entries (processed={cursor}, mode={MODE})")
        return

    day = utc_day()
    state = rebuild_day_state(day)
    placed = capped = denied = 0

    for e in new:
        ask = float(e["ask"])
        req_shares = float(e["shares"])
        decision = check(
            cfg=cfg, state=state, repo_root=DATA_ROOT,
            city=e["city"], ask=ask, shares=req_shares,
        )
        shares = decision.capped_shares if decision.allow else 0.0
        notional = round(ask * shares, 4) if decision.allow else 0.0
        order = {
            "exec_day": day,
            "ts_utc": datetime.now(timezone.utc).isoformat(),
            "mode": MODE,
            "city": e["city"],
            "target_date": e["target_date"],
            "rule": e["rule"],
            "side": e["side"],
            "bracket": e["bracket"],
            "ask": ask,
            "requested_shares": req_shares,
            "shares": round(shares, 4),
            "notional": notional,
            "allow": decision.allow,
            "guard_reason": decision.reason,
            "token_id": e.get("token_id"),
            "event_slug": e.get("event_slug"),
            "shadow_ts_utc": e.get("ts_utc"),
            "placement": "none",
        }
        recorded = False
        if decision.allow:
            if MODE == "live":
                if not LIVE_ARMED:
                    order["placement"] = "blocked_not_armed"  # safe stop, no fill
                else:
                    _place_live_order(e, shares)  # raises by design
                    order["placement"] = "live_placed"
                    state.record_fill(e["city"], notional)
                    recorded = True
            else:
                order["placement"] = "dry_run_logged"
                state.record_fill(e["city"], notional)
                recorded = True
            if recorded:
                placed += 1
                if decision.reason == "ok_capped":
                    capped += 1
        else:
            denied += 1
        append_jsonl(ORDERS, order)

    save_cursor(cursor + len(new))
    print(
        f"mode={MODE} processed={len(new)} placed(intended)={placed} "
        f"capped={capped} denied={denied} | day={day} "
        f"deployed=${state.deployed_usd:.2f} realized=${state.realized_pnl_usd:.2f} "
        f"open={state.open_positions}"
    )


def status() -> None:
    cfg = RiskConfig.load(RISK_CONFIG_PATH)
    day = utc_day()
    st = rebuild_day_state(day)
    print(f"mode={MODE} live_armed={LIVE_ARMED} day={day}")
    print(f"  deployed  ${st.deployed_usd:6.2f} / ${cfg.daily_max_deployed_usd:.0f} cap")
    print(f"  realized  ${st.realized_pnl_usd:6.2f}  (loss stop ${cfg.daily_loss_stop_usd:.0f})")
    print(f"  open pos  {st.open_positions} / {cfg.max_open_positions}")
    for c, n in sorted(st.per_city_open.items()):
        print(f"    {c:12} open={n}/{cfg.max_open_positions_per_city} deployed=${st.per_city_deployed.get(c,0):.2f}/${cfg.per_city_daily_max_notional_usd:.0f}")
    ks = DATA_ROOT / cfg.kill_switch_path
    print(f"  kill switch: {'ACTIVE' if ks.exists() else 'clear'} ({ks})")


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run":
        run_once()
    elif cmd == "status":
        status()
    else:
        print("usage: weather_station_basis_exec.py [run|status]")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
