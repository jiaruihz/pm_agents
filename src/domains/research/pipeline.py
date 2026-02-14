"""Pipeline orchestration functions."""

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .clients import clob as clob_client
from .clients import gamma as gamma_client
from .config import get_settings
from .db import init_db
from .storage import (
    get_active_token_ids,
    get_markets_for_parsing,
    get_markets_with_tokens,
    get_markets_by_ids,
    get_latest_market_rule_parses,
    get_latest_scores,
    get_evidence_records,
    get_market_events_from_raw,
    get_events_by_ids,
    get_latest_event_raw,
    get_latest_market_raw,
    get_market_rule_parse,
    get_market_rule_parse_samples,
    get_latest_orderbook,
    get_latest_price,
    get_token_orderbook_status,
    get_token_ids_by_status,
    get_sync_state,
    save_events_raw,
    save_events,
    save_markets,
    save_markets_raw,
    save_orderbook_levels,
    save_prices,
    save_scores,
    save_sync_state,
    save_token_orderbook_status,
    save_evidence_locker,
    update_market_statuses,
)
from .metrics import compute_metrics
from .parser import parse_market_with_llm, save_market_rule_parses_records, save_market_rule_parse_failure
from .scoring import build_feature_summary
from .nodes.constants import DEFAULT_READY_SCORE, STATUS_EXPIRED, STATUS_INVESTIGATED, STATUS_PARSED, STATUS_READY_TO_SEARCH
from .nodes.filter import FilterNode


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        if value.endswith("Z"):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        return datetime.fromisoformat(value)
    except Exception:
        return None


async def sync_gamma(
    active: bool,
    pages: int,
    page_size: int,
    closed: Optional[bool] = None,
    resume: bool = True,
) -> Dict[str, Any]:
    settings = get_settings()
    raw_m = 0
    raw_e = 0
    canonical = 0
    seen_markets = 0
    kept_markets = 0
    skipped_expired = 0
    markets_pages = 0
    events_pages = 0
    markets_short_page = False
    events_short_page = False

    if not resume:
        save_sync_state("markets", active, page_size, 0)
        save_sync_state("events", active, page_size, 0)

    start_offset = get_sync_state("markets", active, page_size) if resume else 0
    endpoint = f"{settings.gamma_base_url}/markets"
    params = {"active": str(active).lower(), "limit": page_size}
    if closed is not None:
        params["closed"] = str(closed).lower()
    async for items, offset in gamma_client.iter_paginated(
        endpoint, params=params, max_pages=pages, start_offset=start_offset
    ):
        markets_pages += 1
        today = datetime.now(timezone.utc).date()
        filtered_raw = []
        filtered_canonical = []
        for m in items:
            seen_markets += 1
            normalized = gamma_client.normalize_market(m)
            end_at = _parse_iso(normalized.get("end_at_utc"))
            if end_at and end_at.date() < today:
                skipped_expired += 1
                continue
            filtered_raw.append(m)
            filtered_canonical.append(normalized)
        if filtered_raw:
            raw_m += save_markets_raw(filtered_raw)
        if filtered_canonical:
            canonical += save_markets(filtered_canonical)
        kept_markets += len(filtered_canonical)
        if len(items) < page_size:
            markets_short_page = True
        save_sync_state("markets", active, page_size, offset + page_size)

    start_offset = get_sync_state("events", active, page_size) if resume else 0
    endpoint = f"{settings.gamma_base_url}/events"
    params = {"active": str(active).lower(), "limit": page_size}
    if closed is not None:
        params["closed"] = str(closed).lower()
    async for items, offset in gamma_client.iter_paginated(
        endpoint, params=params, max_pages=pages, start_offset=start_offset
    ):
        events_pages += 1
        raw_e += save_events_raw(items)
        save_events([gamma_client.normalize_event(e) for e in items])
        if len(items) < page_size:
            events_short_page = True
        save_sync_state("events", active, page_size, offset + page_size)

    return {
        "raw_markets": raw_m,
        "raw_events": raw_e,
        "canonical": canonical,
        "seen_markets": seen_markets,
        "kept_markets": kept_markets,
        "skipped_expired": skipped_expired,
        "markets_pages": markets_pages,
        "events_pages": events_pages,
        "markets_end_reached": markets_short_page or (markets_pages < pages),
        "events_end_reached": events_short_page or (events_pages < pages),
    }


