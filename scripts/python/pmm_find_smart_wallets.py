from __future__ import annotations

import argparse
import json
import math
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from dotenv import load_dotenv


DATA_API_BASE = "https://data-api.polymarket.com"
GAMMA_BASE = "https://gamma-api.polymarket.com"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _normalize_json_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        txt = value.strip()
        if not txt:
            return []
        try:
            parsed = json.loads(txt)
            if isinstance(parsed, list):
                return parsed
            return [parsed]
        except Exception:
            return [value]
    return []


def _safe_slug_from_url(url: str) -> str:
    parsed = urlparse(url.strip())
    parts = [x for x in parsed.path.split("/") if x]
    if not parts:
        raise ValueError(f"cannot parse slug from url: {url}")
    return parts[-1]


def _fetch_json(url: str, params: Optional[Dict[str, Any]] = None, timeout_sec: float = 8.0) -> Any:
    if params:
        query = urlencode({k: v for k, v in params.items() if v is not None and str(v) != ""})
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}{query}"
    req = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "pm-agent-smart-wallet-finder-v2",
        },
    )
    with urlopen(req, timeout=timeout_sec) as resp:
        raw = resp.read().decode("utf-8")
    return json.loads(raw)


def _fetch_json_safe(
    url: str,
    params: Optional[Dict[str, Any]] = None,
    default: Any = None,
    timeout_sec: float = 8.0,
) -> Any:
    try:
        return _fetch_json(url=url, params=params, timeout_sec=timeout_sec)
    except Exception:
        return default


@dataclass
class TargetMarket:
    slug: str
    condition_id: str
    token_ids: List[str]
    outcomes: List[str]
    question: str
    volume: float = 0.0
    liquidity: float = 0.0


def _market_from_gamma_row(row: Dict[str, Any]) -> Optional[TargetMarket]:
    if not isinstance(row, dict):
        return None
    condition_id = str(row.get("conditionId") or "").strip()
    if not condition_id:
        return None
    token_ids = [
        str(x).strip()
        for x in _normalize_json_list(row.get("clobTokenIds") or row.get("clob_token_ids"))
        if str(x).strip()
    ]
    outcomes = [str(x).strip() for x in _normalize_json_list(row.get("outcomes")) if str(x).strip()]
    volume = _to_float(row.get("volumeNum"), _to_float(row.get("volume"), 0.0))
    liquidity = _to_float(row.get("liquidityNum"), _to_float(row.get("liquidity"), 0.0))
    return TargetMarket(
        slug=str(row.get("slug") or "").strip(),
        condition_id=condition_id,
        token_ids=token_ids,
        outcomes=outcomes,
        question=str(row.get("question") or ""),
        volume=volume,
        liquidity=liquidity,
    )


def _resolve_market(slug: str) -> TargetMarket:
    payload = _fetch_json_safe(f"{GAMMA_BASE}/markets", params={"slug": slug}, default=[])
    if not isinstance(payload, list) or not payload:
        raise RuntimeError(f"cannot resolve market from slug={slug}")
    market = _market_from_gamma_row(payload[0])
    if market is None:
        raise RuntimeError(f"resolved market invalid for slug={slug}")
    return market


def _fetch_top_markets(
    market_count: int,
    fetch_limit: int,
    only_active: bool,
    sort_key: str,
    min_volume: float,
    min_liquidity: float,
) -> List[TargetMarket]:
    payload = _fetch_json_safe(
        f"{GAMMA_BASE}/markets",
        params={
            "limit": max(10, int(fetch_limit)),
            "active": "true" if only_active else "",
            "closed": "false" if only_active else "",
        },
        default=[],
    )
    if not isinstance(payload, list):
        return []
    markets: List[TargetMarket] = []
    for row in payload:
        m = _market_from_gamma_row(row if isinstance(row, dict) else {})
        if m is None:
            continue
        if m.volume < min_volume and m.liquidity < min_liquidity:
            continue
        markets.append(m)
    key_name = "volume" if sort_key == "volume" else "liquidity"
    markets.sort(key=lambda x: getattr(x, key_name, 0.0), reverse=True)
    return markets[: max(1, int(market_count))]


def _upsert_profile_meta(meta: Dict[str, Dict[str, str]], wallet: str, row: Dict[str, Any]) -> None:
    if not wallet:
        return
    item = meta.setdefault(wallet, {"name": "", "pseudonym": ""})
    name = str(row.get("name") or "").strip()
    pseudonym = str(row.get("pseudonym") or "").strip()
    if name and not item.get("name"):
        item["name"] = name
    if pseudonym and not item.get("pseudonym"):
        item["pseudonym"] = pseudonym


