from __future__ import annotations

from typing import Any, Dict, List, Tuple

from src.platform.clients.polymarket_data import PolymarketDataClient
from src.platform.clients.polymarket_gamma import PolymarketGammaClient
from src.platform.clients.polymarket_profiles import PolymarketProfilesClient
from src.strategies.rule_lawyer.models_research import ProfileSummary, ProfileTarget
from src.strategies.rule_lawyer.services.common import WALLET_RE, normalize_json_list, to_float


def _extract_pnl_queries(queries: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, float]]]:
    out: Dict[str, List[Dict[str, float]]] = {}
    for row in queries:
        if not isinstance(row, dict):
            continue
        qk = row.get("queryKey")
        if not isinstance(qk, list) or not qk or str(qk[0]) != "portfolio-pnl":
            continue
        window = str(qk[-1]) if len(qk) >= 4 else "UNKNOWN"
        data = ((row.get("state") or {}).get("data")) or []
        if not isinstance(data, list):
            continue
        out[window] = [{"t": to_float(p.get("t"), 0.0), "p": to_float(p.get("p"), 0.0)} for p in data if isinstance(p, dict)]
    return out


def resolve_profile_target(target: str) -> ProfileTarget:
    text = (target or "").strip()
    if text.startswith("http://") or text.startswith("https://"):
        last = text.rstrip("/").split("/")[-1]
        if WALLET_RE.match(last):
            return ProfileTarget(raw_input=text, kind="wallet", wallet=last.lower(), profile_url=text)
        return ProfileTarget(raw_input=text, kind="url", username=last.lstrip("@"), profile_url=text)
    if WALLET_RE.match(text):
        return ProfileTarget(raw_input=text, kind="wallet", wallet=text.lower())
    return ProfileTarget(raw_input=text, kind="username", username=text.lstrip("@"), profile_url=f"https://polymarket.com/@{text.lstrip('@')}")


def _resolve_profile(target: ProfileTarget, profiles_client: PolymarketProfilesClient) -> Tuple[Dict[str, Any], Dict[str, List[Dict[str, float]]]]:
    username = target.username
    wallet = target.wallet
    user_data: Dict[str, Any] = {}
    if wallet and not username:
        user_data = profiles_client.fetch_profile_user_data(wallet)
        username = str(user_data.get("name") or "").lstrip("@")
    if not username:
        return (
            {
                "profile_url": target.profile_url,
                "username": "",
                "display_name": str(user_data.get("name") or ""),
                "pseudonym": str(user_data.get("pseudonym") or ""),
                "bio": str(user_data.get("bio") or ""),
                "proxy_wallet": wallet,
                "portfolio_volume": 0.0,
                "portfolio_pnl": 0.0,
                "markets_traded": 0,
                "largest_win": 0.0,
                "join_date": str(user_data.get("createdAt") or ""),
            },
            {},
        )
    page = profiles_client.fetch_profile_page(username)
    page_props = ((page.get("props") or {}).get("pageProps")) or {}
    queries = ((page_props.get("dehydratedState") or {}).get("queries")) or []
    user_data = profiles_client.find_query_data(queries, "/api/profile/userData") or user_data
    volume_data = profiles_client.find_query_data(queries, "/api/profile/volume") or {}
    stats_data = profiles_client.find_query_data(queries, "user-stats") or {}
    proxy_wallet = str(
        page_props.get("proxyAddress")
        or page_props.get("primaryAddress")
        or user_data.get("proxyWallet")
        or wallet
        or ""
    ).lower()
    profile = {
        "profile_url": f"https://polymarket.com/@{str(page_props.get('username') or user_data.get('name') or username).lstrip('@')}",
        "username": str(page_props.get("username") or user_data.get("name") or username).lstrip("@"),
        "display_name": str(user_data.get("name") or ""),
        "pseudonym": str(user_data.get("pseudonym") or ""),
        "bio": str(user_data.get("bio") or ""),
        "proxy_wallet": proxy_wallet,
        "portfolio_volume": to_float(volume_data.get("amount"), 0.0),
        "portfolio_pnl": to_float(volume_data.get("pnl"), 0.0),
        "markets_traded": int(to_float(stats_data.get("trades"), 0.0)),
        "largest_win": to_float(stats_data.get("largestWin"), 0.0),
        "join_date": str(stats_data.get("joinDate") or user_data.get("createdAt") or ""),
    }
    return profile, _extract_pnl_queries(queries)


def _score_positions(rows: List[Dict[str, Any]], pnl_key: str, floor_abs: float = 1.0) -> Dict[str, Any]:
    wins = 0
    losses = 0
    flats = 0
    for row in rows:
        pnl = to_float(row.get(pnl_key), 0.0)
        if pnl >= floor_abs:
            wins += 1
        elif pnl <= -floor_abs:
            losses += 1
        else:
            flats += 1
    considered = wins + losses
    rate = wins / considered if considered else 0.0
    return {
        "rows_fetched": len(rows),
        "wins": wins,
        "losses": losses,
        "flats": flats,
        "rows_with_verdict": considered,
        "win_rate": round(rate, 6),
    }