def run_sync_gamma(
    active: bool,
    pages: int,
    page_size: int,
    closed: Optional[bool] = None,
    resume: bool = True,
) -> Dict[str, Any]:
    init_db()
    return asyncio.run(sync_gamma(active=active, pages=pages, page_size=page_size, closed=closed, resume=resume))


async def enrich_prices_orderbooks(
    limit: int,
    top_n: int,
    archive_books: bool,
    fetch_prices: bool = True,
    fetch_books: bool = True,
    two_stage: bool = False,
    orderbook_min_mid: Optional[float] = None,
    orderbook_max_mid: Optional[float] = None,
    market_statuses: Optional[List[str]] = None,
) -> Dict[str, Any]:
    if market_statuses:
        token_ids = get_token_ids_by_status(market_statuses, limit)
    else:
        token_ids = get_active_token_ids(limit)
    if not token_ids:
        return {"tokens": 0, "prices": 0, "levels": 0, "archives": 0}
    settings_count = len(token_ids)

    if not two_stage:
        results = await clob_client.enrich_token_batch(token_ids, top_n=top_n, archive_books=archive_books)
        price_rows = [p for p, _, _ in results if p] if fetch_prices else []
        level_rows = [lvl for _, levels, _ in results for lvl in levels] if fetch_books else []
        archives = [path for _, _, path in results if path] if archive_books else []
        saved_prices = save_prices(price_rows) if price_rows else 0
        saved_levels = save_orderbook_levels(level_rows) if level_rows else 0
        status_rows: List[Dict[str, Any]] = []
        if fetch_books:
            for price_row, levels, _ in results:
                if not levels:
                    continue
                token_id = None
                fetched_at = None
                if price_row:
                    token_id = price_row.get("token_id")
                    fetched_at = price_row.get("fetched_at_utc")
                if not token_id and levels:
                    token_id = levels[0].get("token_id")
                    fetched_at = levels[0].get("fetched_at_utc")
                if token_id and fetched_at:
                    status_rows.append({"token_id": str(token_id), "last_enriched_at_utc": fetched_at})
        saved_status = save_token_orderbook_status(status_rows) if status_rows else 0
        return {
            "tokens": settings_count,
            "prices": saved_prices,
            "levels": saved_levels,
            "archives": len(archives),
            "orderbook_status": saved_status,
        }

    mid_rows = await clob_client.fetch_midprice_batch(token_ids)
    mid_rows = [row for row in mid_rows if row]
    price_source = "midprice"
    if not mid_rows:
        results = await clob_client.enrich_token_batch(token_ids, top_n=1, archive_books=False, depth=1)
        mid_rows = [p for p, _, _ in results if p]
        price_source = "book"
    price_rows = mid_rows if fetch_prices else []
    saved_prices = save_prices(price_rows) if price_rows else 0

    eligible_token_ids: List[str] = []
    for row in mid_rows:
        if not row:
            continue
        mid_val = row.get("mid")
        if mid_val is None:
            continue
        if orderbook_min_mid is not None and mid_val < orderbook_min_mid:
            continue
        if orderbook_max_mid is not None and mid_val > orderbook_max_mid:
            continue
        eligible_token_ids.append(str(row.get("token_id")))

    level_rows: List[Dict[str, Any]] = []
    archives: List[str] = []
    saved_status = 0
    if fetch_books and eligible_token_ids:
        results = await clob_client.enrich_token_batch(
            eligible_token_ids,
            top_n=top_n,
            archive_books=archive_books,
        )
        book_price_rows = [p for p, _, _ in results if p] if fetch_prices else []
        if book_price_rows:
            saved_prices += save_prices(book_price_rows)
        level_rows = [lvl for _, levels, _ in results for lvl in levels] if fetch_books else []
        archives = [path for _, _, path in results if path] if archive_books else []
        status_rows: List[Dict[str, Any]] = []
        for price_row, levels, _ in results:
            if not levels:
                continue
            token_id = None
            fetched_at = None
            if price_row:
                token_id = price_row.get("token_id")
                fetched_at = price_row.get("fetched_at_utc")
            if not token_id and levels:
                token_id = levels[0].get("token_id")
                fetched_at = levels[0].get("fetched_at_utc")
            if token_id and fetched_at:
                status_rows.append({"token_id": str(token_id), "last_enriched_at_utc": fetched_at})
        saved_status = save_token_orderbook_status(status_rows) if status_rows else 0

    saved_levels = save_orderbook_levels(level_rows) if level_rows else 0
    return {
        "tokens": settings_count,
        "prices": saved_prices,
        "levels": saved_levels,
        "archives": len(archives),
        "book_tokens": len(eligible_token_ids),
        "price_source": price_source,
        "orderbook_status": saved_status if fetch_books else 0,
    }


