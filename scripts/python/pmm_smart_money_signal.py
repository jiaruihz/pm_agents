from __future__ import annotations

import argparse
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _parse_wallets(path: Path) -> List[Dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        wallets = payload.get("wallets")
        if isinstance(wallets, list):
            return [x for x in wallets if isinstance(x, dict)]
    return []


def _wallet_token_contributions(
    wallets: List[Dict[str, Any]],
    min_win_rate: float,
    min_resolved_markets: float,
    max_wallets: int,
) -> Dict[str, List[Tuple[float, Dict[str, Any]]]]:
    rows: Dict[str, List[Tuple[float, Dict[str, Any]]]] = {}
    for raw in wallets[:max_wallets]:
        win_rate = _to_float(raw.get("win_rate"), 0.0)
        resolved = _to_float(raw.get("resolved_markets"), _to_float(raw.get("resolved_trades"), 0.0))
        if win_rate < min_win_rate or resolved < min_resolved_markets:
            continue
        convictions = raw.get("token_convictions")
        if not isinstance(convictions, dict):
            continue

        confidence = max(0.0, _to_float(raw.get("confidence"), 1.0))
        base_weight = max(0.0, _to_float(raw.get("weight"), 1.0))
        win_bonus = max(0.0, (win_rate - min_win_rate) / max(1e-6, 1.0 - min_win_rate))
        trade_bonus = max(0.1, min(2.0, math.log1p(resolved) / 3.0))
        wallet_weight = base_weight * confidence * (0.5 + win_bonus) * trade_bonus
        if wallet_weight <= 0:
            continue

        for token_id, raw_conv in convictions.items():
            token = str(token_id).strip()
            if not token:
                continue
            conviction = _clamp(_to_float(raw_conv, 0.0), -1.0, 1.0)
            if conviction == 0.0:
                continue
            score = wallet_weight * conviction
            rows.setdefault(token, []).append((score, raw))
    return rows


def build_token_signals(
    wallets: List[Dict[str, Any]],
    min_win_rate: float = 0.70,
    min_resolved_markets: float = 40,
    max_wallets: int = 200,
    top_k_wallets_per_token: int = 8,
) -> Dict[str, Any]:
    contributions = _wallet_token_contributions(
        wallets=wallets,
        min_win_rate=min_win_rate,
        min_resolved_markets=min_resolved_markets,
        max_wallets=max_wallets,
    )
    token_signals: Dict[str, float] = {}
    token_details: Dict[str, Any] = {}
    used_wallets: set[str] = set()

    for token_id, items in contributions.items():
        ranked = sorted(items, key=lambda x: abs(x[0]), reverse=True)
        selected = ranked[: max(1, int(top_k_wallets_per_token))]
        denom = sum(abs(score) for score, _ in selected)
        if denom <= 0:
            continue
        signal = _clamp(sum(score for score, _ in selected) / denom, -1.0, 1.0)
        token_signals[token_id] = round(signal, 6)

        picked = []
        for score, wallet in selected:
            wallet_addr = str(wallet.get("wallet") or wallet.get("address") or "").strip()
            if wallet_addr:
                used_wallets.add(wallet_addr.lower())
            picked.append(
                {
                    "wallet": wallet_addr,
                    "score": round(score, 6),
                    "win_rate": round(_to_float(wallet.get("win_rate"), 0.0), 6),
                    "resolved_markets": int(_to_float(wallet.get("resolved_markets"), _to_float(wallet.get("resolved_trades"), 0.0))),
                }
            )
        token_details[token_id] = {
            "contributors": picked,
            "contributors_count": len(selected),
        }

    return {
        "generated_at": _utc_now_iso(),
        "token_signals": token_signals,
        "tokens_count": len(token_signals),
        "wallets_input_count": len(wallets),
        "wallets_used_count": len(used_wallets),
        "details": token_details,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pmm_smart_money_signal",
        description="Build smart-money token signals from wallet stats snapshot.",
    )
    parser.add_argument(
        "--wallets-file",
        default="runtime/smart_money_wallets.json",
        help="Input wallets JSON path (list or {wallets:[...]})",
    )
    parser.add_argument(
        "--out-file",
        default="runtime/smart_money_signals.json",
        help="Output token signal JSON path",
    )
    parser.add_argument("--min-win-rate", type=float, default=0.70, help="Wallet min win rate")
    parser.add_argument("--min-resolved-markets", type=float, default=40, help="Wallet min resolved markets")
    parser.add_argument("--max-wallets", type=int, default=200, help="Max wallets from input to scan")
    parser.add_argument("--top-k-wallets", type=int, default=8, help="Top contributing wallets per token")
    parser.add_argument("--watch", action="store_true", help="Watch input file and rebuild periodically")
    parser.add_argument("--interval-sec", type=float, default=15.0, help="Watch mode polling interval")
    return parser


def _run_once(args: argparse.Namespace) -> Dict[str, Any]:
    wallets_file = Path(args.wallets_file)
    if not wallets_file.exists():
        raise FileNotFoundError(f"wallets file not found: {wallets_file}")
    wallets = _parse_wallets(wallets_file)
    result = build_token_signals(
        wallets=wallets,
        min_win_rate=args.min_win_rate,
        min_resolved_markets=args.min_resolved_markets,
        max_wallets=args.max_wallets,
        top_k_wallets_per_token=args.top_k_wallets,
    )
    out_path = Path(args.out_file)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"out_file": str(out_path), **result}, ensure_ascii=False, indent=2))
    return result


def main() -> None:
    args = _build_parser().parse_args()
    if not args.watch:
        _run_once(args)
        return

    wallets_file = Path(args.wallets_file)
    last_mtime = -1.0
    while True:
        try:
            mtime = float(wallets_file.stat().st_mtime) if wallets_file.exists() else -1.0
            if mtime > last_mtime:
                _run_once(args)
                last_mtime = mtime
        except Exception as exc:
            print(json.dumps({"error": str(exc), "at": _utc_now_iso()}, ensure_ascii=False))
        time.sleep(max(1.0, float(args.interval_sec)))


if __name__ == "__main__":
    main()