def _add_candidate(
    candidates: Dict[str, Dict[str, Any]],
    wallet: str,
    score: float,
    source: str,
    market_slug: str,
    name: str = "",
    pseudonym: str = "",
) -> None:
    if not wallet:
        return
    item = candidates.setdefault(
        wallet,
        {
            "wallet": wallet,
            "discovery_score": 0.0,
            "sources": set(),
            "market_slugs": set(),
            "holder_hits": 0,
            "trade_hits": 0,
            "name": "",
            "pseudonym": "",
        },
    )
    item["discovery_score"] += max(0.0, score)
    item["sources"].add(source)
    if market_slug:
        item["market_slugs"].add(market_slug)
    if source == "holders":
        item["holder_hits"] += 1
    if source == "trades":
        item["trade_hits"] += 1
    if name and not item["name"]:
        item["name"] = name
    if pseudonym and not item["pseudonym"]:
        item["pseudonym"] = pseudonym


def _collect_from_market(
    market: TargetMarket,
    candidates: Dict[str, Dict[str, Any]],
    meta: Dict[str, Dict[str, str]],
    max_holders_per_token: int,
    max_market_trades: int,
    page_size: int,
) -> Dict[str, int]:
    stats = {"holders_wallets": 0, "trades_wallets": 0}
    holders_payload = _fetch_json_safe(
        f"{DATA_API_BASE}/holders",
        params={"market": market.condition_id, "limit": max(1, int(max_holders_per_token))},
        default=[],
    )
    seen_holders = set()
    if isinstance(holders_payload, list):
        for token_group in holders_payload:
            if not isinstance(token_group, dict):
                continue
            holders = token_group.get("holders")
            if not isinstance(holders, list):
                continue
            for idx, h in enumerate(holders):
                if not isinstance(h, dict):
                    continue
                wallet = str(h.get("proxyWallet") or "").strip().lower()
                if not wallet:
                    continue
                amount = max(0.0, _to_float(h.get("amount"), 0.0))
                rank_bonus = 1.0 / max(1.0, float(idx + 1))
                holder_score = rank_bonus + min(4.0, math.log1p(amount + 1.0) / 3.0)
                _add_candidate(
                    candidates=candidates,
                    wallet=wallet,
                    score=holder_score,
                    source="holders",
                    market_slug=market.slug,
                    name=str(h.get("name") or "").strip(),
                    pseudonym=str(h.get("pseudonym") or "").strip(),
                )
                _upsert_profile_meta(meta, wallet, h)
                seen_holders.add(wallet)
    stats["holders_wallets"] = len(seen_holders)

    seen_trades = set()
    offset = 0
    total_rows = 0
    while total_rows < max(1, int(max_market_trades)):
        limit = min(max(1, int(page_size)), max(1, int(max_market_trades)) - total_rows)
        rows = _fetch_json_safe(
            f"{DATA_API_BASE}/trades",
            params={
                "market": market.condition_id,
                "limit": limit,
                "offset": offset,
            },
            default=[],
        )
        if not isinstance(rows, list) or not rows:
            break
        for row in rows:
            if not isinstance(row, dict):
                continue
            wallet = str(row.get("proxyWallet") or "").strip().lower()
            if not wallet:
                continue
            size = abs(_to_float(row.get("size"), 0.0))
            price = max(0.0, _to_float(row.get("price"), 0.0))
            trade_notional = size * price
            trade_score = 1.0 + min(4.0, math.log1p(trade_notional + 1.0))
            _add_candidate(
                candidates=candidates,
                wallet=wallet,
                score=trade_score,
                source="trades",
                market_slug=market.slug,
                name=str(row.get("name") or "").strip(),
                pseudonym=str(row.get("pseudonym") or "").strip(),
            )
            _upsert_profile_meta(meta, wallet, row)
            seen_trades.add(wallet)
        total_rows += len(rows)
        if len(rows) < limit:
            break
        offset += limit
    stats["trades_wallets"] = len(seen_trades)
    return stats


def _fetch_user_positions(user: str) -> List[Dict[str, Any]]:
    rows = _fetch_json_safe(f"{DATA_API_BASE}/positions", params={"user": user}, default=[])
    if not isinstance(rows, list):
        return []
    return [x for x in rows if isinstance(x, dict)]


