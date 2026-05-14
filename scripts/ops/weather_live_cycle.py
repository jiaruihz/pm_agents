#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

import requests

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.platform.notification.telegram import send_telegram_message_sync


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


def _write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _live_dedup_key(row: Dict[str, Any]) -> Tuple[str, str, str, str]:
    return (
        str(row.get("strategy") or "weather_edge_v1").strip(),
        str(row.get("target_date") or "").strip(),
        str(row.get("token_id") or "").strip(),
        str(row.get("execution_policy") or "").strip(),
    )


def _prior_submitted_live_keys(live_dir: Path, *, exclude_path: Path) -> Set[Tuple[str, str, str, str]]:
    keys: Set[Tuple[str, str, str, str]] = set()
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
            if key[1] and key[2]:
                keys.add(key)
    return keys


def _filter_plan_file_for_live_dedup(plan_path: Path, prior_keys: Set[Tuple[str, str, str, str]]) -> Dict[str, Any]:
    rows = _read_jsonl(plan_path)
    kept: List[Dict[str, Any]] = []
    seen_this_run: Set[Tuple[str, str, str, str]] = set()
    skipped_prior = 0
    skipped_same_run = 0
    for row in rows:
        key = _live_dedup_key(row)
        if key[1] and key[2] and key in prior_keys:
            skipped_prior += 1
            continue
        if key[1] and key[2] and key in seen_this_run:
            skipped_same_run += 1
            continue
        if key[1] and key[2]:
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
        return "本轮只跑到计划生成，未尝试实盘下单。"
    if reason == "paused_by_telegram":
        return "实盘当前处于暂停状态，本轮只同步数据和生成计划，没有下单。"
    if reason == "balance_allowance_preflight":
        return "实盘下单前检查未通过：余额或授权不足，本轮没有提交订单。"
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
    sync: Dict[str, Any],
    signals: Dict[str, Any],
    planner: Dict[str, Any],
    executor: Dict[str, Any],
    live_path: Path,
    errors: List[str],
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

    if live_orders > 0 and live_errors == 0:
        headline = f"本轮已提交 {live_written} 笔 maker-only 实盘订单。"
    elif live_errors > 0:
        headline = f"本轮尝试下单但有 {live_errors} 笔失败；没有发送 taker 单。"
    else:
        headline = _first_sentence_for_skip(executor.get("skipped"))

    lines = [
        "【Weather 实盘循环】",
        headline,
        "",
        f"运行编号：{run_id}",
        f"数据同步：{'成功' if sync_ok else '失败'}",
        f"使用快照：{_short_path(signals.get('snapshot'))}",
        f"信号筛选：从 {int(signals.get('records', 0) or 0)} 条记录里选出 {int(signals.get('signals', 0) or 0)} 条候选。",
        f"交易计划：通过 {accepted} 条；跨轮去重跳过 {skipped_prior} 条，本轮重复跳过 {skipped_same_run} 条。",
        f"执行结果：提交 {live_written} 笔，失败 {live_errors} 笔，paper 记录 {paper_written} 笔，未启用实盘跳过 {skipped_disabled} 条。",
    ]
    if not signal_ok or not planner_ok:
        lines.append("提醒：信号或计划输出解析为空，需要检查本轮日志。")

    if errors:
        lines.extend(["", "失败摘要："])
        lines.extend(f"- {err}" for err in errors)

    balance = executor.get("balance_preflight") if isinstance(executor, dict) else None
    if isinstance(balance, dict):
        if balance.get("ok_to_submit"):
            lines.extend(["", f"资金检查：通过，可用余额约 {_to_usdc(balance.get('balance'))}。"])
        else:
            lines.extend(
                [
                    "",
                    "资金检查：未通过，实盘提交已跳过。",
                    f"余额：{_to_usdc(balance.get('balance'))}",
                    f"funder：{balance.get('funder', '-')}",
                ]
            )

    if int(executor.get("live_errors", 0) or 0) > 0:
        lines.extend(
            [
                "",
                "需要处理：优先检查钱包余额、allowance 和 CLOB API 鉴权。策略默认 maker-only，不会主动吃单。",
            ]
        )
    if executor.get("skipped") == "balance_allowance_preflight":
        lines.extend(
            [
                "",
                "需要处理：CLOB 余额或授权为 0，本轮已安全跳过实盘提交。",
            ]
        )
    lines.extend(["", f"计划文件：{_short_path(planner.get('out'))}", f"实盘记录：{_short_path(live_path)}"])
    send_telegram_message_sync("\n".join(lines))


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
    _telegram_post("sendMessage", {"chat_id": chat_id, "text": text})


