"""Storage helpers for upsert and queries."""

import json
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from src.models import Event, Market

from .db import upsert_many, db_cursor
from src.platform.clients.gamma import normalize_event


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def save_markets_raw(records: Iterable[Dict[str, Any]]) -> int:
    rows = []
    now = _now_utc_iso()
    for rec in records:
        mid = rec.get("id") or rec.get("marketId")
        if not mid:
            continue
        rows.append({"market_id": mid, "fetched_at_utc": now, "json": json.dumps(rec)})
    return upsert_many("markets_raw", rows)


def save_events_raw(records: Iterable[Dict[str, Any]]) -> int:
    rows = []
    now = _now_utc_iso()
    for rec in records:
        eid = rec.get("id") or rec.get("eventId")
        if not eid:
            continue
        rows.append({"event_id": eid, "fetched_at_utc": now, "json": json.dumps(rec)})
    return upsert_many("events_raw", rows)


def save_events(records: Iterable[Event]) -> int:
    rows: List[Dict[str, Any]] = []
    for rec in records:
        row = rec.to_storage_row()
        if not row.get("event_id"):
            continue
        rows.append(row)
    return upsert_many("events", rows)


def save_markets(records: Iterable[Market]) -> int:
    rows: List[Dict[str, Any]] = []
    for rec in records:
        row = rec.to_storage_row()
        if not row.get("market_id"):
            continue
        row.setdefault("status", None)
        row.setdefault("status_updated_at", None)
        row.setdefault("outcomes_json", None)
        row.setdefault("outcome_prices_json", None)
        row.setdefault("event_ids_json", None)
        row.setdefault("event_slugs_json", None)
        row.setdefault("event_titles_json", None)
        row.setdefault("event_tickers_json", None)
        rows.append(row)
    return upsert_many("markets", rows)


def save_prices(records: Iterable[Dict[str, Any]]) -> int:
    return upsert_many("prices", records)


def save_orderbook_levels(records: Iterable[Dict[str, Any]]) -> int:
    return upsert_many("orderbook_levels", records)


def save_token_orderbook_status(records: Iterable[Dict[str, Any]]) -> int:
    return upsert_many("token_orderbook_status", records)


def save_market_rule_parses(records: Iterable[Dict[str, Any]]) -> int:
    return upsert_many("market_rule_parses", records)


def save_scores(records: Iterable[Dict[str, Any]]) -> int:
    return upsert_many("scores", records)


def save_evidence_locker(records: Iterable[Dict[str, Any]]) -> int:
    return upsert_many("evidence_locker", records)


def mark_new_markets() -> int:
    sql = "UPDATE markets SET status='NEW', status_updated_at=? WHERE status IS NULL"
    with db_cursor() as cur:
        cur.execute(sql, (_now_utc_iso(),))
        return cur.rowcount


def resurrect_markets(liquidity_threshold: float) -> int:
    sql = """
    UPDATE markets
    SET status='NEW', status_updated_at=?
    WHERE status='IGNORED' AND (liquidity IS NOT NULL AND liquidity > ?)
    """
    with db_cursor() as cur:
        cur.execute(sql, (_now_utc_iso(), liquidity_threshold))
        return cur.rowcount


def update_market_statuses(market_ids: Iterable[str], status: str) -> int:
    rows = [{"market_id": mid, "status": status, "status_updated_at": _now_utc_iso()} for mid in market_ids]
    if not rows:
        return 0
    sql = "UPDATE markets SET status=?, status_updated_at=? WHERE market_id=?"
    with db_cursor() as cur:
        cur.executemany(sql, [(r["status"], r["status_updated_at"], r["market_id"]) for r in rows])
        return cur.rowcount


def get_markets_by_status(statuses: Iterable[str], limit: int) -> List[Dict[str, Any]]:
    return get_markets_for_filter(statuses=statuses, limit=limit)