def _fetch_user_trades(user: str, max_trades: int, page_size: int) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    offset = 0
    while len(out) < max(1, int(max_trades)):
        limit = min(max(1, int(page_size)), max(1, int(max_trades)) - len(out))
        rows = _fetch_json_safe(
            f"{DATA_API_BASE}/trades",
            params={"user": user, "limit": limit, "offset": offset},
            default=[],
        )
        if not isinstance(rows, list) or not rows:
            break
        out.extend([x for x in rows if isinstance(x, dict)])
        if len(rows) < limit:
            break
        offset += limit
    return out


def _convictions_from_positions(rows: List[Dict[str, Any]], target_tokens: List[str]) -> Dict[str, float]:
    targets = [str(x).strip() for x in target_tokens if str(x).strip()]
    if not targets:
        return {}
    exposure = {t: 0.0 for t in targets}
    for row in rows:
        asset = str(row.get("asset") or "").strip()
        opposite = str(row.get("oppositeAsset") or "").strip()
        current_value = abs(_to_float(row.get("currentValue"), 0.0))
        size = abs(_to_float(row.get("size"), 0.0))
        cur_price = max(0.0, _to_float(row.get("curPrice"), 0.0))
        notional = current_value if current_value > 0 else (size * cur_price)
        if notional <= 0:
            continue
        if asset in exposure:
            exposure[asset] += notional
        if opposite in exposure:
            exposure[opposite] -= notional
    denom = sum(abs(v) for v in exposure.values())
    if denom <= 0:
        return {k: 0.0 for k in targets}
    return {k: round(_clamp(v / denom, -1.0, 1.0), 6) for k, v in exposure.items()}


def _infer_winner_outcome(market: Dict[str, Any]) -> str:
    closed = bool(market.get("closed", False))
    if not closed:
        return ""
    outcomes = [str(x).strip() for x in _normalize_json_list(market.get("outcomes"))]
    prices = [_to_float(x, 0.0) for x in _normalize_json_list(market.get("outcomePrices"))]
    if not outcomes or len(prices) != len(outcomes):
        return ""
    pairs = sorted([(prices[i], outcomes[i]) for i in range(len(outcomes))], key=lambda x: x[0], reverse=True)
    if not pairs:
        return ""
    top_price, top_outcome = pairs[0]
    second_price = pairs[1][0] if len(pairs) > 1 else 0.0
    if top_price >= 0.97 and second_price <= 0.03:
        return top_outcome
    return ""


def _is_trade_win(side: str, trade_outcome: str, winner_outcome: str, include_sells: bool) -> Optional[bool]:
    s = str(side).upper().strip()
    o = str(trade_outcome).strip().lower()
    w = str(winner_outcome).strip().lower()
    if not o or not w:
        return None
    if s == "BUY":
        return o == w
    if s == "SELL":
        if not include_sells:
            return None
        return o != w
    return None


def _score_wallet_pnl_proxy(
    positions: List[Dict[str, Any]],
    min_notional: float,
    min_pnl_abs: float,
) -> Dict[str, Any]:
    wins = 0
    losses = 0
    resolved_markets = set()
    resolved_notional = 0.0
    for row in positions:
        condition = str(row.get("conditionId") or "").strip()
        if condition:
            resolved_markets.add(condition)
        notional = max(
            abs(_to_float(row.get("totalBought"), 0.0)),
            abs(_to_float(row.get("currentValue"), 0.0)),
            abs(_to_float(row.get("size"), 0.0)) * max(0.0, _to_float(row.get("curPrice"), 0.0)),
        )
        if notional < min_notional:
            continue
        pnl = _to_float(row.get("cashPnl"), 0.0)
        if abs(pnl) < min_pnl_abs:
            pnl = _to_float(row.get("realizedPnl"), 0.0)
        if pnl >= min_pnl_abs:
            wins += 1
            resolved_notional += notional
        elif pnl <= -min_pnl_abs:
            losses += 1
            resolved_notional += notional
    return {
        "wins": wins,
        "losses": losses,
        "resolved_trades": wins + losses,
        "resolved_markets": len(resolved_markets),
        "resolved_notional": resolved_notional,
    }