def _handle_telegram_commands(state_dir: Path) -> Dict[str, Any]:
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not chat_id or not token:
        return {"commands": [], "paused": False}

    offset_path = state_dir / "telegram_update_offset.txt"
    paused_path = state_dir / "PAUSED"
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
        return {"commands": [], "paused": paused_path.exists(), "error": f"{type(exc).__name__}: {exc}"}

    updates = data.get("result") if isinstance(data, dict) else []
    if not isinstance(updates, list):
        updates = []
    if updates:
        max_update_id = max(int(x.get("update_id", 0)) for x in updates if isinstance(x, dict))
        offset_path.write_text(str(max_update_id + 1), encoding="utf-8")

    if offset <= 0:
        return {"commands": [], "paused": paused_path.exists(), "initialized": True}

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
            paused_path.write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")
            commands.append("pause")
            _send_text("已暂停 Weather 实盘。后续循环仍会同步数据和生成研究记录，但不会提交实盘订单。")
        elif text in {"/resume_weather", "resume", "继续", "恢复", "恢复实盘"}:
            if paused_path.exists():
                paused_path.unlink()
            commands.append("resume")
            _send_text("已恢复 Weather 实盘。下一轮如果有合格计划，会按 maker-only 规则尝试挂单。")
        elif text in {"/status_weather", "status", "状态"}:
            commands.append("status")
            if paused_path.exists():
                _send_text("Weather 实盘循环正在运行，但当前处于暂停状态。发送 resume / 继续 可以恢复实盘。")
            else:
                _send_text("Weather 实盘循环正在运行，当前允许 maker-only 挂单。发送 pause / 暂停 可以停止实盘尝试。")

    return {"commands": commands, "paused": paused_path.exists()}


def main() -> int:
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
    except ModuleNotFoundError:
        pass

    parser = argparse.ArgumentParser(description="Run one weather live cycle: sync -> signals -> plans -> maker-only executor.")
    parser.add_argument("--max-order-notional", type=float, default=float(os.getenv("WEATHER_LIVE_MAX_ORDER_NOTIONAL", "3.90")))
    parser.add_argument("--min-edge", type=float, default=float(os.getenv("WEATHER_LIVE_MIN_EDGE", "0.10")))
    parser.add_argument("--dry-run-live", action="store_true", help="Stop before live executor.")
    parser.add_argument("--no-telegram", action="store_true")
    args = parser.parse_args()

    run_id = _utc_run_id()
    signal_path = ROOT / "runtime" / "weather_edge_v1" / "signals" / f"live_{run_id}_signals.jsonl"
    plan_path = ROOT / "runtime" / "weather_edge_v1" / "plans" / f"live_{run_id}_trade_plans.jsonl"
    paper_path = ROOT / "runtime" / "weather_edge_v1" / "paper" / f"live_{run_id}_paper_orders.jsonl"
    live_path = ROOT / "runtime" / "weather_edge_v1" / "live" / f"live_{run_id}_orders.jsonl"
    summary_path = ROOT / "runtime" / "weather_edge_v1" / "live_cycle" / f"{run_id}.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    command_state = _handle_telegram_commands(summary_path.parent)
    paused = bool(command_state.get("paused"))

    sync = _run(["bash", "scripts/ops/sync_weather_remote.sh"], timeout=240)
    signal_cmd = [
        sys.executable,
        "scripts/ops/weather_snapshot_signal_builder.py",
        "--out",
        str(signal_path),
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
        "--max-order-notional",
        str(float(args.max_order_notional)),
        "--min-edge",
        str(float(args.min_edge)),
        "--enable-live",
        "--accepted-only",
    ]
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

    executor: Dict[str, Any] = {
        "live_requested": False,
        "live_orders": 0,
        "live_errors": 0,
        "paper_written": 0,
        "skipped": "dry_run_live",
    }
    executor_run: Dict[str, Any] = {"returncode": 0, "output": ""}
    balance_preflight = _clob_balance_status() if int(planner.get("accepted", 0) or 0) > 0 else {"ok_to_submit": False}
    if not args.dry_run_live and (not paused) and int(planner.get("accepted", 0) or 0) > 0 and bool(balance_preflight.get("ok_to_submit")):
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
    elif int(planner.get("accepted", 0) or 0) > 0 and not bool(balance_preflight.get("ok_to_submit")):
        executor["skipped"] = "balance_allowance_preflight"
        executor["balance_preflight"] = balance_preflight

    errors = _read_live_errors(live_path)
    summary = {
        "run_id": run_id,
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
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    if not args.no_telegram:
        _send_summary(
            run_id=run_id,
            sync=sync,
            signals=signals,
            planner=planner,
            executor=executor,
            live_path=live_path,
            errors=errors,
        )

    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if sync["returncode"] == 0 and signal_run["returncode"] == 0 and planner_run["returncode"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