def get_markets_for_filter(
    statuses: Optional[Iterable[str]] = None,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    cols = (
        "market_id, slug, question, description, rules, category, end_at_utc, volume, liquidity, "
        "clob_token_ids_json, outcomes_json, outcome_prices_json, active, resolved, status, status_updated_at, last_synced_at_utc"
    )
    params: List[Any] = []
    where_clause = ""
    status_list = list(statuses or [])
    if status_list:
        placeholders = ", ".join(["?"] * len(status_list))
        include_null = "NEW" in status_list
        where_clause = f"WHERE status IN ({placeholders})"
        if include_null:
            where_clause = f"WHERE (status IN ({placeholders}) OR status IS NULL)"
        params.extend(status_list)
    limit_clause = ""
    if limit is not None and limit > 0:
        limit_clause = "LIMIT ?"
        params.append(limit)
    sql = f"""
    SELECT {cols}
    FROM markets
    {where_clause}
    ORDER BY last_synced_at_utc DESC
    {limit_clause}
    """
    with db_cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
        return [dict(r) for r in rows]


def get_markets_by_ids(market_ids: Iterable[str]) -> List[Dict[str, Any]]:
    ids = [mid for mid in market_ids if mid]
    if not ids:
        return []
    placeholders = ", ".join(["?"] * len(ids))
    sql = f"""
    SELECT market_id, slug, question, description, rules, category, end_at_utc, volume, liquidity,
           clob_token_ids_json, outcomes_json, outcome_prices_json, event_ids_json, event_slugs_json, event_titles_json,
           event_tickers_json, active, resolved, status, status_updated_at, last_synced_at_utc
    FROM markets
    WHERE market_id IN ({placeholders})
    """
    with db_cursor() as cur:
        cur.execute(sql, ids)
        rows = cur.fetchall()
        return [dict(r) for r in rows]


def get_market_events_from_raw(market_ids: Iterable[str]) -> Dict[str, Dict[str, List[str]]]:
    ids = [mid for mid in market_ids if mid]
    if not ids:
        return {}
    placeholders = ", ".join(["?"] * len(ids))
    sql = f"""
    SELECT market_id, fetched_at_utc, json
    FROM markets_raw
    WHERE market_id IN ({placeholders})
    ORDER BY fetched_at_utc DESC
    """
    raw_map: Dict[str, Dict[str, List[str]]] = {}
    with db_cursor() as cur:
        cur.execute(sql, ids)
        rows = cur.fetchall()
        for row in rows:
            mid = row["market_id"]
            if mid in raw_map:
                continue
            try:
                raw = json.loads(row["json"])
            except Exception:
                continue
            events = raw.get("events") or []
            event_ids: List[str] = []
            event_slugs: List[str] = []
            event_titles: List[str] = []
            event_tickers: List[str] = []
            if isinstance(events, list):
                for event in events:
                    if not isinstance(event, dict):
                        continue
                    eid = event.get("id") or event.get("eventId")
                    if eid is not None:
                        event_ids.append(str(eid))
                    slug = event.get("slug")
                    if slug:
                        event_slugs.append(str(slug))
                    title = event.get("title")
                    if title:
                        event_titles.append(str(title))
                    ticker = event.get("ticker")
                    if ticker:
                        event_tickers.append(str(ticker))
            raw_map[mid] = {
                "event_ids": event_ids,
                "event_slugs": event_slugs,
                "event_titles": event_titles,
                "event_tickers": event_tickers,
            }
    return raw_map


def get_events_by_ids(event_ids: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    ids = [eid for eid in event_ids if eid]
    if not ids:
        return {}
    placeholders = ", ".join(["?"] * len(ids))
    sql = f"""
    SELECT event_id, slug, title, description, ticker, tags_json, active, closed, start_at_utc, end_at_utc,
           volume, liquidity, updated_at_utc, last_synced_at_utc
    FROM events
    WHERE event_id IN ({placeholders})
    """
    with db_cursor() as cur:
        cur.execute(sql, ids)
        rows = cur.fetchall()
        event_map = {row["event_id"]: dict(row) for row in rows}
    missing = [eid for eid in ids if eid not in event_map]
    if not missing:
        return event_map
    placeholders = ", ".join(["?"] * len(missing))
    sql = f"""
    SELECT event_id, fetched_at_utc, json
    FROM events_raw
    WHERE event_id IN ({placeholders})
    ORDER BY fetched_at_utc DESC
    """
    raw_models: Dict[str, Event] = {}
    with db_cursor() as cur:
        cur.execute(sql, missing)
        rows = cur.fetchall()
        for row in rows:
            eid = row["event_id"]
            if eid in raw_models:
                continue
            try:
                raw = json.loads(row["json"])
            except Exception:
                continue
            raw_models[eid] = normalize_event(raw)
    if raw_models:
        save_events(raw_models.values())
        event_map.update({eid: model.to_storage_row() for eid, model in raw_models.items()})
    return event_map


def get_latest_market_rule_parses(market_ids: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    ids = [mid for mid in market_ids if mid]
    if not ids:
        return {}
    placeholders = ", ".join(["?"] * len(ids))
    sql = f"""
    SELECT ac.*
    FROM market_rule_parses ac
    JOIN (
        SELECT market_id, MAX(parsed_at_utc) AS max_parsed
        FROM market_rule_parses
        WHERE market_id IN ({placeholders})
        GROUP BY market_id
    ) latest
      ON ac.market_id = latest.market_id AND ac.parsed_at_utc = latest.max_parsed
    """
    with db_cursor() as cur:
        cur.execute(sql, ids)
        rows = cur.fetchall()
        return {row["market_id"]: dict(row) for row in rows}


def get_evidence_records(market_ids: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    ids = [mid for mid in market_ids if mid]
    if not ids:
        return {}
    placeholders = ", ".join(["?"] * len(ids))
    sql = f"""
    SELECT market_id, search_summary, verification_result, source_links_json
    FROM evidence_locker
    WHERE market_id IN ({placeholders})
    """
    with db_cursor() as cur:
        cur.execute(sql, ids)
        rows = cur.fetchall()
        return {row["market_id"]: dict(row) for row in rows}


def get_sync_state(source: str, active: bool, page_size: int) -> int:
    sql = "SELECT offset FROM sync_state WHERE source=? AND active=? AND page_size=? LIMIT 1"
    with db_cursor() as cur:
        cur.execute(sql, (source, int(bool(active)), page_size))
        row = cur.fetchone()
        return int(row[0]) if row else 0


def save_sync_state(source: str, active: bool, page_size: int, offset: int) -> int:
    rows = [
        {
            "source": source,
            "active": int(bool(active)),
            "page_size": page_size,
            "offset": int(offset),
            "updated_at_utc": _now_utc_iso(),
        }
    ]
    return upsert_many("sync_state", rows)


def get_active_token_ids(limit: int) -> List[str]:
    sql = "SELECT clob_token_ids_json FROM markets WHERE active=1 AND resolved=0 ORDER BY last_synced_at_utc DESC"
    tokens: List[str] = []
    with db_cursor() as cur:
        cur.execute(sql)
        for row in cur.fetchall():
            try:
                ids = json.loads(row[0]) if row[0] else []
                for tid in ids:
                    tokens.append(str(tid))
            except Exception:
                continue
            if len(tokens) >= limit:
                break
    return tokens[:limit]


def get_token_ids_by_status(statuses: Iterable[str], limit: int) -> List[str]:
    status_list = [s for s in statuses if s]
    if not status_list:
        return []
    placeholders = ", ".join(["?"] * len(status_list))
    sql = f"""
    SELECT clob_token_ids_json
    FROM markets
    WHERE status IN ({placeholders})
    ORDER BY last_synced_at_utc DESC
    """
    tokens: List[str] = []
    with db_cursor() as cur:
        cur.execute(sql, status_list)
        for row in cur.fetchall():
            try:
                ids = json.loads(row[0]) if row[0] else []
                for tid in ids:
                    tokens.append(str(tid))
            except Exception:
                continue
            if len(tokens) >= limit:
                break
    return tokens[:limit]


def get_markets_for_parsing(limit: int, prompt_version: str, allow_reparse: bool = False) -> List[Dict[str, Any]]:
    base_sql = """
    SELECT m.market_id, m.slug, m.question, m.description, m.rules, m.category, m.end_at_utc, m.last_synced_at_utc
    FROM markets m
    LEFT JOIN market_rule_parses ac
        ON m.market_id = ac.market_id
        AND ac.prompt_version = ?
    """
    if allow_reparse:
        where_clause = "WHERE ac.market_id IS NULL OR (m.last_synced_at_utc > ac.parsed_at_utc)"
    else:
        where_clause = "WHERE ac.market_id IS NULL"
    sql = f"""
    {base_sql}
    {where_clause}
    ORDER BY m.last_synced_at_utc DESC
    LIMIT ?
    """
    with db_cursor() as cur:
        cur.execute(sql, (prompt_version, limit))
        rows = cur.fetchall()
        return [dict(row) for row in rows]


def get_markets_with_tokens() -> List[Dict[str, Any]]:
    sql = "SELECT market_id, slug, category, end_at_utc, volume, liquidity, clob_token_ids_json, active, resolved FROM markets"
    with db_cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()
        return [dict(row) for row in rows]


def get_market_rule_parse(market_id: str, prompt_version: str) -> Optional[Dict[str, Any]]:
    sql = """
    SELECT market_id, parsed_at_utc, prompt_version, llm_model, strategy_tag, alpha_score,
           rule_score, rule_score_components_json,
           hard_constraints_json, search_keywords_json, parsed_json, raw_response,
           llm_confidence, clarity_score, dispute_risk_score, ambiguity_flags_json
    FROM market_rule_parses
    WHERE market_id=? AND prompt_version=?
    LIMIT 1
    """
    with db_cursor() as cur:
        cur.execute(sql, (market_id, prompt_version))
        row = cur.fetchone()
        return dict(row) if row else None


def get_market_rule_parse_samples(limit: int = 3) -> List[Dict[str, Any]]:
    sql = """
    SELECT market_id, clarity_score, dispute_risk_score, ambiguity_flags_json, parsed_json, rule_score
    FROM market_rule_parses
    ORDER BY parsed_at_utc DESC
    LIMIT ?
    """
    with db_cursor() as cur:
        cur.execute(sql, (limit,))
        rows = cur.fetchall()
        return [dict(r) for r in rows]


def get_latest_price(token_id: str) -> Optional[Dict[str, Any]]:
    sql = "SELECT * FROM prices WHERE token_id=? ORDER BY fetched_at_utc DESC LIMIT 1"
    with db_cursor() as cur:
        cur.execute(sql, (token_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def get_latest_orderbook(token_id: str) -> List[Dict[str, Any]]:
    sql_time = "SELECT fetched_at_utc FROM orderbook_levels WHERE token_id=? ORDER BY fetched_at_utc DESC LIMIT 1"
    with db_cursor() as cur:
        cur.execute(sql_time, (token_id,))
        row = cur.fetchone()
        if not row:
            return []
        ts = row[0]
        cur.execute(
            "SELECT token_id, fetched_at_utc, side, level, price, size FROM orderbook_levels WHERE token_id=? AND fetched_at_utc=?",
            (token_id, ts),
        )
        return [dict(r) for r in cur.fetchall()]


def get_token_orderbook_status(token_ids: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    ids = [tid for tid in token_ids if tid]
    if not ids:
        return {}
    placeholders = ", ".join(["?"] * len(ids))
    sql = f"""
    SELECT token_id, last_enriched_at_utc
    FROM token_orderbook_status
    WHERE token_id IN ({placeholders})
    """
    with db_cursor() as cur:
        cur.execute(sql, ids)
        rows = cur.fetchall()
        return {row["token_id"]: dict(row) for row in rows}


def get_latest_scores(market_ids: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    ids = [mid for mid in market_ids if mid]
    if not ids:
        return {}
    placeholders = ", ".join(["?"] * len(ids))
    sql = f"""
    SELECT s.*
    FROM scores s
    JOIN (
        SELECT market_id, MAX(scored_at_utc) AS max_scored
        FROM scores
        WHERE market_id IN ({placeholders})
        GROUP BY market_id
    ) latest
      ON s.market_id = latest.market_id AND s.scored_at_utc = latest.max_scored
    """
    with db_cursor() as cur:
        cur.execute(sql, ids)
        rows = cur.fetchall()
        return {row["market_id"]: dict(row) for row in rows}


def _get_latest_raw(
    table: str,
    id_col: str,
    ids: Iterable[str],
) -> Dict[str, Dict[str, Any]]:
    id_list = [val for val in ids if val]
    if not id_list:
        return {}
    placeholders = ", ".join(["?"] * len(id_list))
    sql = f"""
    SELECT {id_col}, fetched_at_utc, json
    FROM {table}
    WHERE {id_col} IN ({placeholders})
    ORDER BY fetched_at_utc DESC
    """
    raw_map: Dict[str, Dict[str, Any]] = {}
    with db_cursor() as cur:
        cur.execute(sql, id_list)
        for row in cur.fetchall():
            key = row[id_col]
            if key in raw_map:
                continue
            try:
                payload = json.loads(row["json"]) if row["json"] else None
            except Exception:
                payload = row["json"]
            raw_map[key] = {"fetched_at_utc": row["fetched_at_utc"], "json": payload}
    return raw_map


def get_latest_market_raw(market_ids: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    return _get_latest_raw("markets_raw", "market_id", market_ids)


def get_latest_event_raw(event_ids: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    return _get_latest_raw("events_raw", "event_id", event_ids)
