#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ops.weather_live_cycle import (
    _build_live_contract_alerts,
    _clob_balance_status,
    _filter_plan_file_for_live_dedup,
    _load_json_from_output,
    _prior_submitted_live_keys,
    _read_live_errors,
    _run,
)
from src.platform.notification.telegram import send_telegram_message_sync


def _utc_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _latest_cycle_summary(live_cycle_dir: Path, source_policy: str) -> tuple[Path, dict[str, Any]]:
    for path in sorted(live_cycle_dir.glob("*.json"), reverse=True):
        summary = _read_json(path)
        config = summary.get("config") if isinstance(summary.get("config"), dict) else {}
        if config.get("execution_policy") != source_policy:
            continue
        signal_path = Path(str((summary.get("paths") or {}).get("signal") or ""))
        if signal_path.exists():
            return path, summary
    raise RuntimeError(f"no recent {source_policy} signal file found")


def _merge_today_signals(live_cycle_dir: Path, source_policy: str, out_path: Path) -> dict[str, Any]:
    """Merge signals from ALL of today's source_policy runs into out_path.

    For each market_id we keep the most recent signal by snapshot timestamp.
    This ensures the branch policy sees every market
    that the source policy has ever signalled today, not just the ones
    from the most recent single run (which may have fewer markets when
    the latest snapshot only covers a subset of the universe).

    Returns merge stats for run summary and contract alerts.
    """
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    # Collect all today's summaries for this policy, oldest-first so later
    # entries overwrite earlier ones when we dedup by market_id.
    # Signal files use market_id as the stable dedup key and
    # snapshot_fetched_at_utc for recency ordering.
    seen: dict[str, dict] = {}  # market_id → signal row
    source_files = 0
    source_rows = 0
    invalid_json_lines = 0
    missing_market_id = 0
    missing_snapshot_ts = 0

    for path in sorted(live_cycle_dir.glob("*.json")):
        # Filter to today's runs by filename prefix (YYYYMMDD)
        if not path.stem.startswith(today):
            continue
        summary = _read_json(path)
        config = summary.get("config") if isinstance(summary.get("config"), dict) else {}
        if config.get("execution_policy") != source_policy:
            continue
        signal_path = Path(str((summary.get("paths") or {}).get("signal") or ""))
        if not signal_path.exists():
            continue
        source_files += 1
        for line in signal_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            source_rows += 1
            try:
                sig = json.loads(line)
            except json.JSONDecodeError:
                invalid_json_lines += 1
                continue
            mid = str(sig.get("market_id") or "").strip()
            if not mid:
                missing_market_id += 1
                continue
            # Keep the version from the latest snapshot
            snap_ts = sig.get("snapshot_fetched_at_utc") or sig.get("snapshot_ts_utc") or ""
            if not snap_ts:
                missing_snapshot_ts += 1
            existing = seen.get(mid)
            existing_ts = (existing or {}).get("snapshot_fetched_at_utc") or (existing or {}).get("snapshot_ts_utc") or ""
            if existing is None or snap_ts >= existing_ts:
                seen[mid] = sig

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        for sig in seen.values():
            fh.write(json.dumps(sig) + "\n")
    return {
        "source_files": source_files,
        "source_rows": source_rows,
        "merged_signals": len(seen),
        "invalid_json_lines": invalid_json_lines,
        "missing_market_id": missing_market_id,
        "missing_snapshot_ts": missing_snapshot_ts,
    }


