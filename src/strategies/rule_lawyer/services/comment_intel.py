from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from src.platform.clients.polymarket_comments import PolymarketCommentsClient
from src.strategies.rule_lawyer.models_research import CommentRecord, ResolvedMarket
from src.strategies.rule_lawyer.services.common import to_float


URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
SPAM_RE = re.compile(r"(telegram|discord|dm me|send funds|wallet|airdrop|follow me)", re.IGNORECASE)
BULLISH_RE = re.compile(r"\b(yes|higher|bull|buy|likely|will happen|over|upside|priced low)\b", re.IGNORECASE)
BEARISH_RE = re.compile(r"\b(no|lower|bear|sell|unlikely|won't|under|overpriced|priced high)\b", re.IGNORECASE)
RISK_RE = re.compile(r"\b(rule|resolve|resolution|ambigu|risk|committee|discretion|unclear|dispute)\b", re.IGNORECASE)


def _normalize_comment(row: Dict[str, Any], wallet_score_lookup: Optional[Dict[str, float]] = None) -> CommentRecord:
    reactions = row.get("reactions") if isinstance(row.get("reactions"), list) else []
    reports = row.get("reports") if isinstance(row.get("reports"), list) else []
    profile = row.get("profile") if isinstance(row.get("profile"), dict) else {}
    position_context = {}
    for key in ["position", "positions", "userPosition", "marketPosition"]:
        if row.get(key):
            position_context[key] = row.get(key)
    body = str(row.get("body") or row.get("commentBody") or "").strip()
    wallet = str(profile.get("proxyWallet") or row.get("proxyWallet") or "").strip().lower()
    record = CommentRecord(
        comment_id=str(row.get("id") or row.get("commentID") or ""),
        body=body,
        created_at=str(row.get("createdAt") or ""),
        reaction_count=int(row.get("reactionCount") or len(reactions) or 0),
        reply_count=int(row.get("replyCount") or 0),
        report_count=int(row.get("reportCount") or len(reports) or 0),
        profile_wallet=wallet,
        profile_name=str(profile.get("name") or row.get("name") or "").strip(),
        profile_pseudonym=str(profile.get("pseudonym") or row.get("pseudonym") or "").strip(),
        profile_verified=bool(profile.get("verified", False)),
        position_context=position_context,
        raw=row,
    )
    scored = score_comment(record, wallet_score_lookup or {})
    return scored


def score_comment(record: CommentRecord, wallet_score_lookup: Dict[str, float]) -> CommentRecord:
    body = (record.body or "").strip()
    text = body.lower()
    classification = "noise"
    bias = "neutral"
    if RISK_RE.search(text):
        classification = "risk_note"
    elif BULLISH_RE.search(text):
        classification = "bullish_evidence"
        bias = "yes"
    elif BEARISH_RE.search(text):
        classification = "bearish_evidence"
        bias = "no"
    likes_weight = min(10.0, float(record.reaction_count))
    holder_bonus = 4.0 if record.position_context else 0.0
    wallet_bonus = min(8.0, max(0.0, wallet_score_lookup.get(record.profile_wallet, 0.0)))
    specificity_bonus = 0.0
    if len(body) >= 120:
        specificity_bonus += 2.0
    if URL_RE.search(body):
        specificity_bonus += 2.0
    if any(x in text for x in ["%", "$", "odds", "price", "probability", "chance", "points"]):
        specificity_bonus += 1.0
    spam_penalty = 6.0 if SPAM_RE.search(body) else 0.0
    if len(body) < 20:
        spam_penalty += 1.5
    record.classification = classification
    record.bias_direction = bias
    record.value_score = round(max(0.0, likes_weight + holder_bonus + wallet_bonus + specificity_bonus - spam_penalty), 4)
    return record


def summarize_comments(records: List[CommentRecord]) -> Dict[str, Any]:
    bullish = [x for x in records if x.classification == "bullish_evidence"]
    bearish = [x for x in records if x.classification == "bearish_evidence"]
    risk_notes = [x for x in records if x.classification == "risk_note"]
    top = sorted(records, key=lambda x: (x.value_score, x.reaction_count), reverse=True)
    return {
        "total_comments": len(records),
        "bullish_comments": len(bullish),
        "bearish_comments": len(bearish),
        "risk_notes": len(risk_notes),
        "top_commentary": [x.to_dict() for x in top[:10]],
    }


def _fetch_candidate_comments(
    client: PolymarketCommentsClient,
    entity_type: str,
    entity_id: str,
    order: str,
    limit: int,
) -> List[Dict[str, Any]]:
    try:
        return client.fetch_comments(entity_type, entity_id, order=order, limit=limit, offset=0)
    except Exception:
        return []


def _best_effort_fetch_market_comments(
    market: ResolvedMarket,
    client: PolymarketCommentsClient,
    mode: str,
    limit: int,
) -> Tuple[str, str, List[Dict[str, Any]]]:
    candidates = [
        ("market", market.market_id),
        ("event", market.event_id),
        ("market_comment", market.market_id),
        ("question", market.market_id),
        ("market", market.condition_id),
    ]
    orders = ["reactionCount"] if mode == "top_only" else ["reactionCount", "createdAt"]
    for entity_type, entity_id in candidates:
        if not entity_id:
            continue
        merged: List[Dict[str, Any]] = []
        seen = set()
        for order in orders:
            rows = _fetch_candidate_comments(client, entity_type, entity_id, order=order, limit=limit if len(orders) == 1 else max(1, limit // len(orders)))
            for row in rows:
                comment_id = str(row.get("id") or row.get("commentID") or "")
                key = comment_id or f"{row.get('createdAt')}::{row.get('body')}"
                if key in seen:
                    continue
                seen.add(key)
                merged.append(row)
        if merged:
            return "ok", f"{entity_type}:{entity_id}", merged
    return "unavailable", "", []


def collect_market_comments(
    market: ResolvedMarket,
    comment_limit: int = 30,
    mode: str = "top_and_newest",
    fallback: str = "auto",
    wallet_score_lookup: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    client = PolymarketCommentsClient()
    status, method, raw_rows = _best_effort_fetch_market_comments(market, client, mode=mode, limit=comment_limit)
    if status != "ok":
        return {
            "comment_status": "unavailable",
            "comment_fetch_method": method,
            "comment_stats": {"total_comments": 0, "bullish_comments": 0, "bearish_comments": 0, "risk_notes": 0},
            "top_commentary": [],
            "records": [],
            "raw_comments": [],
            "fallback": fallback,
        }
    records = [_normalize_comment(row, wallet_score_lookup) for row in raw_rows if isinstance(row, dict)]
    summary = summarize_comments(records)
    return {
        "comment_status": "ok",
        "comment_fetch_method": method,
        "comment_stats": {k: v for k, v in summary.items() if k != "top_commentary"},
        "top_commentary": summary["top_commentary"],
        "records": [x.to_dict() for x in records],
        "raw_comments": raw_rows,
        "fallback": fallback,
    }