def _score_wallet_resolved_trades(
    trades: List[Dict[str, Any]],
    include_sells: bool,
    market_cache: Dict[str, Dict[str, Any]],
    winner_cache: Dict[str, str],
) -> Dict[str, Any]:
    wins = 0
    losses = 0
    resolved_markets = set()
    resolved_notional = 0.0
    for tr in trades:
        slug_i = str(tr.get("slug") or "").strip()
        if not slug_i:
            continue
        if slug_i not in winner_cache:
            market_payload = _fetch_json_safe(f"{GAMMA_BASE}/markets", params={"slug": slug_i, "limit": 1}, default=[])
            market_cache[slug_i] = market_payload[0] if isinstance(market_payload, list) and market_payload else {}
            winner_cache[slug_i] = _infer_winner_outcome(market_cache.get(slug_i, {}))
        winner = winner_cache.get(slug_i, "")
        if not winner:
            continue
        trade_is_win = _is_trade_win(
            side=str(tr.get("side") or ""),
            trade_outcome=str(tr.get("outcome") or ""),
            winner_outcome=winner,
            include_sells=include_sells,
        )
        if trade_is_win is None:
            continue
        if trade_is_win:
            wins += 1
        else:
            losses += 1
        condition = str(tr.get("conditionId") or "").strip() or slug_i
        resolved_markets.add(condition)
        size = abs(_to_float(tr.get("size"), 0.0))
        price = max(0.0, _to_float(tr.get("price"), 0.0))
        resolved_notional += size * price
    return {
        "wins": wins,
        "losses": losses,
        "resolved_trades": wins + losses,
        "resolved_markets": len(resolved_markets),
        "resolved_notional": resolved_notional,
    }