def run_enrich(
    limit: int,
    top_n: int,
    archive_books: bool,
    fetch_prices: bool = True,
    fetch_books: bool = True,
    two_stage: bool = False,
    orderbook_min_mid: Optional[float] = None,
    orderbook_max_mid: Optional[float] = None,
    market_statuses: Optional[List[str]] = None,
) -> Dict[str, Any]:
    init_db()
    return asyncio.run(
        enrich_prices_orderbooks(
            limit=limit,
            top_n=top_n,
            archive_books=archive_books,
            fetch_prices=fetch_prices,
            fetch_books=fetch_books,
            two_stage=two_stage,
            orderbook_min_mid=orderbook_min_mid,
            orderbook_max_mid=orderbook_max_mid,
            market_statuses=market_statuses,
        )
    )


async def llm_parse_batch(batch: int, allow_reparse: bool = False) -> Dict[str, Any]:
    markets = get_markets_for_parsing(limit=batch, prompt_version="v1", allow_reparse=allow_reparse)
    if not markets:
        return {"parsed": 0}
    parsed_count = 0
    for m in markets:
        try:
            parsed = await parse_market_with_llm(m)
        except Exception:
            continue
        if parsed:
            save_market_rule_parses_records(m["market_id"], parsed, parsed.dict())
            parsed_count += 1
    return {"parsed": parsed_count}


def run_llm_parse(batch: int, allow_reparse: bool = False) -> Dict[str, Any]:
    init_db()
    try:
        return asyncio.run(llm_parse_batch(batch=batch, allow_reparse=allow_reparse))
    except ValueError as e:
        return {"parsed": 0, "error": str(e)}


def compute_scores_and_candidates(min_volume: float, max_spread: float, clarity_threshold: float, dispute_threshold: float):
    init_db()
    markets = get_markets_with_tokens()
    scored_rows = []
    candidates = []
    from datetime import datetime, timezone
    import json

    scored_at = datetime.now(timezone.utc).isoformat()
    for m in markets:
        token_ids = []
        try:
            token_ids = json.loads(m.get("clob_token_ids_json") or "[]")
        except Exception:
            token_ids = []
        if not token_ids:
            continue
        token_id = str(token_ids[0])
        price_row = get_latest_price(token_id)
        levels = get_latest_orderbook(token_id)
        if not price_row:
            continue
        metrics = compute_metrics(price_row, levels)
        rule_rec = get_market_rule_parse(m["market_id"], "v1") or {}
        features = {
            **metrics,
            "volume": m.get("volume"),
            "liquidity": m.get("liquidity"),
            "trigger_type": (json.loads(rule_rec.get("parsed_json", "{}")) or {}).get("trigger_type", "unknown")
            if rule_rec
            else "unknown",
            "clarity_score": rule_rec.get("clarity_score"),
            "dispute_risk_score": rule_rec.get("dispute_risk_score"),
            "ambiguity_flags": json.loads(rule_rec.get("ambiguity_flags_json", "[]")) if rule_rec else [],
        }
        scores = build_feature_summary(metrics, features)
        features_json = json.dumps({**features, **scores}, ensure_ascii=False)
        scored_rows.append(
            {
                "market_id": m["market_id"],
                "scored_at_utc": scored_at,
                "total_score": scores["total_score"],
                "features_json": features_json,
            }
        )
        # Candidate filter
        if (
            m.get("active") == 1
            and m.get("resolved") == 0
            and (m.get("volume") or 0) >= min_volume
            and metrics.get("spread") is not None
            and metrics.get("spread") <= max_spread
            and (rule_rec.get("clarity_score") or 0) >= clarity_threshold
            and (rule_rec.get("dispute_risk_score") or 0) <= dispute_threshold
        ):
            candidates.append(
                {
                    "slug": m.get("slug"),
                    "market_id": m.get("market_id"),
                    "end_at_utc": m.get("end_at_utc"),
                    "category": m.get("category"),
                    **{k: features.get(k) for k in ["volume", "liquidity"]},
                    **{k: metrics.get(k) for k in ["mid", "best_bid", "best_ask", "spread", "spread_pct_mid", "depth_1pct_bid", "depth_1pct_ask", "depth_2pct_bid", "depth_2pct_ask"]},
                    "trigger_type": features.get("trigger_type"),
                    "clarity_score": features.get("clarity_score"),
                    "dispute_risk_score": features.get("dispute_risk_score"),
                    "ambiguity_flags": features.get("ambiguity_flags"),
                    "trigger_minimum_conditions": (json.loads(rule_rec.get("parsed_json", "{}")) or {}).get(
                        "trigger_minimum_conditions", []
                    ) if rule_rec else [],
                    "notes_for_humans": (json.loads(rule_rec.get("parsed_json", "{}")) or {}).get("notes_for_humans", "")
                    if rule_rec
                    else "",
                }
            )

    saved = save_scores(scored_rows)
    return {"scores_saved": saved, "candidates": candidates}


