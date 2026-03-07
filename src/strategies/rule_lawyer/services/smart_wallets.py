from __future__ import annotations

import math
from typing import Any, Dict, List

from src.platform.clients.polymarket_data import PolymarketDataClient
from src.platform.clients.polymarket_gamma import PolymarketGammaClient
from src.strategies.rule_lawyer.models_research import ResolvedMarket, WalletSignal
from src.strategies.rule_lawyer.services.common import normalize_json_list, to_float


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


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
    market: ResolvedMarket,
    data_client: PolymarketDataClient,
    max_holders_per_token: int,
    max_market_trades: int,
    page_size: int,
) -> tuple[Dict[str, Dict[str, Any]], Dict[str, int]]:
    candidates: Dict[str, Dict[str, Any]] = {}
    stats = {"holders_wallets": 0, "trades_wallets": 0}
    seen_holders = set()
    for token_group in data_client.get_market_holders(market.condition_id, limit=max_holders_per_token):
        holders = token_group.get("holders")
        if not isinstance(holders, list):
            continue
        for idx, holder in enumerate(holders):
            if not isinstance(holder, dict):
                continue
            wallet = str(holder.get("proxyWallet") or "").strip().lower()
            if not wallet:
                continue
            amount = max(0.0, to_float(holder.get("amount"), 0.0))
            rank_bonus = 1.0 / max(1.0, float(idx + 1))
            holder_score = rank_bonus + min(4.0, math.log1p(amount + 1.0) / 3.0)
            _add_candidate(
                candidates,
                wallet,
                holder_score,
                "holders",
                market.slug,
                name=str(holder.get("name") or "").strip(),
                pseudonym=str(holder.get("pseudonym") or "").strip(),
            )
            seen_holders.add(wallet)
    stats["holders_wallets"] = len(seen_holders)

    seen_trades = set()
    total = 0
    offset = 0
    while total < max(1, max_market_trades):
        limit = min(max(1, page_size), max_market_trades - total)
        rows = data_client.get_market_trades(market.condition_id, limit=limit, offset=offset)
        if not rows:
            break
        for row in rows:
            wallet = str(row.get("proxyWallet") or "").strip().lower()
            if not wallet:
                continue
            size = abs(to_float(row.get("size"), 0.0))
            price = max(0.0, to_float(row.get("price"), 0.0))
            trade_score = 1.0 + min(4.0, math.log1p(size * price + 1.0))
            _add_candidate(
                candidates,
                wallet,
                trade_score,
                "trades",
                market.slug,
                name=str(row.get("name") or "").strip(),
                pseudonym=str(row.get("pseudonym") or "").strip(),
            )
            seen_trades.add(wallet)
        total += len(rows)
        if len(rows) < limit:
            break
        offset += len(rows)
    stats["trades_wallets"] = len(seen_trades)
    return candidates, stats


def _convictions_from_positions(rows: List[Dict[str, Any]], target_tokens: List[str]) -> Dict[str, float]:
    tokens = [str(x).strip() for x in target_tokens if str(x).strip()]
    exposure = {t: 0.0 for t in tokens}
    for row in rows:
        asset = str(row.get("asset") or "").strip()
        opposite = str(row.get("oppositeAsset") or "").strip()
        current_value = abs(to_float(row.get("currentValue"), 0.0))
        size = abs(to_float(row.get("size"), 0.0))
        cur_price = max(0.0, to_float(row.get("curPrice"), 0.0))
        notional = current_value if current_value > 0 else size * cur_price
        if notional <= 0:
            continue
        if asset in exposure:
            exposure[asset] += notional
        if opposite in exposure:
            exposure[opposite] -= notional
    denom = sum(abs(v) for v in exposure.values())
    if denom <= 0:
        return {k: 0.0 for k in tokens}
    return {k: round(_clamp(v / denom, -1.0, 1.0), 6) for k, v in exposure.items()}


def _infer_winner_outcome(market: Dict[str, Any]) -> str:
    outcomes = [str(x).strip() for x in normalize_json_list(market.get("outcomes"))]
    prices = [to_float(x, 0.0) for x in normalize_json_list(market.get("outcomePrices"))]
    if not outcomes or len(outcomes) != len(prices) or not bool(market.get("closed", False)):
        return ""
    pairs = sorted([(prices[i], outcomes[i]) for i in range(len(outcomes))], key=lambda x: x[0], reverse=True)
    if not pairs:
        return ""
    top_price, top_outcome = pairs[0]
    second = pairs[1][0] if len(pairs) > 1 else 0.0
    if top_price >= 0.97 and second <= 0.03:
        return top_outcome
    return ""