def _behavior_features(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not trades:
        return {
            "trade_count": 0,
            "distinct_markets": 0,
            "buy_ratio": 0.0,
            "avg_trade_notional": 0.0,
            "median_gap_sec": 0.0,
            "trades_per_hour": 0.0,
        }
    timestamps = []
    notionals = []
    buy_count = 0
    market_keys = set()
    for tr in trades:
        ts = int(_to_float(tr.get("timestamp"), 0.0))
        if ts > 0:
            timestamps.append(ts)
        size = abs(_to_float(tr.get("size"), 0.0))
        price = max(0.0, _to_float(tr.get("price"), 0.0))
        notionals.append(size * price)
        side = str(tr.get("side") or "").upper()
        if side == "BUY":
            buy_count += 1
        key = str(tr.get("conditionId") or tr.get("slug") or "").strip()
        if key:
            market_keys.add(key)
    trade_count = len(trades)
    avg_notional = sum(notionals) / max(1, len(notionals))
    buy_ratio = buy_count / max(1, trade_count)
    median_gap = 0.0
    tph = 0.0
    if len(timestamps) >= 2:
        ts_sorted = sorted(timestamps)
        gaps = [ts_sorted[i] - ts_sorted[i - 1] for i in range(1, len(ts_sorted)) if ts_sorted[i] > ts_sorted[i - 1]]
        if gaps:
            gaps_sorted = sorted(gaps)
            median_gap = gaps_sorted[len(gaps_sorted) // 2]
        hours = max(1.0 / 60.0, (ts_sorted[-1] - ts_sorted[0]) / 3600.0)
        tph = trade_count / hours
    return {
        "trade_count": int(trade_count),
        "distinct_markets": int(len(market_keys)),
        "buy_ratio": round(buy_ratio, 4),
        "avg_trade_notional": round(avg_notional, 4),
        "median_gap_sec": round(median_gap, 2),
        "trades_per_hour": round(tph, 4),
    }


def _style_label(features: Dict[str, Any]) -> str:
    trade_count = int(features.get("trade_count", 0))
    distinct = int(features.get("distinct_markets", 0))
    avg_notional = float(features.get("avg_trade_notional", 0.0))
    median_gap = float(features.get("median_gap_sec", 0.0))
    tph = float(features.get("trades_per_hour", 0.0))
    if trade_count >= 80 and distinct >= 25 and ((median_gap > 0 and median_gap <= 120) or tph >= 20):
        return "bot_like_market_maker"
    if distinct <= 6 and avg_notional >= 120:
        return "concentrated_conviction_trader"
    if distinct >= 30 and avg_notional <= 60:
        return "broad_flow_rotator"
    if trade_count < 20 and avg_notional >= 100:
        return "low_freq_whale"
    return "discretionary_mixed"


def _heuristic_explanation(style: str, features: Dict[str, Any], score_mode: str) -> str:
    return (
        f"Style={style}; score_mode={score_mode}; "
        f"trades={features.get('trade_count', 0)}, "
        f"distinct_markets={features.get('distinct_markets', 0)}, "
        f"avg_notional={features.get('avg_trade_notional', 0)}, "
        f"median_gap_sec={features.get('median_gap_sec', 0)}, "
        f"trades_per_hour={features.get('trades_per_hour', 0)}"
    )


def _llm_explain_wallet(
    wallet_row: Dict[str, Any],
    features: Dict[str, Any],
    score_mode: str,
    llm_base_url: str,
    llm_api_key: str,
    llm_model: str,
    llm_timeout_sec: float,
) -> str:
    prompt = {
        "wallet": wallet_row.get("wallet", ""),
        "name": wallet_row.get("name", ""),
        "pseudonym": wallet_row.get("pseudonym", ""),
        "win_rate": wallet_row.get("win_rate", 0),
        "resolved_trades": wallet_row.get("resolved_trades", 0),
        "resolved_markets": wallet_row.get("resolved_markets", 0),
        "resolved_notional": wallet_row.get("resolved_notional", 0),
        "style_label": wallet_row.get("style_label", ""),
        "behavior": features,
        "score_mode": score_mode,
    }
    messages = [
        {
            "role": "system",
            "content": (
                "You are a quant analyst. Provide concise trader-style interpretation. "
                "Output Chinese plain text, max 120 chars."
            ),
        },
        {
            "role": "user",
            "content": (
                "基于以下钱包特征，解释其交易风格（机器人/主观/集中押注/轮动等），"
                "并给1条跟单风险提示:\n" + json.dumps(prompt, ensure_ascii=False)
            ),
        },
    ]
    payload = {
        "model": llm_model,
        "temperature": 0,
        "messages": messages,
    }
    req = Request(
        llm_base_url.rstrip("/") + "/v1/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {llm_api_key}",
        },
        method="POST",
    )
    with urlopen(req, timeout=max(3.0, float(llm_timeout_sec))) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    choices = body.get("choices") if isinstance(body, dict) else None
    if not isinstance(choices, list) or not choices:
        return ""
    msg = choices[0].get("message") if isinstance(choices[0], dict) else {}
    return str(msg.get("content") or "").strip()


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pmm_find_smart_wallets",
        description="Discover high-win-rate Polymarket wallets and output analyzable smart-money snapshot.",
    )
    p.add_argument("--url", default="", help="Polymarket market URL")
    p.add_argument("--slug", default="", help="Polymarket market slug")
    p.add_argument("--condition-id", default="", help="Condition id")
    p.add_argument("--token-ids", default="", help="Optional token IDs for conviction mapping")

    p.add_argument(
        "--discovery-mode",
        choices=["multi_market", "single_market", "global_recent"],
        default="multi_market",
        help="Candidate discovery scope",
    )
    p.add_argument("--market-count", type=int, default=10, help="Top markets to scan in multi_market mode")
    p.add_argument("--market-fetch-limit", type=int, default=150, help="How many markets to fetch before sorting")
    p.add_argument("--market-sort-key", choices=["volume", "liquidity"], default="volume", help="Market ranking key")
    p.add_argument("--market-min-volume", type=float, default=50000.0, help="Min market volume for discovery")
    p.add_argument("--market-min-liquidity", type=float, default=10000.0, help="Min market liquidity for discovery")
    p.add_argument("--only-active-markets", action="store_true", help="Scan only active/open markets")

    p.add_argument("--max-candidate-wallets", type=int, default=80, help="Max candidate wallets to evaluate")
    p.add_argument("--max-holders-per-token", type=int, default=80, help="Per market holder depth")
    p.add_argument("--max-market-trades", type=int, default=300, help="Per market trades sampled for discovery")
    p.add_argument("--max-user-trades", type=int, default=300, help="Per wallet trades sampled")
    p.add_argument("--behavior-max-trades", type=int, default=120, help="Per wallet trades used for style profiling")
    p.add_argument("--min-behavior-trades", type=int, default=1, help="Min behavior trades required to keep wallet")
    p.add_argument("--page-size", type=int, default=100, help="Pagination size")

    p.add_argument("--score-mode", choices=["pnl_proxy", "resolved_trades"], default="pnl_proxy", help="Wallet score mode")
    p.add_argument("--min-position-notional", type=float, default=10.0, help="Min notional in pnl_proxy scoring")
    p.add_argument("--min-pnl-abs", type=float, default=0.2, help="Min abs pnl in pnl_proxy scoring")
    p.add_argument("--min-win-rate", type=float, default=0.60, help="Min wallet win rate")
    p.add_argument("--min-resolved-trades", type=int, default=6, help="Min sample trades")
    p.add_argument("--min-resolved-markets", type=int, default=4, help="Min sample markets")
    p.add_argument("--include-sells", action="store_true", help="Include SELL in resolved_trades scoring")
    p.add_argument("--top-wallets", type=int, default=30, help="Final output wallet count")

    p.add_argument("--enable-llm-explain", action="store_true", help="Use LLM to explain wallet style")
    p.add_argument("--llm-top-wallets", type=int, default=8, help="LLM explanation wallet count")
    p.add_argument("--llm-base-url", default=os.getenv("LLM_BASE_URL", ""), help="LLM base url")
    p.add_argument("--llm-api-key", default=os.getenv("LLM_API_KEY", ""), help="LLM API key")
    p.add_argument("--llm-model", default=os.getenv("LLM_MODEL", ""), help="LLM model")
    p.add_argument("--llm-timeout-sec", type=float, default=10.0, help="LLM request timeout")

    p.add_argument("--out-file", default="runtime/smart_money_wallets.json", help="Output snapshot path")
    p.add_argument("--sleep-ms", type=int, default=0, help="Optional sleep between wallet evaluations")
    return p