def preview_token_metrics(sample_size: int = 3) -> List[Dict[str, Any]]:
    tokens = get_active_token_ids(sample_size)
    previews: List[Dict[str, Any]] = []
    for tid in tokens:
        price = get_latest_price(tid)
        levels = get_latest_orderbook(tid)
        if not price:
            continue
        metrics = compute_metrics(price, levels)
        previews.append({"token_id": tid, **metrics})
        if len(previews) >= sample_size:
            break
    return previews


def preview_market_rule_parses(sample_size: int = 3) -> List[Dict[str, Any]]:
    import json

    samples = get_market_rule_parse_samples(sample_size)
    out: List[Dict[str, Any]] = []
    for s in samples:
        parsed_json = json.loads(s.get("parsed_json", "{}")) if s.get("parsed_json") else {}
        out.append(
            {
                "market_id": s.get("market_id"),
                "trigger_type": parsed_json.get("trigger_type"),
                "clarity_score": s.get("clarity_score"),
                "rule_score": s.get("rule_score"),
                "ambiguity_flags": json.loads(s.get("ambiguity_flags_json", "[]")) if s.get("ambiguity_flags_json") else [],
            }
        )
    return out


def get_market_details(
    market_ids: List[str],
    include_analysis: bool = True,
    include_evidence: bool = True,
    include_orderbooks: bool = True,
    include_scores: bool = True,
    include_raw: bool = False,
) -> List[Dict[str, Any]]:
    init_db()
    ids = [mid for mid in market_ids if mid]
    if not ids:
        return []
    markets = get_markets_by_ids(ids)
    market_map = {m["market_id"]: m for m in markets}
    analysis_map = get_latest_market_rule_parses(ids) if include_analysis else {}
    evidence_map = get_evidence_records(ids) if include_evidence else {}
    scores_map = get_latest_scores(ids) if include_scores else {}
    raw_markets_map = get_latest_market_raw(ids) if include_raw else {}

    def _safe_list(value: Optional[str]) -> List[Any]:
        try:
            data = json.loads(value or "[]")
            return data if isinstance(data, list) else []
        except Exception:
            return []

    event_ids_set = set()
    market_events: Dict[str, List[str]] = {}
    raw_events = get_market_events_from_raw(market_map.keys())
    for mid, market in market_map.items():
        event_ids = [str(eid) for eid in _safe_list(market.get("event_ids_json")) if eid]
        if not event_ids:
            event_ids = raw_events.get(mid, {}).get("event_ids") or []
        if event_ids:
            market_events[mid] = event_ids
            event_ids_set.update(event_ids)
    events_map = get_events_by_ids(list(event_ids_set)) if event_ids_set else {}
    raw_events_map = get_latest_event_raw(list(event_ids_set)) if include_raw and event_ids_set else {}

    details: List[Dict[str, Any]] = []
    for mid in ids:
        market = market_map.get(mid)
        if not market:
            continue
        outcomes = _safe_list(market.get("outcomes_json"))
        outcome_prices = _safe_list(market.get("outcome_prices_json"))
        token_ids = _safe_list(market.get("clob_token_ids_json"))

        tokens: List[Dict[str, Any]] = []
        status_map = get_token_orderbook_status([str(tid) for tid in token_ids])
        for tid in token_ids:
            price_row = get_latest_price(str(tid))
            levels = get_latest_orderbook(str(tid)) if include_orderbooks else []
            metrics = compute_metrics(price_row, levels) if price_row else {}
            orderbook_status = status_map.get(str(tid))
            tokens.append(
                {
                    "token_id": str(tid),
                    "latest_price": price_row,
                    "orderbook_levels": levels,
                    "metrics": metrics,
                    "orderbook_status": orderbook_status,
                }
            )

        event_details: List[Dict[str, Any]] = []
        raw_event = raw_events.get(mid) or {}
        for eid in market_events.get(mid, []):
            ev = events_map.get(eid)
            if not ev:
                continue
            tags = _safe_list(ev.get("tags_json"))
            ev_payload = dict(ev)
            ev_payload["tags"] = tags
            event_details.append(ev_payload)

        market_payload = dict(market)
        market_payload["market_url"] = (
            f"https://polymarket.com/market/{market.get('slug')}" if market.get("slug") else None
        )
        market_payload["event_ids"] = market_events.get(mid, [])
        market_payload["event_slugs"] = _safe_list(market.get("event_slugs_json")) or raw_event.get("event_slugs") or []
        market_payload["event_titles"] = _safe_list(market.get("event_titles_json")) or raw_event.get("event_titles") or []
        market_payload["event_tickers"] = _safe_list(market.get("event_tickers_json")) or raw_event.get("event_tickers") or []

        raw_payload = None
        if include_raw:
            raw_payload = {
                "market": raw_markets_map.get(mid),
                "events": {eid: raw_events_map.get(eid) for eid in market_events.get(mid, [])},
            }

        details.append(
            {
                "market": market_payload,
                "parsed": {
                    "outcomes": outcomes,
                    "outcome_prices": outcome_prices,
                    "token_ids": token_ids,
                },
                "tokens": tokens,
                "events": event_details,
                "analysis": analysis_map.get(mid),
                "evidence": evidence_map.get(mid),
                "scores": scores_map.get(mid),
                "raw": raw_payload,
            }
        )
    return details