def _branch_signal_alerts(merge_stats: dict[str, Any] | None, signal_count: int) -> list[str]:
    if not merge_stats:
        return []
    alerts: list[str] = []
    merged = int(merge_stats.get("merged_signals", 0) or 0)
    if signal_count != merged:
        alerts.append(f"policy branch signal count mismatch: wrote {signal_count}, expected merged {merged}.")
    if int(merge_stats.get("invalid_json_lines", 0) or 0) > 0:
        alerts.append(f"policy branch skipped invalid source signal JSON lines: {merge_stats['invalid_json_lines']}.")
    if int(merge_stats.get("missing_market_id", 0) or 0) > 0:
        alerts.append(f"policy branch skipped source signals without market_id: {merge_stats['missing_market_id']}.")
    if int(merge_stats.get("missing_snapshot_ts", 0) or 0) > 0:
        alerts.append(f"policy branch source signals missing snapshot timestamp: {merge_stats['missing_snapshot_ts']}.")
    return alerts


def _order_rows(path: str | Path, limit: int = 6) -> list[dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if isinstance(row, dict):
            rows.append(row)
        if len(rows) >= limit:
            break
    return rows


def _policy_line(summary: dict[str, Any]) -> str:
    config = summary.get("config") if isinstance(summary.get("config"), dict) else {}
    planner = summary.get("planner") if isinstance(summary.get("planner"), dict) else {}
    executor = summary.get("executor") if isinstance(summary.get("executor"), dict) else {}
    policy = config.get("execution_policy", "-")
    accepted = int(planner.get("accepted", 0) or 0)
    before = planner.get("accepted_before_live_dedup")
    if before is not None:
        accepted_text = f"{accepted}/{int(before or 0)}"
    else:
        accepted_text = str(accepted)
    return (
        f"- {policy}: signals {int((summary.get('signals') or {}).get('signals', 0) or 0)}, "
        f"accepted {accepted_text}, live {int(executor.get('live_written', 0) or 0)}/"
        f"{int(executor.get('live_orders', 0) or 0)}, errors {int(executor.get('live_errors', 0) or 0)}"
    )


def _order_line(row: dict[str, Any]) -> str:
    city = row.get("city") or "-"
    bracket = row.get("bracket") or "-"
    side = row.get("signal_side") or row.get("order_side") or "-"
    requested = float(row.get("requested_price") or row.get("limit_price") or 0)
    posted = float(row.get("posted_price") or row.get("entry_price") or 0)
    mode = row.get("quote_mode") or "-"
    edge = float(row.get("quote_edge") or 0)
    order_id = str(((row.get("exchange_response") or {}).get("place") or {}).get("orderID") or row.get("order_id") or "")
    suffix = f" #{order_id[:10]}" if order_id else ""
    return f"- {city} {bracket} {side}: {requested:.3f} -> {posted:.3f}, {mode}, edge {edge:.3f}{suffix}"


def _send_branch_telegram(
    *,
    source_summary: dict[str, Any],
    branch_summary: dict[str, Any],
    dry_run_live: bool,
) -> None:
    if dry_run_live:
        return
    executor = branch_summary.get("executor") if isinstance(branch_summary.get("executor"), dict) else {}
    alerts = branch_summary.get("contract_alerts") or []
    if int(executor.get("live_orders", 0) or 0) == 0 and not alerts:
        return
    src_cfg = source_summary.get("config") if isinstance(source_summary.get("config"), dict) else {}
    branch_cfg = branch_summary.get("config") if isinstance(branch_summary.get("config"), dict) else {}
    source_policy = src_cfg.get("execution_policy", "-")
    branch_policy = branch_cfg.get("execution_policy", "-")
    source_signal = branch_cfg.get("source_signal_path", "-")
    headline = (
        f"执行策略分支：{source_policy} vs {branch_policy}"
        if not alerts
        else f"执行策略分支有 {len(alerts)} 个告警"
    )
    lines = [
        "【天气策略 A/B 下单】",
        headline,
        "",
        "共享信号：",
        f"- {Path(str(source_signal)).name}",
        "",
        "本轮汇总：",
        _policy_line(source_summary),
        _policy_line(branch_summary),
    ]
    orders = _order_rows((branch_summary.get("paths") or {}).get("live", ""), limit=6)
    if orders:
        lines.extend(["", f"{branch_policy} 挂单明细："])
        lines.extend(_order_line(row) for row in orders)
    if alerts:
        lines.extend(["", "告警："])
        lines.extend(f"- {x}" for x in alerts[:6])
    lines.extend(
        [
            "",
            "说明：accepted A/B 共享同一批 signal；fill/PnL 后续按 orderID 入库归因。",
        ]
    )
    try:
        send_telegram_message_sync("\n".join(lines))
    except Exception as exc:
        print(f"[WARN] telegram branch summary failed: {type(exc).__name__}: {exc}")


def main() -> int:
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
    except ModuleNotFoundError:
        pass

    parser = argparse.ArgumentParser(
        description="Run one execution-policy branch from a shared live signal file."
    )
    parser.add_argument("--execution-policy", choices=("mid_price_core_v1", "maker_queue_v1", "maker_queue_v2"), required=True)
    parser.add_argument("--source-policy", default="mid_price_core_v1")
    parser.add_argument("--source-signal")
    parser.add_argument("--max-order-notional", type=float, default=float(os.getenv("WEATHER_LIVE_MAX_ORDER_NOTIONAL", "5.00")))
    parser.add_argument("--sizing-mode", choices=("notional", "fixed_shares"), default=os.getenv("WEATHER_LIVE_SIZING_MODE", "notional"))
    parser.add_argument("--fixed-order-shares", type=float, default=float(os.getenv("WEATHER_LIVE_FIXED_ORDER_SHARES", "10.0")))
    parser.add_argument("--max-order-shares", type=float, default=float(os.getenv("WEATHER_LIVE_MAX_ORDER_SHARES", "25.0")))
    parser.add_argument("--city-pool", default=os.getenv("WEATHER_LIVE_CITY_POOL", "t1_trading"))
    parser.add_argument("--min-edge", type=float, default=float(os.getenv("WEATHER_LIVE_MIN_EDGE", "0.10")))
    parser.add_argument("--min-entry-price", type=float, default=float(os.getenv("WEATHER_LIVE_MIN_ENTRY_PRICE", "0.25")))
    parser.add_argument("--max-entry-price", type=float, default=float(os.getenv("WEATHER_LIVE_MAX_ENTRY_PRICE", "0.75")))
    parser.add_argument("--min-quote-edge", type=float, default=float(os.getenv("WEATHER_LIVE_MIN_QUOTE_EDGE", "0.03")))
    parser.add_argument("--max-quote-spread", type=float, default=float(os.getenv("WEATHER_LIVE_MAX_QUOTE_SPREAD", "0.12")))
    parser.add_argument("--max-mid-drift", type=float, default=float(os.getenv("WEATHER_LIVE_MAX_MID_DRIFT", "0.10")))
    parser.add_argument("--quote-improvement-ticks", type=int, default=int(os.getenv("WEATHER_LIVE_QUOTE_IMPROVEMENT_TICKS", "1")))
    parser.add_argument("--wide-spread-shade-ticks", type=int, default=int(os.getenv("WEATHER_LIVE_WIDE_SPREAD_SHADE_TICKS", "1")))
    parser.add_argument("--adverse-selection-spread-fraction", type=float, default=float(os.getenv("WEATHER_LIVE_ADVERSE_SELECTION_SPREAD_FRACTION", "0.50")))
    parser.add_argument("--dry-run-live", action="store_true")
    parser.add_argument("--no-telegram", action="store_true")
    args = parser.parse_args()

    run_id = _utc_run_id()
    runtime = ROOT / "runtime" / "weather_edge_v1"
    live_cycle_dir = runtime / "live_cycle"
    live_cycle_dir.mkdir(parents=True, exist_ok=True)

    source_summary_path = Path("")
    source_summary: dict[str, Any] = {}
    if args.source_signal:
        source_signal_path = Path(args.source_signal)
    else:
        source_summary_path, source_summary = _latest_cycle_summary(live_cycle_dir, args.source_policy)
        source_signal_path = Path(str((source_summary.get("paths") or {}).get("signal") or ""))
    if not source_signal_path.exists():
        raise RuntimeError(f"source signal file not found: {source_signal_path}")

    signal_path = runtime / "signals" / f"live_{run_id}_signals.jsonl"
    plan_path = runtime / "plans" / f"live_{run_id}_trade_plans.jsonl"
    paper_path = runtime / "paper" / f"live_{run_id}_paper_orders.jsonl"
    live_path = runtime / "live" / f"live_{run_id}_orders.jsonl"
    summary_path = live_cycle_dir / f"{run_id}.json"
    signal_path.parent.mkdir(parents=True, exist_ok=True)

    # Merge all of today's source-policy signals (not just the latest run's file).
    # Each source run may capture different markets depending on which snapshot was
    # current at run time; London/Paris/Warsaw often appear only in early-morning
    # runs while Miami/NYC may appear in later ones.  Merging gives the branch
    # policy full coverage, deduplicating by market_id (latest snapshot wins).
    merge_stats: dict[str, Any] | None = None
    if not args.source_signal:
        merge_stats = _merge_today_signals(live_cycle_dir, args.source_policy, signal_path)
        print(
            f"[policy_branch] merged {merge_stats['merged_signals']} signals from today's "
            f"{args.source_policy} runs → {signal_path.name}",
            flush=True,
        )
    else:
        shutil.copyfile(source_signal_path, signal_path)

    live_config = {
        "city_pool": str(args.city_pool),
        "sizing_mode": str(args.sizing_mode),
        "max_order_notional": float(args.max_order_notional),
        "fixed_order_shares": float(args.fixed_order_shares),
        "max_order_shares": float(args.max_order_shares),
        "min_edge": float(args.min_edge),
        "min_entry_price": float(args.min_entry_price),
        "max_entry_price": float(args.max_entry_price),
        "execution_policy": str(args.execution_policy),
        "source_policy": str(args.source_policy),
        "min_quote_edge": float(args.min_quote_edge),
        "max_quote_spread": float(args.max_quote_spread),
        "max_mid_drift": float(args.max_mid_drift),
        "quote_improvement_ticks": int(args.quote_improvement_ticks),
        "wide_spread_shade_ticks": int(args.wide_spread_shade_ticks),
        "adverse_selection_spread_fraction": float(args.adverse_selection_spread_fraction),
        "source_signal_path": str(source_signal_path),
        "source_summary_path": str(source_summary_path) if source_summary_path else "",
    }

    planner_cmd = [
        sys.executable,
        "scripts/ops/weather_trade_planner.py",
        "--signals",
        str(signal_path),
        "--out",
        str(plan_path),
        "--max-order-notional",
        str(live_config["max_order_notional"]),
        "--sizing-mode",
        str(live_config["sizing_mode"]),
        "--fixed-order-shares",
        str(live_config["fixed_order_shares"]),
        "--max-order-shares",
        str(live_config["max_order_shares"]),
        "--min-edge",
        str(live_config["min_edge"]),
        "--min-entry-price",
        str(live_config["min_entry_price"]),
        "--max-entry-price",
        str(live_config["max_entry_price"]),
        "--execution-policy",
        str(live_config["execution_policy"]),
        "--min-quote-edge",
        str(live_config["min_quote_edge"]),
        "--max-quote-spread",
        str(live_config["max_quote_spread"]),
        "--max-mid-drift",
        str(live_config["max_mid_drift"]),
        "--quote-improvement-ticks",
        str(live_config["quote_improvement_ticks"]),
        "--wide-spread-shade-ticks",
        str(live_config["wide_spread_shade_ticks"]),
        "--adverse-selection-spread-fraction",
        str(live_config["adverse_selection_spread_fraction"]),
        "--enable-live",
        "--accepted-only",
    ]
    planner_run = _run(planner_cmd, timeout=90)
    planner = _load_json_from_output(planner_run["output"])
    live_dedup = _filter_plan_file_for_live_dedup(
        plan_path,
        _prior_submitted_live_keys(live_path.parent, exclude_path=live_path),
    )
    planner["live_dedup"] = live_dedup
    if live_dedup["plans_before"] != live_dedup["plans_after"]:
        planner["accepted_before_live_dedup"] = planner.get("accepted", 0)
        planner["accepted"] = live_dedup["plans_after"]
        planner["plans"] = live_dedup["plans_after"]

    executor: dict[str, Any] = {
        "live_requested": False,
        "live_orders": 0,
        "live_errors": 0,
        "paper_written": 0,
        "skipped": "dry_run_live",
    }
    executor_run: dict[str, Any] = {"returncode": 0, "output": ""}
    balance_preflight = _clob_balance_status() if int(planner.get("accepted", 0) or 0) > 0 else {"ok_to_submit": False}
    if not args.dry_run_live and int(planner.get("accepted", 0) or 0) > 0 and bool(balance_preflight.get("ok_to_submit")):
        executor_cmd = [
            sys.executable,
            "scripts/ops/weather_order_executor.py",
            "--plans",
            str(plan_path),
            "--paper-out",
            str(paper_path),
            "--live-out",
            str(live_path),
            "--live",
            "--confirm-live",
            "--no-telegram",
        ]
        executor_run = _run(executor_cmd, timeout=180)
        executor = _load_json_from_output(executor_run["output"])
        executor["balance_preflight"] = balance_preflight
    elif int(planner.get("accepted", 0) or 0) > 0 and not args.dry_run_live:
        executor["skipped"] = "balance_allowance_preflight"
        executor["balance_preflight"] = balance_preflight

    signal_count = sum(1 for line in signal_path.read_text(encoding="utf-8").splitlines() if line.strip())
    signals = {
        "out": str(signal_path),
        "source_signal_path": str(source_signal_path),
        "signals": signal_count,
    }
    if merge_stats is not None:
        signals["merge_stats"] = merge_stats
    sync = {"cmd": ["shared_signal_branch"], "returncode": 0, "output": f"source_signal={source_signal_path}"}
    signal_run_cmd = (
        ["merge_today_signals", args.source_policy, str(signal_path)]
        if not args.source_signal
        else ["copy", str(source_signal_path), str(signal_path)]
    )
    signal_run = {"cmd": signal_run_cmd, "returncode": 0, "output": ""}
    errors = _read_live_errors(live_path)
    contract_alerts = _build_live_contract_alerts(
        config=live_config,
        sync=sync,
        signal_run=signal_run,
        planner_run=planner_run,
        signals=signals,
        planner=planner,
        executor=executor,
        signal_path=signal_path,
        plan_path=plan_path,
        live_path=live_path,
        errors=errors,
    )
    contract_alerts.extend(_branch_signal_alerts(merge_stats, signal_count))
    summary = {
        "run_id": run_id,
        "config": live_config,
        "sync": sync,
        "signal_run": signal_run,
        "signals": signals,
        "planner_run": planner_run,
        "planner": planner,
        "executor_run": executor_run,
        "executor": executor,
        "paths": {
            "signal": str(signal_path),
            "plan": str(plan_path),
            "paper": str(paper_path),
            "live": str(live_path),
            "summary": str(summary_path),
        },
        "live_errors": errors,
        "contract_alerts": contract_alerts,
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    if not args.no_telegram:
        _send_branch_telegram(
            source_summary=source_summary,
            branch_summary=summary,
            dry_run_live=bool(args.dry_run_live),
        )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if planner_run["returncode"] == 0 else 1


if __name__ == "__main__":
    os.chdir(ROOT)
    raise SystemExit(main())