def _score_wallet_pnl_proxy(positions: List[Dict[str, Any]], min_notional: float, min_pnl_abs: float) -> Dict[str, Any]:
    wins = 0
    losses = 0
    markets = set()
    notional_total = 0.0
    for row in positions:
        condition = str(row.get("conditionId") or "").strip()
        if condition:
            markets.add(condition)
        notional = max(
            abs(to_float(row.get("totalBought"), 0.0)),
            abs(to_float(row.get("currentValue"), 0.0)),
            abs(to_float(row.get("size"), 0.0)) * max(0.0, to_float(row.get("curPrice"), 0.0)),
        )
        if notional < min_notional:
            continue
        pnl = to_float(row.get("cashPnl"), 0.0)
        if abs(pnl) < min_pnl_abs:
            pnl = to_float(row.get("realizedPnl"), 0.0)
        if pnl >= min_pnl_abs:
            wins += 1
            notional_total += notional
        elif pnl <= -min_pnl_abs:
            losses += 1
            notional_total += notional
    return {
        "wins": wins,
        "losses": losses,
        "resolved_trades": wins + losses,
        "resolved_markets": len(markets),
        "resolved_notional": notional_total,
    }


def _score_wallet_resolved_trades(
    trades: List[Dict[str, Any]],
    include_sells: bool,
    winner_cache: Dict[str, str],
) -> Dict[str, Any]:
    gamma_client = PolymarketGammaClient()
    wins = 0
    losses = 0
    markets = set()
    notional_total = 0.0
    for tr in trades:
        slug = str(tr.get("slug") or "").strip()
        if not slug:
            continue
        if slug not in winner_cache:
            payload = gamma_client.fetch_market_by_id_or_slug(slug=slug) or {}
            winner_cache[slug] = _infer_winner_outcome(payload)
        winner = winner_cache.get(slug, "")
        if not winner:
            continue
        side = str(tr.get("side") or "").upper().strip()
        outcome = str(tr.get("outcome") or "").strip().lower()
        resolved = None
        if side == "BUY":
            resolved = outcome == winner.lower()
        elif side == "SELL" and include_sells:
            resolved = outcome != winner.lower()
        if resolved is None:
            continue
        if resolved:
            wins += 1
        else:
            losses += 1
        markets.add(str(tr.get("conditionId") or slug))
        notional_total += abs(to_float(tr.get("size"), 0.0)) * max(0.0, to_float(tr.get("price"), 0.0))
    return {
        "wins": wins,
        "losses": losses,
        "resolved_trades": wins + losses,
        "resolved_markets": len(markets),
        "resolved_notional": notional_total,
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
    timestamps: List[int] = []
    notionals: List[float] = []
    buy_count = 0
    market_keys = set()
    for tr in trades:
        ts = int(to_float(tr.get("timestamp"), 0.0))
        if ts > 0:
            timestamps.append(ts)
        notionals.append(abs(to_float(tr.get("size"), 0.0)) * max(0.0, to_float(tr.get("price"), 0.0)))
        if str(tr.get("side") or "").upper() == "BUY":
            buy_count += 1
        market_keys.add(str(tr.get("conditionId") or tr.get("slug") or "").strip())
    avg_notional = sum(notionals) / max(1, len(notionals))
    median_gap = 0.0
    tph = 0.0
    if len(timestamps) >= 2:
        ts_sorted = sorted(timestamps)
        gaps = [ts_sorted[i] - ts_sorted[i - 1] for i in range(1, len(ts_sorted)) if ts_sorted[i] > ts_sorted[i - 1]]
        if gaps:
            gaps_sorted = sorted(gaps)
            median_gap = gaps_sorted[len(gaps_sorted) // 2]
        hours = max(1.0 / 60.0, (ts_sorted[-1] - ts_sorted[0]) / 3600.0)
        tph = len(trades) / hours
    return {
        "trade_count": len(trades),
        "distinct_markets": len([x for x in market_keys if x]),
        "buy_ratio": round(buy_count / max(1, len(trades)), 4),
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


def discover_market_wallets(
    market: ResolvedMarket,
    holders_depth: int = 40,
    max_market_trades: int = 250,
    max_candidate_wallets: int = 50,
    top_wallets: int = 12,
    score_mode: str = "pnl_proxy",
    max_user_trades: int = 300,
    behavior_max_trades: int = 120,
    min_win_rate: float = 0.55,
    min_resolved_trades: int = 6,
    min_resolved_markets: int = 4,
    include_sells: bool = False,
    min_position_notional: float = 10.0,
    min_pnl_abs: float = 0.2,
    page_size: int = 100,
) -> Dict[str, Any]:
    data_client = PolymarketDataClient()
    candidates, stats = _collect_from_market(
        market=market,
        data_client=data_client,
        max_holders_per_token=holders_depth,
        max_market_trades=max_market_trades,
        page_size=page_size,
    )
    ranked_candidates = sorted(
        candidates.values(),
        key=lambda x: (
            float(x.get("discovery_score", 0.0)),
            int(x.get("trade_hits", 0)),
            int(x.get("holder_hits", 0)),
        ),
        reverse=True,
    )[: max(1, max_candidate_wallets)]

    winner_cache: Dict[str, str] = {}
    selected: List[WalletSignal] = []
    skipped = 0
    for cand in ranked_candidates:
        wallet = str(cand.get("wallet") or "").strip().lower()
        if not wallet:
            continue
        positions = [row for batch in data_client.iter_user_positions(wallet, page_size=200, max_rows=800) for row in batch]
        trades_for_scoring: List[Dict[str, Any]] = []
        if score_mode == "resolved_trades":
            trades_for_scoring = [row for batch in data_client.iter_user_trades(wallet, page_size=page_size, max_rows=max_user_trades) for row in batch]
            scored = _score_wallet_resolved_trades(trades_for_scoring, include_sells=include_sells, winner_cache=winner_cache)
        else:
            scored = _score_wallet_pnl_proxy(positions, min_notional=min_position_notional, min_pnl_abs=min_pnl_abs)
        resolved_trades = int(scored.get("resolved_trades", 0))
        resolved_markets = int(scored.get("resolved_markets", 0))
        wins = int(scored.get("wins", 0))
        losses = int(scored.get("losses", 0))
        if resolved_trades < min_resolved_trades or resolved_markets < min_resolved_markets:
            skipped += 1
            continue
        win_rate = wins / max(1, resolved_trades)
        if win_rate < min_win_rate:
            continue
        behavior_trades = [row for batch in data_client.iter_user_trades(wallet, page_size=page_size, max_rows=behavior_max_trades) for row in batch]
        features = _behavior_features(behavior_trades)
        style = _style_label(features)
        confidence = _clamp((resolved_trades / 200.0) * ((win_rate - 0.5) / 0.5), 0.0, 1.0)
        score = (win_rate - 0.5) * (resolved_trades ** 0.5) * (max(1, resolved_markets) ** 0.3)
        selected.append(
            WalletSignal(
                wallet=wallet,
                name=str(cand.get("name") or "").strip(),
                pseudonym=str(cand.get("pseudonym") or "").strip(),
                win_rate=round(win_rate, 6),
                wins=wins,
                losses=losses,
                resolved_trades=resolved_trades,
                resolved_markets=resolved_markets,
                resolved_notional=round(to_float(scored.get("resolved_notional"), 0.0), 4),
                confidence=round(confidence, 6),
                score=round(score, 6),
                discovery_score=round(to_float(cand.get("discovery_score"), 0.0), 6),
                discovery_sources=sorted(list(cand.get("sources", set()))),
                discovery_markets=sorted(list(cand.get("market_slugs", set()))),
                style_label=style,
                style_features=features,
                style_explanation=_heuristic_explanation(style, features, score_mode),
                token_convictions=_convictions_from_positions(positions, market.token_ids),
                raw={"candidate": cand},
            )
        )
    selected.sort(
        key=lambda x: (x.score, x.win_rate, x.discovery_score, x.resolved_trades),
        reverse=True,
    )
    selected = selected[: max(1, top_wallets)]
    return {
        "generated_from_market": market.to_dict(),
        "stats": {
            "candidate_wallets": len(ranked_candidates),
            "selected_wallets": len(selected),
            "skipped_no_sample": skipped,
            **stats,
        },
        "wallets": [x.to_dict() for x in selected],
    }