def _extract_slug_from_url(url: str) -> Optional[str]:
    try:
        from urllib.parse import urlparse
    except Exception:
        return None
    parsed = urlparse(url)
    if not parsed.path:
        return None
    parts = [p for p in parsed.path.split("/") if p]
    if not parts:
        return None
    if "market" in parts:
        idx = parts.index("market")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    return parts[-1]


def _resolve_market_input(
    market_id: Optional[str],
    market_url: Optional[str],
    market_input: Optional[str],
) -> Dict[str, Optional[str]]:
    if not market_id and not market_url and market_input:
        if market_input.startswith("http"):
            market_url = market_input
        else:
            market_id = market_input
    slug = _extract_slug_from_url(market_url) if market_url else None
    return {"market_id": market_id, "slug": slug}


def _safe_list(value: Optional[str]) -> List[Any]:
    try:
        data = json.loads(value or "[]")
        return data if isinstance(data, list) else []
    except Exception:
        return []


async def _sync_single_market(market_id: Optional[str], slug: Optional[str]) -> Optional[Dict[str, Any]]:
    raw = await gamma_client.fetch_market_by_id_or_slug(market_id=market_id, slug=slug)
    if not raw:
        return None
    save_markets_raw([raw])
    normalized = gamma_client.normalize_market(raw)
    save_markets([normalized])
    events = raw.get("events") if isinstance(raw, dict) else []
    if isinstance(events, list) and events:
        save_events_raw(events)
        save_events([gamma_client.normalize_event(ev) for ev in events if isinstance(ev, dict)])
    return normalized


async def _enrich_market_tokens(token_ids: List[str], top_n: int) -> Dict[str, Any]:
    if not token_ids:
        return {"prices": 0, "levels": 0}
    try:
        results = await clob_client.enrich_token_batch(token_ids, top_n=top_n, archive_books=False)
    except Exception as exc:
        return {"prices": 0, "levels": 0, "error": str(exc)}
    price_rows = [p for p, _, _ in results if p]
    level_rows = [lvl for _, levels, _ in results for lvl in levels]
    saved_prices = save_prices(price_rows) if price_rows else 0
    saved_levels = save_orderbook_levels(level_rows) if level_rows else 0
    status_rows: List[Dict[str, Any]] = []
    for price_row, levels, _ in results:
        if not levels:
            continue
        token_id = None
        fetched_at = None
        if price_row:
            token_id = price_row.get("token_id")
            fetched_at = price_row.get("fetched_at_utc")
        if not token_id and levels:
            token_id = levels[0].get("token_id")
            fetched_at = levels[0].get("fetched_at_utc")
        if token_id and fetched_at:
            status_rows.append({"token_id": str(token_id), "last_enriched_at_utc": fetched_at})
    saved_status = save_token_orderbook_status(status_rows) if status_rows else 0
    return {"prices": saved_prices, "levels": saved_levels, "orderbook_status": saved_status}