def _summarize_positions(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    pnl_rows = []
    total = 0.0
    for row in rows:
        pnl = to_float(row.get("cashPnl"), 0.0)
        total += pnl
        pnl_rows.append(
            {
                "slug": str(row.get("slug") or ""),
                "title": str(row.get("title") or ""),
                "cash_pnl": round(pnl, 4),
            }
        )
    pnl_rows.sort(key=lambda x: abs(to_float(x.get("cash_pnl"), 0.0)), reverse=True)
    return {
        "positions_count": len(rows),
        "positions_with_nonzero_cash_pnl": len([x for x in pnl_rows if to_float(x.get("cash_pnl"), 0.0) != 0.0]),
        "cash_pnl_sum": round(total, 4),
        "top_cash_pnl_positions": pnl_rows[:10],
    }


def _analyze_pnl_series(points: List[Dict[str, float]]) -> Dict[str, Any]:
    if len(points) < 2:
        base = to_float(points[0].get("p"), 0.0) if points else 0.0
        return {
            "points": len(points),
            "down_steps": 0,
            "up_steps": 0,
            "flat_steps": 0,
            "max_drawdown_abs": 0.0,
            "start_pnl": round(base, 4),
            "end_pnl": round(base, 4),
            "peak_pnl": round(base, 4),
            "trough_pnl": round(base, 4),
        }
    ordered = sorted(points, key=lambda x: to_float(x.get("t"), 0.0))
    prev = to_float(ordered[0].get("p"), 0.0)
    peak = prev
    trough = prev
    max_dd = 0.0
    down = 0
    up = 0
    flat = 0
    for row in ordered[1:]:
        cur = to_float(row.get("p"), 0.0)
        if cur < prev:
            down += 1
        elif cur > prev:
            up += 1
        else:
            flat += 1
        peak = max(peak, cur)
        trough = min(trough, cur)
        if peak > cur:
            max_dd = max(max_dd, peak - cur)
        prev = cur
    return {
        "points": len(ordered),
        "down_steps": down,
        "up_steps": up,
        "flat_steps": flat,
        "max_drawdown_abs": round(max_dd, 4),
        "start_pnl": round(to_float(ordered[0].get("p"), 0.0), 4),
        "end_pnl": round(to_float(ordered[-1].get("p"), 0.0), 4),
        "peak_pnl": round(peak, 4),
        "trough_pnl": round(trough, 4),
    }


def _infer_winner_outcome(market: Dict[str, Any]) -> str:
    outcomes = [str(x).strip() for x in normalize_json_list(market.get("outcomes")) if str(x).strip()]
    prices = [to_float(x, 0.0) for x in normalize_json_list(market.get("outcomePrices"))]
    if not outcomes or len(prices) != len(outcomes) or not bool(market.get("closed", False)):
        return ""
    pairs = sorted([(prices[idx], outcomes[idx]) for idx in range(len(outcomes))], key=lambda x: x[0], reverse=True)
    if not pairs:
        return ""
    top_price, top_outcome = pairs[0]
    second_price = pairs[1][0] if len(pairs) > 1 else 0.0
    if top_price >= 0.97 and second_price <= 0.03:
        return top_outcome
    return ""


def _is_trade_win(side: str, outcome: str, winner: str) -> bool | None:
    s = side.upper().strip()
    o = outcome.lower().strip()
    w = winner.lower().strip()
    if not o or not w:
        return None
    if s == "BUY":
        return o == w
    if s == "SELL":
        return o != w
    return None


def _score_trades(trades: List[Dict[str, Any]], max_resolve_markets: int) -> Dict[str, Any]:
    gamma_client = PolymarketGammaClient()
    cache: Dict[str, str] = {}
    resolved = 0
    wins = 0
    losses = 0
    unresolved = 0
    seen = set()
    for row in trades:
        slug = str(row.get("slug") or "").strip()
        if not slug:
            continue
        seen.add(slug)
        if slug not in cache and resolved < max_resolve_markets:
            payload = gamma_client.fetch_market_by_id_or_slug(slug=slug) or {}
            cache[slug] = _infer_winner_outcome(payload)
            resolved += 1
        winner = cache.get(slug, "")
        if not winner:
            unresolved += 1
            continue
        verdict = _is_trade_win(str(row.get("side") or ""), str(row.get("outcome") or ""), winner)
        if verdict is None:
            unresolved += 1
            continue
        if verdict:
            wins += 1
        else:
            losses += 1
    considered = wins + losses
    return {
        "trades_fetched": len(trades),
        "distinct_markets_in_trades": len(seen),
        "resolved_markets_checked": resolved,
        "trades_with_verdict": considered,
        "wins": wins,
        "losses": losses,
        "unresolved_or_skipped": unresolved,
        "inferred_win_rate": round(wins / considered, 6) if considered else 0.0,
        "all_correct_on_sample": bool(considered > 0 and losses == 0),
    }


def _build_verdict(summary: Dict[str, Any], pnl_analysis: Dict[str, Any]) -> str:
    considered = int(summary.get("trades_with_verdict", summary.get("rows_with_verdict", 0)))
    losses = int(summary.get("losses", 0))
    win_rate = to_float(summary.get("inferred_win_rate", summary.get("win_rate", 0.0)), 0.0)
    if considered == 0:
        if pnl_analysis.get("down_steps", 0) or pnl_analysis.get("max_drawdown_abs", 0):
            return "从公开 PnL 时间序列看并非一路全对，但缺少足够可判定样本，无法给出逐单精确胜率。"
        return "样本内没有足够可判定的已结算交易，无法判断是否全对。"
    if losses == 0:
        return "在可判定样本内暂时是全胜，但这不等于内幕或长期必胜，仍需观察回撤和样本扩展。"
    return f"在可判定样本内并非全对（推断胜率约 {win_rate:.2%}），不支持历史预测全对的说法。"


def audit_profile(
    target: str,
    fetch_all: bool = False,
    max_trades: int = 800,
    resolve_trade_outcomes: bool = False,
    max_resolve_markets: int = 80,
) -> ProfileSummary:
    profile_target = resolve_profile_target(target)
    profiles_client = PolymarketProfilesClient()
    data_client = PolymarketDataClient()
    profile, pnl_queries = _resolve_profile(profile_target, profiles_client)
    wallet = str(profile.get("proxy_wallet") or "")
    trade_cap = 200000 if fetch_all else max_trades
    position_cap = 5000 if fetch_all else 800
    closed_cap = 5000 if fetch_all else 2000
    trades = [row for batch in data_client.iter_user_trades(wallet, page_size=200, max_rows=trade_cap) for row in batch]
    positions = [row for batch in data_client.iter_user_positions(wallet, page_size=200, max_rows=position_cap) for row in batch]
    closed_positions = [
        row
        for batch in data_client.iter_user_closed_positions(wallet, page_size=50, max_rows=closed_cap)
        for row in batch
    ]
    if resolve_trade_outcomes:
        trade_summary = _score_trades(trades, max_resolve_markets=max_resolve_markets)
    else:
        trade_summary = {
            "trades_fetched": len(trades),
            "distinct_markets_in_trades": len({str(x.get('slug') or '') for x in trades if isinstance(x, dict)}),
            "note": "trade outcome resolution disabled",
        }
    position_score_raw = _score_positions(positions, "cashPnl")
    position_score = {
        "positions_fetched": position_score_raw["rows_fetched"],
        "winning_positions": position_score_raw["wins"],
        "losing_positions": position_score_raw["losses"],
        "flat_positions": position_score_raw["flats"],
        "positions_with_verdict": position_score_raw["rows_with_verdict"],
        "position_win_rate": position_score_raw["win_rate"],
        "all_correct_on_positions": bool(position_score_raw["rows_with_verdict"] > 0 and position_score_raw["losses"] == 0),
    }
    closed_score_raw = _score_positions(closed_positions, "realizedPnl")
    closed_position_score = {
        "closed_positions_fetched": closed_score_raw["rows_fetched"],
        "winning_closed_positions": closed_score_raw["wins"],
        "losing_closed_positions": closed_score_raw["losses"],
        "flat_closed_positions": closed_score_raw["flats"],
        "closed_positions_with_verdict": closed_score_raw["rows_with_verdict"],
        "closed_position_win_rate": closed_score_raw["win_rate"],
        "all_correct_on_closed_positions": bool(closed_score_raw["rows_with_verdict"] > 0 and closed_score_raw["losses"] == 0),
    }
    pnl_analysis = _analyze_pnl_series(pnl_queries.get("ALL", []))
    fallback = {
        "rows_with_verdict": closed_score_raw["rows_with_verdict"] or position_score_raw["rows_with_verdict"],
        "losses": closed_score_raw["losses"] if closed_score_raw["rows_with_verdict"] else position_score_raw["losses"],
        "win_rate": closed_score_raw["win_rate"] if closed_score_raw["rows_with_verdict"] else position_score_raw["win_rate"],
    }
    verdict = _build_verdict(trade_summary if resolve_trade_outcomes else fallback, pnl_analysis)
    return ProfileSummary(
        target=target,
        profile=profile,
        trade_summary=trade_summary,
        position_score=position_score,
        closed_position_score=closed_position_score,
        position_summary=_summarize_positions(positions),
        portfolio_pnl_analysis=pnl_analysis,
        portfolio_pnl_windows={k: len(v) for k, v in pnl_queries.items()},
        api_status={
            "data_api_trades": "ok" if trades else "unavailable_or_empty",
            "data_api_positions": "ok" if positions else "unavailable_or_empty",
            "data_api_closed_positions": "ok" if closed_positions else "unavailable_or_empty",
        },
        verdict=verdict,
        caveat="本工具只基于公开可抓取数据和启发式规则，不能证明任何人存在内幕交易。结论仅用于数据审计与策略研究。",
        raw_payloads={
            "profile": profile,
            "pnl_queries": pnl_queries,
            "trades": trades,
            "positions": positions,
            "closed_positions": closed_positions,
        },
    )