def main() -> None:
    load_dotenv()
    args = _build_parser().parse_args()

    slug = str(args.slug).strip()
    if not slug and args.url.strip():
        slug = _safe_slug_from_url(args.url.strip())
    condition_id = str(args.condition_id).strip()

    target_market: Optional[TargetMarket] = None
    if slug:
        target_market = _resolve_market(slug)
        condition_id = target_market.condition_id

    target_tokens = [x.strip() for x in str(args.token_ids).split(",") if x.strip()]
    if not target_tokens and target_market is not None:
        target_tokens = list(target_market.token_ids)

    candidates: Dict[str, Dict[str, Any]] = {}
    candidate_meta: Dict[str, Dict[str, str]] = {}
    discovery_stats: Dict[str, Any] = {
        "from_holders": 0,
        "from_market_trades": 0,
        "markets_scanned": 0,
    }
    scanned_markets: List[Dict[str, Any]] = []

    discovery_mode = str(args.discovery_mode).strip()
    if target_market is not None:
        discovery_mode = "single_market"
    elif condition_id and discovery_mode != "global_recent":
        discovery_mode = "single_market"

    if discovery_mode == "single_market":
        market = target_market or TargetMarket(
            slug="",
            condition_id=condition_id,
            token_ids=target_tokens,
            outcomes=[],
            question="",
        )
        stats = _collect_from_market(
            market=market,
            candidates=candidates,
            meta=candidate_meta,
            max_holders_per_token=max(1, int(args.max_holders_per_token)),
            max_market_trades=max(1, int(args.max_market_trades)),
            page_size=max(1, int(args.page_size)),
        )
        discovery_stats["from_holders"] += int(stats["holders_wallets"])
        discovery_stats["from_market_trades"] += int(stats["trades_wallets"])
        discovery_stats["markets_scanned"] = 1
        scanned_markets.append(
            {
                "slug": market.slug,
                "condition_id": market.condition_id,
                "volume": market.volume,
                "liquidity": market.liquidity,
            }
        )
    elif discovery_mode == "multi_market":
        markets = _fetch_top_markets(
            market_count=max(1, int(args.market_count)),
            fetch_limit=max(10, int(args.market_fetch_limit)),
            only_active=bool(args.only_active_markets),
            sort_key=str(args.market_sort_key),
            min_volume=max(0.0, float(args.market_min_volume)),
            min_liquidity=max(0.0, float(args.market_min_liquidity)),
        )
        for m in markets:
            stats = _collect_from_market(
                market=m,
                candidates=candidates,
                meta=candidate_meta,
                max_holders_per_token=max(1, int(args.max_holders_per_token)),
                max_market_trades=max(1, int(args.max_market_trades)),
                page_size=max(1, int(args.page_size)),
            )
            discovery_stats["from_holders"] += int(stats["holders_wallets"])
            discovery_stats["from_market_trades"] += int(stats["trades_wallets"])
            scanned_markets.append(
                {
                    "slug": m.slug,
                    "condition_id": m.condition_id,
                    "volume": m.volume,
                    "liquidity": m.liquidity,
                }
            )
        discovery_stats["markets_scanned"] = len(markets)
    else:
        rows = _fetch_json_safe(
            f"{DATA_API_BASE}/trades",
            params={"limit": max(10, int(args.max_market_trades))},
            default=[],
        )
        seen = set()
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                wallet = str(row.get("proxyWallet") or "").strip().lower()
                if not wallet:
                    continue
                size = abs(_to_float(row.get("size"), 0.0))
                price = max(0.0, _to_float(row.get("price"), 0.0))
                notional = size * price
                _add_candidate(
                    candidates=candidates,
                    wallet=wallet,
                    score=1.0 + min(3.0, math.log1p(notional + 1.0)),
                    source="trades",
                    market_slug=str(row.get("slug") or ""),
                    name=str(row.get("name") or "").strip(),
                    pseudonym=str(row.get("pseudonym") or "").strip(),
                )
                _upsert_profile_meta(candidate_meta, wallet, row)
                seen.add(wallet)
        discovery_stats["from_market_trades"] = len(seen)
        discovery_stats["markets_scanned"] = 0

    ranked_candidates = sorted(
        candidates.values(),
        key=lambda x: (
            float(x.get("discovery_score", 0.0)),
            int(x.get("trade_hits", 0)),
            int(x.get("holder_hits", 0)),
        ),
        reverse=True,
    )
    ranked_candidates = ranked_candidates[: max(1, int(args.max_candidate_wallets))]

    market_cache: Dict[str, Dict[str, Any]] = {}
    winner_cache: Dict[str, str] = {}
    wallets_out: List[Dict[str, Any]] = []
    skipped_no_sample = 0

    for cand in ranked_candidates:
        wallet = str(cand.get("wallet") or "").strip().lower()
        if not wallet:
            continue

        positions = _fetch_user_positions(wallet)
        trades_for_scoring: List[Dict[str, Any]] = []
        if args.score_mode == "resolved_trades":
            trades_for_scoring = _fetch_user_trades(
                user=wallet,
                max_trades=max(1, int(args.max_user_trades)),
                page_size=max(1, int(args.page_size)),
            )

        if args.score_mode == "pnl_proxy":
            scored = _score_wallet_pnl_proxy(
                positions=positions,
                min_notional=max(0.0, float(args.min_position_notional)),
                min_pnl_abs=max(0.0, float(args.min_pnl_abs)),
            )
        else:
            scored = _score_wallet_resolved_trades(
                trades=trades_for_scoring,
                include_sells=bool(args.include_sells),
                market_cache=market_cache,
                winner_cache=winner_cache,
            )

        resolved_trades = int(scored.get("resolved_trades", 0))
        resolved_markets = int(scored.get("resolved_markets", 0))
        wins = int(scored.get("wins", 0))
        losses = int(scored.get("losses", 0))
        if resolved_trades < max(1, int(args.min_resolved_trades)) or resolved_markets < max(1, int(args.min_resolved_markets)):
            skipped_no_sample += 1
            continue
        win_rate = wins / max(1, resolved_trades)
        if win_rate < float(args.min_win_rate):
            continue

        behavior_trades = _fetch_user_trades(
            user=wallet,
            max_trades=max(1, int(args.behavior_max_trades)),
            page_size=max(1, int(args.page_size)),
        )
        features = _behavior_features(behavior_trades)
        if int(features.get("trade_count", 0)) < max(0, int(args.min_behavior_trades)):
            skipped_no_sample += 1
            continue
        style = _style_label(features)
        confidence = _clamp((resolved_trades / 200.0) * ((win_rate - 0.5) / 0.5), 0.0, 1.0)
        score = (win_rate - 0.5) * (resolved_trades ** 0.5) * (max(1, resolved_markets) ** 0.3)
        token_convictions = _convictions_from_positions(positions, target_tokens)

        wallet_row = {
            "wallet": wallet,
            "name": str(cand.get("name") or candidate_meta.get(wallet, {}).get("name", "")).strip(),
            "pseudonym": str(cand.get("pseudonym") or candidate_meta.get(wallet, {}).get("pseudonym", "")).strip(),
            "win_rate": round(win_rate, 6),
            "wins": wins,
            "losses": losses,
            "resolved_trades": resolved_trades,
            "resolved_markets": resolved_markets,
            "resolved_notional": round(_to_float(scored.get("resolved_notional"), 0.0), 4),
            "confidence": round(confidence, 6),
            "weight": round(max(0.0, score), 6),
            "score": round(score, 6),
            "token_convictions": token_convictions,
            "score_mode": str(args.score_mode),
            "discovery_score": round(_to_float(cand.get("discovery_score"), 0.0), 6),
            "discovery_sources": sorted(list(cand.get("sources", set()))),
            "discovery_markets": sorted(list(cand.get("market_slugs", set()))),
            "style_label": style,
            "style_features": features,
            "style_explanation": _heuristic_explanation(style, features, str(args.score_mode)),
            "llm_explanation": "",
        }
        wallets_out.append(wallet_row)
        if args.sleep_ms > 0:
            time.sleep(max(0.0, float(args.sleep_ms) / 1000.0))

    wallets_out.sort(
        key=lambda x: (
            float(x.get("score", 0.0)),
            float(x.get("win_rate", 0.0)),
            float(x.get("discovery_score", 0.0)),
            int(x.get("resolved_trades", 0)),
        ),
        reverse=True,
    )
    wallets_out = wallets_out[: max(1, int(args.top_wallets))]

    llm_enabled = bool(args.enable_llm_explain)
    llm_base_url = str(args.llm_base_url or "").strip()
    llm_api_key = str(args.llm_api_key or "").strip()
    llm_model = str(args.llm_model or "").strip()
    llm_ready = bool(llm_enabled and llm_base_url and llm_api_key and llm_model)
    llm_errors = 0
    if llm_enabled:
        for idx, wallet_row in enumerate(wallets_out[: max(0, int(args.llm_top_wallets))]):
            if not llm_ready:
                wallet_row["llm_explanation"] = "LLM未配置，使用启发式风格解释"
                continue
            try:
                wallet_row["llm_explanation"] = _llm_explain_wallet(
                    wallet_row=wallet_row,
                    features=wallet_row.get("style_features", {}),
                    score_mode=str(args.score_mode),
                    llm_base_url=llm_base_url,
                    llm_api_key=llm_api_key,
                    llm_model=llm_model,
                    llm_timeout_sec=float(args.llm_timeout_sec),
                )
            except Exception:
                llm_errors += 1
                wallet_row["llm_explanation"] = ""

    result = {
        "generated_at": _utc_now_iso(),
        "source": {"data_api": DATA_API_BASE, "gamma_api": GAMMA_BASE},
        "method": {
            "discovery_mode": discovery_mode,
            "score_mode": str(args.score_mode),
            "steps": [
                "discover_candidates_from_markets_or_recent_trades",
                "evaluate_wallet_winrate",
                "profile_wallet_style",
                "optional_llm_explanation",
            ],
        },
        "target_market": {
            "slug": target_market.slug if target_market else slug,
            "condition_id": target_market.condition_id if target_market else condition_id,
            "token_ids": target_tokens,
            "outcomes": target_market.outcomes if target_market else [],
            "question": target_market.question if target_market else "",
        },
        "filters": {
            "min_win_rate": float(args.min_win_rate),
            "min_resolved_trades": int(args.min_resolved_trades),
            "min_resolved_markets": int(args.min_resolved_markets),
            "include_sells": bool(args.include_sells),
            "max_user_trades": int(args.max_user_trades),
            "behavior_max_trades": int(args.behavior_max_trades),
            "min_behavior_trades": int(args.min_behavior_trades),
        },
        "stats": {
            "candidate_wallets": len(ranked_candidates),
            "selected_wallets": len(wallets_out),
            "skipped_no_sample": skipped_no_sample,
            "market_cache_size": len(market_cache),
            "winner_cache_size": len(winner_cache),
            "llm_enabled": llm_enabled,
            "llm_ready": llm_ready,
            "llm_errors": llm_errors,
            **discovery_stats,
        },
        "markets_scanned": scanned_markets,
        "wallets": wallets_out,
    }

    out_path = Path(args.out_file)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "out_file": str(out_path),
                "discovery_mode": discovery_mode,
                "score_mode": str(args.score_mode),
                "candidate_wallets": len(ranked_candidates),
                "selected_wallets": len(wallets_out),
                "markets_scanned": int(discovery_stats.get("markets_scanned", 0)),
                "llm_ready": llm_ready,
                "llm_errors": llm_errors,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