def build_final_prompt(
    market_id: str,
    template_path: str = "data/template.md",
    output_dir: str = "output",
) -> Dict[str, Any]:
    template_file = Path(template_path)
    if not template_file.exists():
        return {"error": "template_not_found", "template_path": template_path}
    details = get_market_details(
        [market_id],
        include_analysis=True,
        include_evidence=True,
        include_orderbooks=True,
        include_scores=True,
        include_raw=True,
    )
    if not details:
        return {"error": "market_not_found"}
    payload = details[0]
    template = template_file.read_text(encoding="utf-8")
    market_json = json.dumps(payload, ensure_ascii=False, indent=2)
    template = template.replace("<<<MARKET_JSON>>>", market_json)
    market_url = payload.get("market", {}).get("market_url") or ""
    if "- market_url:" in template:
        template = template.replace("- market_url:", f"- market_url: {market_url}")
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    final_path = output_path / f"final_prompt_{market_id}.md"
    final_path.write_text(template, encoding="utf-8")
    return {"output_path": str(final_path), "preview": template[:4000]}


def run_single_market_flow(
    market_id: Optional[str] = None,
    market_url: Optional[str] = None,
    market_input: Optional[str] = None,
    top_n: Optional[int] = None,
    build_prompt: bool = True,
) -> Dict[str, Any]:
    init_db()
    settings = get_settings()
    resolved = _resolve_market_input(market_id, market_url, market_input)

    async def _run() -> Dict[str, Any]:
        synced = await _sync_single_market(resolved["market_id"], resolved["slug"])
        if not synced:
            return {"error": "market_not_found"}
        market_id_value = str(synced.get("market_id"))
        token_ids = [str(tid) for tid in _safe_list(synced.get("clob_token_ids_json")) if tid]
        market_rows = get_markets_by_ids([market_id_value])
        if not market_rows:
            return {"error": "market_missing_after_sync"}
        filter_node = FilterNode(metadata_only=True)
        filter_res = filter_node.evaluate_market(market_rows[0])
        enrich_res = None
        if filter_res.get("status") != STATUS_EXPIRED:
            enrich_res = await _enrich_market_tokens(token_ids, top_n or settings.orderbook_top_n)

        parse_res: Optional[Dict[str, Any]] = None
        evidence_res: Optional[Dict[str, Any]] = None
        status_after_parse = None
        if filter_res.get("passed") and filter_res.get("status") != STATUS_EXPIRED:
            try:
                parsed = await parse_market_with_llm(market_rows[0])
            except Exception as exc:
                save_market_rule_parse_failure(market_id_value, str(exc))
                parse_res = {"market_id": market_id_value, "parsed": False, "error": str(exc)}
            else:
                if not parsed:
                    save_market_rule_parse_failure(market_id_value, "parse_failed")
                    parse_res = {"market_id": market_id_value, "parsed": False}
                else:
                    alpha_score = int(round((parsed.clarity_score or 0) * 100))
                    save_market_rule_parses_records(
                        market_id_value,
                        parsed,
                        parsed.dict(),
                        strategy_tag="MANUAL_RUN",
                        alpha_score=alpha_score,
                        hard_constraints=[],
                        search_keywords=[],
                    )
                    parse_res = {"market_id": market_id_value, "parsed": True, "alpha_score": alpha_score}
                    if filter_res.get("passed"):
                        status_after_parse = STATUS_READY_TO_SEARCH if alpha_score >= DEFAULT_READY_SCORE else STATUS_PARSED
                        update_market_statuses([market_id_value], status_after_parse)
        else:
            skip_reason = "expired" if filter_res.get("status") == STATUS_EXPIRED else "filtered_out"
            parse_res = {"market_id": market_id_value, "parsed": False, "skipped": skip_reason}

        if filter_res.get("passed") and status_after_parse == STATUS_READY_TO_SEARCH:
            record = {
                "market_id": market_id_value,
                "search_summary": "",
                "verification_result": "UNCERTAIN",
                "source_links_json": "[]",
            }
            saved = save_evidence_locker([record])
            updated = update_market_statuses([market_id_value], STATUS_INVESTIGATED)
            evidence_res = {"evidence_saved": saved, "status": STATUS_INVESTIGATED, "updated": updated}

        prompt_res = build_final_prompt(market_id_value) if build_prompt else None
        return {
            "market_id": market_id_value,
            "sync": {"market_id": market_id_value, "token_ids": token_ids},
            "enrich": enrich_res,
            "filter": filter_res,
            "parse": parse_res,
            "evidence": evidence_res,
            "prompt": prompt_res,
        }

    return asyncio.run(_run())
