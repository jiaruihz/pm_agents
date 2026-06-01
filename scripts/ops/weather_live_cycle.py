#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import requests

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.platform.notification.telegram import send_telegram_message_sync
from src.strategies.weather_edge_v1.tools.live_state import pause_live, read_live_state, resume_live, status_text


def _utc_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _run(cmd: List[str], *, timeout: int = 180) -> Dict[str, Any]:
    proc = subprocess.run(
        cmd,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
    )
    return {
        "cmd": cmd,
        "returncode": proc.returncode,
        "output": proc.stdout[-8000:],
    }


def _load_json_from_output(output: str) -> Dict[str, Any]:
    end = output.rfind("}")
    if end < 0:
        return {}
    starts = [idx for idx, char in enumerate(output[:end]) if char == "{"]
    for start in reversed(starts):
        try:
            parsed = json.loads(output[start : end + 1])
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


def _read_live_errors(path: Path, limit: int = 5) -> List[str]:
    if not path.exists():
        return []
    errors: List[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if row.get("status") != "error":
            continue
        response = row.get("exchange_response") or {}
        err = str(response.get("error") or response)
        label = f"{row.get('city')} {row.get('bracket')}: {err}"
        errors.append(label[:300])
        if len(errors) >= limit:
            break
    return errors


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _sample_labels(rows: List[Dict[str, Any]], field: str, limit: int = 5) -> str:
    values: List[str] = []
    seen: Set[str] = set()
    for row in rows:
        value = str(row.get(field) or "").strip()
        if not value or value in seen:
            continue
        values.append(value)
        seen.add(value)
        if len(values) >= limit:
            break
    return ", ".join(values) if values else "-"


def _slug(value: Any, default: str = "default") -> str:
    text = str(value or "").strip()
    if not text:
        text = default
    return "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in text)


def _infer_strategy_instance(row: Dict[str, Any]) -> str:
    explicit = str(row.get("strategy_instance") or "").strip()
    if explicit:
        return explicit
    policy = str(row.get("execution_policy") or "").strip() or "unknown_policy"
    window = str(row.get("entry_price_window") or "").strip()
    side = str(row.get("signal_side") or "").strip().upper()
    if policy == "mid_price_core_v1":
        if window == "0.20-0.45" or window == "0.35-0.65":
            return "mid_price_core_v1_side_band"
        if window == "0.25-0.75":
            return "mid_price_core_v1_25_75"
    if policy == "mid_price_core_v2":
        return "mid_price_core_v2_25_75"
    suffix = window.replace(".", "").replace("-", "_") if window else side.lower() or "default"
    return f"{policy}_{suffix}"


def _validate_rows_for_live_contract(
    *,
    rows: List[Dict[str, Any]],
    label: str,
    expected_city_pool: str,
    expected_sizing_mode: str,
    expected_notional: float,
    min_entry_price: float,
    max_entry_price: float,
    yes_window: Optional[str] = None,
    no_window: Optional[str] = None,
    notional_tolerance: float = 0.05,
) -> List[str]:
    alerts: List[str] = []
    if expected_city_pool and expected_city_pool.lower() != "all":
        bad_pool = [row for row in rows if str(row.get("city_pool") or "").strip() != expected_city_pool]
        if bad_pool:
            alerts.append(
                f"{label} 出现非 {expected_city_pool} city_pool："
                f"{_sample_labels(bad_pool, 'city')}（{len(bad_pool)} 条）"
            )

    global_window = f"{min_entry_price:.2f}-{max_entry_price:.2f}"

    def _expected_window(row: Dict[str, Any]) -> str:
        side = str(row.get("signal_side") or row.get("side") or "").upper()
        if side == "BUY_YES" and yes_window:
            return yes_window
        if side == "BUY_NO" and no_window:
            return no_window
        return global_window

    bad_window = [
        row
        for row in rows
        if str(row.get("entry_price_window") or _expected_window(row)) != _expected_window(row)
    ]
    if bad_window:
        alerts.append(f"{label} entry_price_window 偏离预期：{len(bad_window)} 条。")

    if expected_sizing_mode:
        bad_sizing = [row for row in rows if str(row.get("sizing_mode") or "").strip() != expected_sizing_mode]
        if bad_sizing:
            alerts.append(f"{label} sizing_mode 偏离 {expected_sizing_mode}：{len(bad_sizing)} 条。")

    if expected_sizing_mode == "notional" and expected_notional > 0:
        bad_notional = [
            row
            for row in rows
            if _to_float(row.get("notional"), 0.0)
            > (_to_float(row.get("order_notional_cap"), 0.0) or expected_notional) + notional_tolerance
        ]
        if bad_notional:
            alerts.append(
                f"{label} notional 超过上限 {expected_notional:.2f}："
                f"{_sample_labels(bad_notional, 'city')}（{len(bad_notional)} 条）"
            )
    return alerts


def _build_live_contract_alerts(
    *,
    config: Dict[str, Any],
    sync: Dict[str, Any],
    signal_run: Dict[str, Any],
    planner_run: Dict[str, Any],
    signals: Dict[str, Any],
    planner: Dict[str, Any],
    executor: Dict[str, Any],
    signal_path: Path,
    plan_path: Path,
    live_path: Path,
    errors: List[str],
) -> List[str]:
    alerts: List[str] = []
    if int(sync.get("returncode", 1)) != 0:
        alerts.append("数据同步失败。")
    if int(signal_run.get("returncode", 1)) != 0:
        alerts.append("signal builder 执行失败。")
    if int(planner_run.get("returncode", 1)) != 0:
        alerts.append("trade planner 执行失败。")
    if not signals:
        alerts.append("signal builder 输出解析为空。")
    if not planner:
        alerts.append("trade planner 输出解析为空。")
    if int(executor.get("live_errors", 0) or 0) > 0 or errors:
        alerts.append(f"实盘 executor 出现失败：{int(executor.get('live_errors', 0) or 0)} 条。")

    expected_city_pool = str(config.get("city_pool") or "").strip()
    expected_sizing_mode = str(config.get("sizing_mode") or "").strip()
    expected_notional = _to_float(config.get("max_order_notional"), 0.0)
    min_entry_price = _to_float(config.get("min_entry_price"), 0.25)
    max_entry_price = _to_float(config.get("max_entry_price"), 0.75)

    def _side_window(lo_key: str, hi_key: str) -> Optional[str]:
        lo = config.get(lo_key)
        hi = config.get(hi_key)
        if lo is None or hi is None:
            return None
        return f"{_to_float(lo, 0.0):.2f}-{_to_float(hi, 0.0):.2f}"

    yes_window = _side_window("yes_min_entry_price", "yes_max_entry_price")
    no_window = _side_window("no_min_entry_price", "no_max_entry_price")
    signal_rows = _read_jsonl(signal_path)
    plan_rows = [row for row in _read_jsonl(plan_path) if str(row.get("status") or "") == "accepted"]
    live_rows = [row for row in _read_jsonl(live_path) if str(row.get("status") or "") == "submitted"]

    signal_count = int(signals.get("signals", 0) or 0) if isinstance(signals, dict) else 0
    planner_signal_count = int(planner.get("signals", 0) or 0) if isinstance(planner, dict) else 0
    planner_accepted = int(planner.get("accepted", 0) or 0) if isinstance(planner, dict) else 0
    if signal_count != len(signal_rows):
        alerts.append(f"signals summary/file count mismatch: summary={signal_count}, file={len(signal_rows)}.")
    if planner_signal_count != len(signal_rows):
        alerts.append(f"planner input count mismatch: planner={planner_signal_count}, signal_file={len(signal_rows)}.")
    if planner_accepted != len(plan_rows):
        alerts.append(f"planner accepted/file count mismatch: planner={planner_accepted}, accepted_file={len(plan_rows)}.")

    alerts.extend(
        _validate_rows_for_live_contract(
            rows=signal_rows,
            label="signals",
            expected_city_pool=expected_city_pool,
            expected_sizing_mode="",
            expected_notional=0.0,
            min_entry_price=min_entry_price,
            max_entry_price=max_entry_price,
            yes_window=yes_window,
            no_window=no_window,
        )
    )
    alerts.extend(
        _validate_rows_for_live_contract(
            rows=plan_rows,
            label="plans",
            expected_city_pool=expected_city_pool,
            expected_sizing_mode=expected_sizing_mode,
            expected_notional=expected_notional,
            min_entry_price=min_entry_price,
            max_entry_price=max_entry_price,
            yes_window=yes_window,
            no_window=no_window,
        )
    )
    alerts.extend(
        _validate_rows_for_live_contract(
            rows=live_rows,
            label="live orders",
            expected_city_pool=expected_city_pool,
            expected_sizing_mode=expected_sizing_mode,
            expected_notional=expected_notional,
            min_entry_price=min_entry_price,
            max_entry_price=max_entry_price,
            yes_window=yes_window,
            no_window=no_window,
        )
    )
    return alerts


def _write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _live_dedup_key(row: Dict[str, Any]) -> Tuple[str, str, str, str, str, str]:
    market_key = str(row.get("market_id") or "").strip()
    if not market_key:
        city = str(row.get("city") or "").strip()
        bracket = str(row.get("bracket") or "").strip()
        market_key = f"{city}|{bracket}" if city and bracket else str(row.get("token_id") or "").strip()
    return (
        str(row.get("strategy") or "weather_edge_v1").strip(),
        _infer_strategy_instance(row),
        str(row.get("target_date") or "").strip(),
        market_key,
        str(row.get("execution_policy") or "").strip(),
        str(row.get("child_order_role") or "single").strip() or "single",
    )


def _live_history_dirs(live_dir: Path) -> List[Path]:
    dirs = [live_dir]
    remote_live = live_dir.parent / "remote_pm_agent" / "live"
    if remote_live != live_dir:
        dirs.append(remote_live)
    return dirs


def _live_market_key(row: Dict[str, Any]) -> Tuple[str, str]:
    market_key = str(row.get("market_id") or "").strip()
    if not market_key:
        city = str(row.get("city") or "").strip()
        bracket = str(row.get("bracket") or "").strip()
        market_key = f"{city}|{bracket}" if city and bracket else str(row.get("token_id") or "").strip()
    return (str(row.get("target_date") or "").strip(), market_key)


def _live_signal_side(row: Dict[str, Any]) -> str:
    return str(row.get("signal_side") or row.get("side") or "").strip().upper()


def _live_exposure_key(row: Dict[str, Any]) -> Tuple[str, str, str]:
    target_date, market_key = _live_market_key(row)
    return (target_date, market_key, _infer_strategy_instance(row))


def _prior_live_market_notional(live_dir: Path, *, exclude_path: Path) -> Dict[Tuple[str, str, str], float]:
    exposure: Dict[Tuple[str, str, str], float] = {}
    excluded = exclude_path.resolve()
    for history_dir in _live_history_dirs(live_dir):
        if not history_dir.exists():
            continue
        for path in sorted(history_dir.glob("*.jsonl")):
            if path.resolve() == excluded:
                continue
            for row in _read_jsonl(path):
                if row.get("record_type") != "weather_edge_live_order":
                    continue
                if str(row.get("status") or "").strip() != "submitted":
                    continue
                key = _live_exposure_key(row)
                if not key[0] or not key[1] or not key[2]:
                    continue
                notional = _to_float(row.get("posted_notional", row.get("notional")), 0.0)
                exposure[key] = exposure.get(key, 0.0) + max(0.0, notional)
    return exposure


def _prior_live_market_sides(live_dir: Path, *, exclude_path: Path) -> Dict[Tuple[str, str], Set[str]]:
    sides: Dict[Tuple[str, str], Set[str]] = {}
    excluded = exclude_path.resolve()
    for history_dir in _live_history_dirs(live_dir):
        if not history_dir.exists():
            continue
        for path in sorted(history_dir.glob("*.jsonl")):
            if path.resolve() == excluded:
                continue
            for row in _read_jsonl(path):
                if row.get("record_type") != "weather_edge_live_order":
                    continue
                if str(row.get("status") or "").strip() != "submitted":
                    continue
                key = _live_market_key(row)
                side = _live_signal_side(row)
                if key[0] and key[1] and side:
                    sides.setdefault(key, set()).add(side)
    return sides


def _filter_plan_file_for_live_exposure_cap(
    plan_path: Path,
    prior_exposure: Dict[Tuple[str, str, str], float],
    *,
    max_market_notional: float,
    prior_market_sides: Optional[Dict[Tuple[str, str], Set[str]]] = None,
    block_opposite_side: bool = True,
) -> Dict[str, Any]:
    rows = _read_jsonl(plan_path)
    if max_market_notional <= 0:
        return {
            "enabled": False,
            "max_market_notional": float(max_market_notional),
            "plans_before": len(rows),
            "plans_after": len(rows),
            "skipped_exposure_cap": 0,
            "skipped_opposite_side": 0,
        }
    kept: List[Dict[str, Any]] = []
    exposure = dict(prior_exposure)
    market_sides = {key: set(value) for key, value in (prior_market_sides or {}).items()}
    skipped = 0
    skipped_opposite = 0
    skipped_notional = 0.0
    for row in rows:
        market_key = _live_market_key(row)
        exposure_key = _live_exposure_key(row)
        side = _live_signal_side(row)
        existing_sides = market_sides.get(market_key, set())
        if block_opposite_side and market_key[0] and market_key[1] and side and existing_sides and side not in existing_sides:
            skipped_opposite += 1
            continue
        plan_notional = max(0.0, _to_float(row.get("posted_notional", row.get("notional")), 0.0))
        current = exposure.get(exposure_key, 0.0)
        if (
            exposure_key[0]
            and exposure_key[1]
            and exposure_key[2]
            and plan_notional > 0
            and current + plan_notional > max_market_notional + 1e-9
        ):
            skipped += 1
            skipped_notional += plan_notional
            continue
        kept.append(row)
        if exposure_key[0] and exposure_key[1] and exposure_key[2]:
            exposure[exposure_key] = current + plan_notional
        if market_key[0] and market_key[1] and side:
            market_sides.setdefault(market_key, set()).add(side)
    if len(kept) != len(rows):
        _write_jsonl(plan_path, kept)
    return {
        "enabled": True,
        "max_market_notional": float(max_market_notional),
        "plans_before": len(rows),
        "plans_after": len(kept),
        "skipped_exposure_cap": skipped,
        "skipped_opposite_side": skipped_opposite,
        "skipped_notional": round(skipped_notional, 6),
    }

def _prior_submitted_live_keys(live_dir: Path, *, exclude_path: Path) -> Set[Tuple[str, str, str, str, str, str]]:
    keys: Set[Tuple[str, str, str, str, str, str]] = set()
    if not live_dir.exists():
        return keys
    for path in sorted(live_dir.glob("*.jsonl")):
        if path.resolve() == exclude_path.resolve():
            continue
        for row in _read_jsonl(path):
            if row.get("record_type") != "weather_edge_live_order":
                continue
            if str(row.get("status") or "").strip() != "submitted":
                continue
            key = _live_dedup_key(row)
            if key[2] and key[3]:
                keys.add(key)
    return keys


def _filter_plan_file_for_live_dedup(plan_path: Path, prior_keys: Set[Tuple[str, str, str, str, str, str]]) -> Dict[str, Any]:
    rows = _read_jsonl(plan_path)
    kept: List[Dict[str, Any]] = []
    seen_this_run: Set[Tuple[str, str, str, str, str, str]] = set()
    skipped_prior = 0
    skipped_same_run = 0
    for row in rows:
        key = _live_dedup_key(row)
        if key[2] and key[3] and key in prior_keys:
            skipped_prior += 1
            continue
        if key[2] and key[3] and key in seen_this_run:
            skipped_same_run += 1
            continue
        if key[2] and key[3]:
            seen_this_run.add(key)
        kept.append(row)
    if len(kept) != len(rows):
        _write_jsonl(plan_path, kept)
    return {
        "plans_before": len(rows),
        "plans_after": len(kept),
        "skipped_prior_submitted_live": skipped_prior,
        "skipped_same_run_duplicate": skipped_same_run,
    }


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _to_usdc(raw_value: Any) -> str:
    value = _to_int(raw_value, 0)
    return f"{value / 1_000_000:.2f} USDC"


def _short_path(path: Any) -> str:
    text = str(path or "").strip()
    if not text:
        return "-"
    try:
        return str(Path(text).relative_to(ROOT))
    except Exception:
        return text


def _first_sentence_for_skip(skipped: Any) -> str:
    reason = str(skipped or "").strip()
    if reason == "dry_run_live":
        return "本轮只生成了交易计划，没有尝试真实下单。"
    if reason == "no_accepted_plans_after_live_dedup":
        return "本轮候选计划已被 live 去重过滤，没有新的真实订单需要提交。"
    if reason == "no_accepted_plans":
        return "本轮没有通过筛选的交易计划，因此没有提交真实订单。"
    if reason == "paused_by_telegram":
        return "实盘当前是暂停状态。本轮已同步数据并生成计划，但没有下单。"
    if reason == "balance_allowance_preflight":
        return "下单前检查没有通过，可能是余额或授权不足。本轮没有提交订单。"
    if reason:
        return f"本轮未提交实盘订单，原因：{reason}。"
    return "本轮已完成。"


def _clob_balance_status() -> Dict[str, Any]:
    try:
        from py_clob_client_v2.client import ClobClient
        from py_clob_client_v2.clob_types import AssetType, BalanceAllowanceParams
        from py_clob_client_v2.constants import POLYGON
    except Exception as exc:
        return {
            "ok_to_submit": False,
            "error": f"{type(exc).__name__}: {exc}",
        }

    host = (
        os.getenv("CLOB_BASE_URL", "").strip()
        or os.getenv("PM_API_BASE_URL", "").strip()
        or "https://clob.polymarket.com"
    )
    private_key = os.getenv("POLYGON_WALLET_PRIVATE_KEY", "").strip() or os.getenv("PM", "").strip()
    funder = os.getenv("PM_ADDRESS", "").strip()
    if not private_key:
        return {"ok_to_submit": False, "error": "missing private key"}

    try:
        signer = ClobClient(host, chain_id=POLYGON, key=private_key).get_address()
        signature_type = 1 if funder and signer and funder.lower() != signer.lower() else 0
        client = ClobClient(
            host,
            chain_id=POLYGON,
            key=private_key,
            signature_type=signature_type,
            funder=funder or None,
        )
        client.set_api_creds(client.derive_api_key())
        payload = client.get_balance_allowance(
            BalanceAllowanceParams(asset_type=AssetType.COLLATERAL, signature_type=signature_type)
        )
    except Exception as exc:
        return {
            "ok_to_submit": False,
            "error": f"{type(exc).__name__}: {exc}",
            "funder": funder,
        }

    balance = _to_int(payload.get("balance") if isinstance(payload, dict) else 0)
    allowances = payload.get("allowances", {}) if isinstance(payload, dict) else {}
    allowance_values = [_to_int(v) for v in allowances.values()] if isinstance(allowances, dict) else []
    max_allowance = max(allowance_values, default=0)
    return {
        "ok_to_submit": balance > 0 and max_allowance > 0,
        "balance": balance,
        "max_allowance": max_allowance,
        "signature_type": signature_type,
        "signer": signer,
        "funder": funder,
        "raw": payload,
    }


def _send_summary(
    *,
    run_id: str,
    config: Dict[str, Any],
    sync: Dict[str, Any],
    signals: Dict[str, Any],
    planner: Dict[str, Any],
    executor: Dict[str, Any],
    live_path: Path,
    errors: List[str],
    contract_alerts: List[str],
) -> None:
    sync_ok = int(sync.get("returncode", 1)) == 0
    signal_ok = bool(signals)
    planner_ok = bool(planner)
    accepted = int(planner.get("accepted", 0) or 0)
    live_orders = int(executor.get("live_orders", 0) or 0)
    live_errors = int(executor.get("live_errors", 0) or 0)
    paper_written = int(executor.get("paper_written", 0) or 0)
    live_written = int(executor.get("live_written", 0) or 0)
    skipped_disabled = int(executor.get("live_skipped_disabled", 0) or 0)
    dedup = planner.get("live_dedup") if isinstance(planner.get("live_dedup"), dict) else {}
    skipped_prior = int(dedup.get("skipped_prior_submitted_live", 0) or 0)
    skipped_same_run = int(dedup.get("skipped_same_run_duplicate", 0) or 0)

    if contract_alerts:
        headline = f"告警：本轮发现 {len(contract_alerts)} 个实盘约束异常。"
    elif live_orders > 0 and live_errors == 0:
        headline = f"本轮已提交 {live_written} 笔真实挂单。"
    elif live_errors > 0:
        headline = f"本轮尝试下单，但有 {live_errors} 笔失败；没有主动吃单。"
    else:
        headline = _first_sentence_for_skip(executor.get("skipped"))

    lines = [
        "【天气策略实盘】",
        headline,
        "",
        f"数据同步：{'成功' if sync_ok else '失败'}。",
        f"信号筛选：从 {int(signals.get('records', 0) or 0)} 条快照记录里，选出 {int(signals.get('signals', 0) or 0)} 条候选。",
        f"交易计划：{accepted} 条通过风控；去重跳过 {skipped_prior + skipped_same_run} 条。",
        f"下单结果：成功 {live_written} 笔，失败 {live_errors} 笔。",
    ]
    if paper_written:
        lines.append(f"模拟记录：写入 {paper_written} 条。")
    if skipped_disabled:
        lines.append(f"未启用实盘而跳过：{skipped_disabled} 条。")
    if not signal_ok or not planner_ok:
        lines.append("提醒：信号或计划输出解析为空，需要检查本轮日志。")

    if errors:
        lines.extend(["", "失败摘要："])
        lines.extend(f"- {err}" for err in errors)
    if contract_alerts:
        lines.extend(["", "约束告警："])
        lines.extend(f"- {alert}" for alert in contract_alerts[:8])

    balance = executor.get("balance_preflight") if isinstance(executor, dict) else None
    if isinstance(balance, dict):
        if balance.get("ok_to_submit"):
            lines.extend(["", f"资金和授权检查：通过，可用余额约 {_to_usdc(balance.get('balance'))}。"])
        else:
            lines.extend(
                [
                    "",
                    "资金和授权检查：未通过，已跳过真实下单。",
                    f"余额：{_to_usdc(balance.get('balance'))}",
                ]
            )

    if int(executor.get("live_errors", 0) or 0) > 0:
        lines.extend(
            [
                "",
                "需要处理：优先检查钱包余额、授权和交易所 API 鉴权。策略默认只挂单，不会主动吃单。",
            ]
        )
    if executor.get("skipped") == "balance_allowance_preflight":
        lines.extend(
            [
                "",
                "需要处理：余额或授权为 0，本轮已安全跳过真实下单。",
            ]
        )
    lines.extend(
        [
            "",
            "实盘参数：",
            f"- strategy_instance={config.get('strategy_instance', '-')}",
            f"- city_pool={config.get('city_pool')}",
            f"- sizing_mode={config.get('sizing_mode')}",
            f"- max_order_notional={float(config.get('max_order_notional', 0.0)):.2f}",
            f"- max_order_shares={float(config.get('max_order_shares', 0.0)):.2f}",
            f"- entry_price_window={float(config.get('min_entry_price', 0.0)):.2f}-{float(config.get('max_entry_price', 0.0)):.2f}",
            f"- min_edge={float(config.get('min_edge', 0.0)):.2f}",
            f"- execution_policy={config.get('execution_policy', '-')}",
            f"- min_quote_edge={float(config.get('min_quote_edge', 0.0)):.2f}",
            f"- max_quote_spread={float(config.get('max_quote_spread', 0.0)):.2f}",
            "",
            "排查信息：",
            f"- 运行编号：{run_id}",
            f"- 快照：{_short_path(signals.get('snapshot'))}",
            f"- 计划文件：{_short_path(planner.get('out'))}",
            f"- 实盘记录：{_short_path(live_path)}",
        ]
    )
    try:
        send_telegram_message_sync("\n".join(lines))
    except Exception as exc:
        print(f"[WARN] telegram summary send failed: {type(exc).__name__}: {exc}")


def _telegram_post(method: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        return {}
    url = f"https://api.telegram.org/bot{token}/{method}"
    resp = requests.post(url, json=payload, timeout=12)
    resp.raise_for_status()
    data = resp.json()
    return data if isinstance(data, dict) else {}


def _send_text(text: str) -> None:
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not chat_id:
        return
    try:
        _telegram_post("sendMessage", {"chat_id": chat_id, "text": text})
    except Exception as exc:
        print(f"[WARN] telegram command reply failed: {type(exc).__name__}: {exc}")


def _telegram_control_running(state_dir: Path) -> bool:
    pid_path = state_dir / "telegram_control.pid"
    if not pid_path.exists():
        return False
    try:
        pid = int(pid_path.read_text(encoding="utf-8").strip())
    except Exception:
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _handle_telegram_commands(state_dir: Path) -> Dict[str, Any]:
    if _telegram_control_running(state_dir):
        return {"commands": [], **read_live_state(state_dir), "skipped": "telegram_control_running"}

    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not chat_id or not token:
        return {"commands": [], **read_live_state(state_dir)}

    offset_path = state_dir / "telegram_update_offset.txt"
    offset = 0
    if offset_path.exists():
        try:
            offset = int(offset_path.read_text().strip())
        except Exception:
            offset = 0

    payload: Dict[str, Any] = {"timeout": 0, "allowed_updates": ["message"]}
    if offset > 0:
        payload["offset"] = offset
    try:
        data = _telegram_post("getUpdates", payload)
    except Exception as exc:
        return {"commands": [], **read_live_state(state_dir), "error": f"{type(exc).__name__}: {exc}"}

    updates = data.get("result") if isinstance(data, dict) else []
    if not isinstance(updates, list):
        updates = []
    if updates:
        max_update_id = max(int(x.get("update_id", 0)) for x in updates if isinstance(x, dict))
        offset_path.write_text(str(max_update_id + 1), encoding="utf-8")

    if offset <= 0:
        return {"commands": [], **read_live_state(state_dir), "initialized": True}

    commands: List[str] = []
    for update in updates:
        if not isinstance(update, dict):
            continue
        message = update.get("message") or {}
        if not isinstance(message, dict):
            continue
        chat = message.get("chat") or {}
        if str(chat.get("id") or "") != str(chat_id):
            continue
        text = str(message.get("text") or "").strip().lower()
        if not text:
            continue
        if text in {"/pause_weather", "pause", "暂停", "暂停实盘"}:
            pause_live(state_dir, reason="Telegram 命令暂停", source="telegram")
            commands.append("pause")
            _send_text("已暂停天气策略实盘。后续循环仍会同步数据和生成计划，但不会提交真实订单。")
        elif text in {"/resume_weather", "resume", "继续", "恢复", "恢复实盘"}:
            resume_live(state_dir)
            commands.append("resume")
            _send_text("已恢复天气策略实盘。下一轮如果有合格计划，会只按挂单方式尝试提交，不主动吃单。")
        elif text in {"/status_weather", "status", "状态"}:
            commands.append("status")
            _send_text(status_text(read_live_state(state_dir)))

    return {"commands": commands, **read_live_state(state_dir)}


def main() -> int:
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
    except ModuleNotFoundError:
        pass

    parser = argparse.ArgumentParser(description="Run one weather live cycle: sync -> signals -> plans -> maker-only executor.")
    parser.add_argument(
        "--strategy-instance",
        default=os.getenv("WEATHER_LIVE_STRATEGY_INSTANCE", os.getenv("WEATHER_STRATEGY_INSTANCE", "")),
        help="Stable strategy instance id used in summaries, filenames, and live dedup.",
    )
    parser.add_argument("--max-order-notional", type=float, default=float(os.getenv("WEATHER_LIVE_MAX_ORDER_NOTIONAL", "5.00")))
    parser.add_argument(
        "--max-market-notional",
        type=float,
        default=float(os.getenv("WEATHER_LIVE_MAX_MARKET_NOTIONAL", "0")),
        help="Max submitted live notional per target_date+market_id per strategy instance. 0 defaults to max-order-notional.",
    )
    parser.add_argument("--sizing-mode", choices=("notional", "fixed_shares"), default=os.getenv("WEATHER_LIVE_SIZING_MODE", "notional"))
    parser.add_argument("--fixed-order-shares", type=float, default=float(os.getenv("WEATHER_LIVE_FIXED_ORDER_SHARES", "10.0")))
    parser.add_argument(
        "--max-order-shares",
        type=float,
        default=float(os.getenv("WEATHER_LIVE_MAX_ORDER_SHARES", os.getenv("WEATHER_LIVE_MAX_POSITION", "25.0"))),
    )
    parser.add_argument("--max-position", type=float, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--min-order-shares", type=float, default=float(os.getenv("WEATHER_LIVE_MIN_ORDER_SHARES", "5.0")))
    parser.add_argument("--city-pool", default=os.getenv("WEATHER_LIVE_CITY_POOL", "t1_trading"))
    parser.add_argument(
        "--allowed-cities",
        default=os.getenv("WEATHER_LIVE_ALLOWED_CITIES", ""),
        help="Comma-separated city allowlist after city_pool filtering. Empty means all cities in the pool.",
    )
    parser.add_argument("--min-edge", type=float, default=float(os.getenv("WEATHER_LIVE_MIN_EDGE", "0.10")))
    parser.add_argument("--min-entry-price", type=float, default=float(os.getenv("WEATHER_LIVE_MIN_ENTRY_PRICE", "0.25")))
    parser.add_argument("--max-entry-price", type=float, default=float(os.getenv("WEATHER_LIVE_MAX_ENTRY_PRICE", "0.75")))
    # Per-side entry band / edge (2026-05-31 entry-band strategy; see
    # docs/WEATHER_ENTRY_BAND_AND_SIZING_DESIGN.md §7). Sizing stays flat;
    # only the per-side selection gate differs. Env-overridable.
    parser.add_argument("--yes-min-entry-price", type=float, default=float(os.getenv("WEATHER_LIVE_YES_MIN_ENTRY_PRICE", "0.20")))
    parser.add_argument("--yes-max-entry-price", type=float, default=float(os.getenv("WEATHER_LIVE_YES_MAX_ENTRY_PRICE", "0.45")))
    parser.add_argument("--yes-min-edge", type=float, default=float(os.getenv("WEATHER_LIVE_YES_MIN_EDGE", "0.20")))
    parser.add_argument("--no-min-entry-price", type=float, default=float(os.getenv("WEATHER_LIVE_NO_MIN_ENTRY_PRICE", "0.35")))
    parser.add_argument("--no-max-entry-price", type=float, default=float(os.getenv("WEATHER_LIVE_NO_MAX_ENTRY_PRICE", "0.65")))
    parser.add_argument("--no-min-edge", type=float, default=float(os.getenv("WEATHER_LIVE_NO_MIN_EDGE", "0.10")))
    parser.add_argument(
        "--snapshot-lookback-minutes",
        type=float,
        default=float(os.getenv("WEATHER_LIVE_SNAPSHOT_LOOKBACK_MINUTES", "90")),
    )
    parser.add_argument(
        "--min-hours-to-settle",
        type=float,
        default=float(os.getenv("WEATHER_LIVE_MIN_HOURS_TO_SETTLE", "22")),
    )
    parser.add_argument(
        "--max-hours-to-settle",
        type=float,
        default=float(os.getenv("WEATHER_LIVE_MAX_HOURS_TO_SETTLE", "28")),
    )
    parser.add_argument(
        "--execution-policy",
        choices=("mid_price_core_v1", "maker_queue_v1", "maker_queue_v2", "mid_price_core_v2"),
        default=os.getenv("WEATHER_LIVE_EXECUTION_POLICY", "mid_price_core_v1"),
    )
    parser.add_argument("--min-quote-edge", type=float, default=float(os.getenv("WEATHER_LIVE_MIN_QUOTE_EDGE", "0.03")))
    parser.add_argument("--max-quote-spread", type=float, default=float(os.getenv("WEATHER_LIVE_MAX_QUOTE_SPREAD", "0.12")))
    parser.add_argument("--max-mid-drift", type=float, default=float(os.getenv("WEATHER_LIVE_MAX_MID_DRIFT", "0.10")))
    parser.add_argument(
        "--quote-improvement-ticks",
        type=int,
        default=int(os.getenv("WEATHER_LIVE_QUOTE_IMPROVEMENT_TICKS", "1")),
    )
    parser.add_argument(
        "--wide-spread-shade-ticks",
        type=int,
        default=int(os.getenv("WEATHER_LIVE_WIDE_SPREAD_SHADE_TICKS", "1")),
    )
    parser.add_argument(
        "--adverse-selection-spread-fraction",
        type=float,
        default=float(os.getenv("WEATHER_LIVE_ADVERSE_SELECTION_SPREAD_FRACTION", "0.50")),
    )
    parser.add_argument("--low-band-ceiling", type=float, default=float(os.getenv("WEATHER_LIVE_LOW_BAND_CEILING", "0.40")))
    parser.add_argument("--high-band-floor", type=float, default=float(os.getenv("WEATHER_LIVE_HIGH_BAND_FLOOR", "0.55")))
    parser.add_argument(
        "--split-enabled",
        action=argparse.BooleanOptionalAction,
        default=os.getenv("WEATHER_LIVE_SPLIT_ENABLED", "1").strip().lower() not in {"0", "false", "no"},
    )
    parser.add_argument("--taker-fraction", type=float, default=float(os.getenv("WEATHER_LIVE_TAKER_FRACTION", "0.50")))
    parser.add_argument("--split-min-edge", type=float, default=float(os.getenv("WEATHER_LIVE_SPLIT_MIN_EDGE", "0.10")))
    parser.add_argument(
        "--high-band-shade-narrow",
        type=int,
        default=int(os.getenv("WEATHER_LIVE_HIGH_BAND_SHADE_NARROW", "1")),
    )
    parser.add_argument(
        "--high-band-shade-wide",
        type=int,
        default=int(os.getenv("WEATHER_LIVE_HIGH_BAND_SHADE_WIDE", "2")),
    )
    parser.add_argument("--high-band-min-edge", type=float, default=float(os.getenv("WEATHER_LIVE_HIGH_BAND_MIN_EDGE", "0.15")))
    parser.add_argument("--high-band-size-mult", type=float, default=float(os.getenv("WEATHER_LIVE_HIGH_BAND_SIZE_MULT", "0.60")))
    parser.add_argument("--dry-run-live", action="store_true", help="Stop before live executor.")
    parser.add_argument("--no-telegram", action="store_true")
    args = parser.parse_args()
    max_order_shares = float(args.max_order_shares if args.max_position is None else args.max_position)
    max_market_notional = float(args.max_market_notional) if float(args.max_market_notional) > 0 else float(args.max_order_notional)
    strategy_instance = str(args.strategy_instance or "").strip()
    if not strategy_instance:
        yes_tuple = (float(args.yes_min_entry_price), float(args.yes_max_entry_price), float(args.yes_min_edge))
        no_tuple = (float(args.no_min_entry_price), float(args.no_max_entry_price), float(args.no_min_edge))
        global_tuple = (float(args.min_entry_price), float(args.max_entry_price), float(args.min_edge))
        if str(args.execution_policy) == "mid_price_core_v1" and yes_tuple == global_tuple and no_tuple == global_tuple:
            strategy_instance = "mid_price_core_v1_25_75"
        elif str(args.execution_policy) == "mid_price_core_v1":
            strategy_instance = "mid_price_core_v1_side_band"
        elif str(args.execution_policy) == "mid_price_core_v2":
            strategy_instance = "mid_price_core_v2_25_75"
        else:
            strategy_instance = str(args.execution_policy)
    live_config = {
        "strategy_instance": strategy_instance,
        "city_pool": str(args.city_pool),
        "allowed_cities": str(args.allowed_cities),
        "sizing_mode": str(args.sizing_mode),
        "max_order_notional": float(args.max_order_notional),
        "max_market_notional": max_market_notional,
        "fixed_order_shares": float(args.fixed_order_shares),
        "max_order_shares": max_order_shares,
        "min_order_shares": float(args.min_order_shares),
        "min_edge": float(args.min_edge),
        "min_entry_price": float(args.min_entry_price),
        "max_entry_price": float(args.max_entry_price),
        "yes_min_entry_price": float(args.yes_min_entry_price),
        "yes_max_entry_price": float(args.yes_max_entry_price),
        "yes_min_edge": float(args.yes_min_edge),
        "no_min_entry_price": float(args.no_min_entry_price),
        "no_max_entry_price": float(args.no_max_entry_price),
        "no_min_edge": float(args.no_min_edge),
        "snapshot_lookback_minutes": float(args.snapshot_lookback_minutes),
        "min_hours_to_settle": float(args.min_hours_to_settle),
        "max_hours_to_settle": float(args.max_hours_to_settle),
        "execution_policy": str(args.execution_policy),
        "min_quote_edge": float(args.min_quote_edge),
        "max_quote_spread": float(args.max_quote_spread),
        "max_mid_drift": float(args.max_mid_drift),
        "quote_improvement_ticks": int(args.quote_improvement_ticks),
        "wide_spread_shade_ticks": int(args.wide_spread_shade_ticks),
        "adverse_selection_spread_fraction": float(args.adverse_selection_spread_fraction),
        "low_band_ceiling": float(args.low_band_ceiling),
        "high_band_floor": float(args.high_band_floor),
        "split_enabled": bool(args.split_enabled),
        "taker_fraction": float(args.taker_fraction),
        "split_min_edge": float(args.split_min_edge),
        "high_band_shade_narrow": int(args.high_band_shade_narrow),
        "high_band_shade_wide": int(args.high_band_shade_wide),
        "high_band_min_edge": float(args.high_band_min_edge),
        "high_band_size_mult": float(args.high_band_size_mult),
    }

    run_id = _utc_run_id()
    instance_slug = _slug(strategy_instance)
    signal_path = ROOT / "runtime" / "weather_edge_v1" / "signals" / f"live_{instance_slug}_{run_id}_signals.jsonl"
    plan_path = ROOT / "runtime" / "weather_edge_v1" / "plans" / f"live_{instance_slug}_{run_id}_trade_plans.jsonl"
    paper_path = ROOT / "runtime" / "weather_edge_v1" / "paper" / f"live_{instance_slug}_{run_id}_paper_orders.jsonl"
    live_path = ROOT / "runtime" / "weather_edge_v1" / "live" / f"live_{instance_slug}_{run_id}_orders.jsonl"
    summary_path = ROOT / "runtime" / "weather_edge_v1" / "live_cycle" / f"{run_id}_{instance_slug}.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    command_state = _handle_telegram_commands(summary_path.parent)
    paused = bool(command_state.get("paused"))

    sync = _run(["bash", "scripts/ops/sync_weather_remote.sh"], timeout=240)
    signal_cmd = [
        sys.executable,
        "scripts/ops/weather_snapshot_signal_builder.py",
        "--out",
        str(signal_path),
        "--strategy-instance",
        strategy_instance,
        "--city-pool",
        str(live_config["city_pool"]),
        "--allowed-cities",
        str(live_config["allowed_cities"]),
        "--min-edge",
        str(float(live_config["min_edge"])),
        "--min-entry-price",
        str(float(live_config["min_entry_price"])),
        "--max-entry-price",
        str(float(live_config["max_entry_price"])),
        "--yes-min-entry-price",
        str(float(live_config["yes_min_entry_price"])),
        "--yes-max-entry-price",
        str(float(live_config["yes_max_entry_price"])),
        "--yes-min-edge",
        str(float(live_config["yes_min_edge"])),
        "--no-min-entry-price",
        str(float(live_config["no_min_entry_price"])),
        "--no-max-entry-price",
        str(float(live_config["no_max_entry_price"])),
        "--no-min-edge",
        str(float(live_config["no_min_edge"])),
        "--snapshot-lookback-minutes",
        str(float(live_config["snapshot_lookback_minutes"])),
        "--min-hours-to-settle",
        str(float(live_config["min_hours_to_settle"])),
        "--max-hours-to-settle",
        str(float(live_config["max_hours_to_settle"])),
    ]
    signal_run = _run(signal_cmd, timeout=180)
    signals = _load_json_from_output(signal_run["output"])

    planner_cmd = [
        sys.executable,
        "scripts/ops/weather_trade_planner.py",
        "--signals",
        str(signal_path),
        "--out",
        str(plan_path),
        "--strategy-instance",
        strategy_instance,
        "--max-order-notional",
        str(float(live_config["max_order_notional"])),
        "--sizing-mode",
        str(live_config["sizing_mode"]),
        "--fixed-order-shares",
        str(float(live_config["fixed_order_shares"])),
        "--max-order-shares",
        str(float(live_config["max_order_shares"])),
        "--min-order-shares",
        str(float(live_config["min_order_shares"])),
        "--min-edge",
        str(float(live_config["min_edge"])),
        "--min-entry-price",
        str(float(live_config["min_entry_price"])),
        "--max-entry-price",
        str(float(live_config["max_entry_price"])),
        "--yes-min-entry-price",
        str(float(live_config["yes_min_entry_price"])),
        "--yes-max-entry-price",
        str(float(live_config["yes_max_entry_price"])),
        "--yes-min-edge",
        str(float(live_config["yes_min_edge"])),
        "--no-min-entry-price",
        str(float(live_config["no_min_entry_price"])),
        "--no-max-entry-price",
        str(float(live_config["no_max_entry_price"])),
        "--no-min-edge",
        str(float(live_config["no_min_edge"])),
        "--execution-policy",
        str(live_config["execution_policy"]),
        "--min-quote-edge",
        str(float(live_config["min_quote_edge"])),
        "--max-quote-spread",
        str(float(live_config["max_quote_spread"])),
        "--max-mid-drift",
        str(float(live_config["max_mid_drift"])),
        "--quote-improvement-ticks",
        str(int(live_config["quote_improvement_ticks"])),
        "--wide-spread-shade-ticks",
        str(int(live_config["wide_spread_shade_ticks"])),
        "--adverse-selection-spread-fraction",
        str(float(live_config["adverse_selection_spread_fraction"])),
        "--low-band-ceiling",
        str(float(live_config["low_band_ceiling"])),
        "--high-band-floor",
        str(float(live_config["high_band_floor"])),
        "--taker-fraction",
        str(float(live_config["taker_fraction"])),
        "--split-min-edge",
        str(float(live_config["split_min_edge"])),
        "--high-band-shade-narrow",
        str(int(live_config["high_band_shade_narrow"])),
        "--high-band-shade-wide",
        str(int(live_config["high_band_shade_wide"])),
        "--high-band-min-edge",
        str(float(live_config["high_band_min_edge"])),
        "--high-band-size-mult",
        str(float(live_config["high_band_size_mult"])),
        "--enable-live",
        "--accepted-only",
    ]
    if not bool(live_config["split_enabled"]):
        planner_cmd.append("--no-split-enabled")
    planner_run = _run(planner_cmd, timeout=60)
    planner = _load_json_from_output(planner_run["output"])
    prior_live_keys = _prior_submitted_live_keys(live_path.parent, exclude_path=live_path)
    live_dedup = _filter_plan_file_for_live_dedup(plan_path, prior_live_keys)
    if live_dedup["plans_before"] != live_dedup["plans_after"]:
        planner["accepted_before_live_dedup"] = planner.get("accepted", 0)
        planner["plans_before_live_dedup"] = planner.get("plans", 0)
        planner["accepted"] = live_dedup["plans_after"]
        planner["plans"] = live_dedup["plans_after"]
    planner["live_dedup"] = live_dedup

    live_exposure_cap = _filter_plan_file_for_live_exposure_cap(
        plan_path,
        _prior_live_market_notional(live_path.parent, exclude_path=live_path),
        max_market_notional=float(live_config["max_market_notional"]),
        prior_market_sides=_prior_live_market_sides(live_path.parent, exclude_path=live_path),
    )
    if live_exposure_cap["plans_before"] != live_exposure_cap["plans_after"]:
        planner.setdefault("accepted_before_live_exposure_cap", planner.get("accepted", 0))
        planner.setdefault("plans_before_live_exposure_cap", planner.get("plans", 0))
        planner["accepted"] = live_exposure_cap["plans_after"]
        planner["plans"] = live_exposure_cap["plans_after"]
    planner["live_exposure_cap"] = live_exposure_cap

    accepted_after_dedup = int(planner.get("accepted", 0) or 0)
    no_submit_reason = "dry_run_live" if args.dry_run_live else "no_accepted_plans"
    if (
        not args.dry_run_live
        and accepted_after_dedup == 0
        and int(live_dedup.get("plans_before", 0) or 0) > int(live_dedup.get("plans_after", 0) or 0)
    ):
        no_submit_reason = "no_accepted_plans_after_live_dedup"

    executor: Dict[str, Any] = {
        "live_requested": False,
        "live_orders": 0,
        "live_errors": 0,
        "paper_written": 0,
        "skipped": no_submit_reason,
    }
    executor_run: Dict[str, Any] = {"returncode": 0, "output": ""}
    balance_preflight = _clob_balance_status() if accepted_after_dedup > 0 else {"ok_to_submit": False}
    if not args.dry_run_live and (not paused) and accepted_after_dedup > 0 and bool(balance_preflight.get("ok_to_submit")):
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
    elif paused:
        executor["skipped"] = "paused_by_telegram"
        executor["balance_preflight"] = balance_preflight
    elif accepted_after_dedup > 0 and not bool(balance_preflight.get("ok_to_submit")):
        executor["skipped"] = "balance_allowance_preflight"
        executor["balance_preflight"] = balance_preflight

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
        "telegram_commands": command_state,
        "paths": {
            "plan": str(plan_path),
            "signal": str(signal_path),
            "paper": str(paper_path),
            "live": str(live_path),
            "summary": str(summary_path),
        },
        "live_errors": errors,
        "contract_alerts": contract_alerts,
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    if not args.no_telegram:
        _send_summary(
            run_id=run_id,
            config=live_config,
            sync=sync,
            signals=signals,
            planner=planner,
            executor=executor,
            live_path=live_path,
            errors=errors,
            contract_alerts=contract_alerts,
        )

    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if sync["returncode"] == 0 and signal_run["returncode"] == 0 and planner_run["returncode"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
