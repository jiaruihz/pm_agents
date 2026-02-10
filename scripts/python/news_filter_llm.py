from __future__ import annotations

import json
import os
import sqlite3
import time
from typing import Any, Dict, List, Optional

import typer

from agents.application.news_filter_llm import PROMPT_VERSION, NewsLlmFilter
from agents.connectors.news_decision_store import NewsDecisionStore


app = typer.Typer(add_completion=False)


def _connect(sqlite_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(sqlite_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


def _safe_str(v: Any) -> str:
    return str(v or "").strip()


def _load_items(
    sqlite_path: str,
    limit: int,
    since_ts: Optional[int],
) -> List[Dict[str, Any]]:
    with _connect(sqlite_path) as conn:
        if since_ts is None:
            cur = conn.execute(
                """
                SELECT id, source, source_type, query, title, url, published_at, description, fetched_at
                FROM news_items
                ORDER BY fetched_at DESC
                LIMIT ?
                """,
                (limit,),
            )
        else:
            cur = conn.execute(
                """
                SELECT id, source, source_type, query, title, url, published_at, description, fetched_at
                FROM news_items
                WHERE fetched_at >= ?
                ORDER BY fetched_at DESC
                LIMIT ?
                """,
                (since_ts, limit),
            )
        rows = cur.fetchall()

    out: List[Dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "id": _safe_str(r[0]),
                "source": _safe_str(r[1]),
                "source_type": _safe_str(r[2]),
                "query": _safe_str(r[3]),
                "title": _safe_str(r[4]),
                "url": _safe_str(r[5]),
                "published_at": _safe_str(r[6]) or None,
                "description": _safe_str(r[7]),
                "fetched_at": int(r[8] or 0),
            }
        )
    return out


@app.command()
def run(
    market_key: str = typer.Option(..., help="A stable key for this market/topic (used to partition decisions)"),
    market_question: str = typer.Option(..., help="Market question (the thing you're trading)"),
    resolution_criteria: str = typer.Option(
        "...",
        help="Short resolution criteria. If unknown, keep it generic; be conservative.",
    ),
    out_dir: str = typer.Option("local_news_cache", help="Directory containing news.sqlite"),
    limit: int = typer.Option(50, help="How many recent items to consider"),
    since_sec: int = typer.Option(0, help="Only consider items fetched within last N seconds (0 disables)"),
    model: str = typer.Option("", help="Override NEWS_LLM_MODEL"),
    sleep_ms: int = typer.Option(250, help="Sleep between LLM calls (simple rate limiting)"),
    dry_run: bool = typer.Option(False, help="Don't call LLM; just show how many items would be processed"),
) -> None:
    """
    LLM filter node: reads local_news_cache/news.sqlite and writes decisions to news_decisions.

    Example (OpenAI):
      NEWS_LLM_API_KEY=... ./venv/bin/python -m scripts.python.news_filter_llm \
        --market-key tsla_spacex_merge \
        --market-question "Will Tesla and SpaceX merge by June 30, 2026?" \
        --resolution-criteria "A merger is announced by either company or confirmed by SEC filing."

    Example (OpenAI-compatible):
      NEWS_LLM_BASE_URL=https://antchat.alipay.com \
      NEWS_LLM_API_KEY=... \
      NEWS_LLM_MODEL=Qwen3-... \
      ./venv/bin/python -m scripts.python.news_filter_llm ...
    """

    sqlite_path = os.path.join(out_dir, "news.sqlite")
    decisions_jsonl = os.path.join(out_dir, "news_decisions.jsonl")
    store = NewsDecisionStore(sqlite_path=sqlite_path, jsonl_path=decisions_jsonl)

    since_ts: Optional[int] = None
    if since_sec and since_sec > 0:
        since_ts = int(time.time()) - int(since_sec)

    items = _load_items(sqlite_path=sqlite_path, limit=limit, since_ts=since_ts)
    if not items:
        print("[news_filter_llm] no items found")
        return

    effective_model = model.strip() or os.getenv("NEWS_LLM_MODEL") or "gpt-4o-mini"

    # Skip items already decided for this (market_key, model, prompt_version).
    pending: List[Dict[str, Any]] = []
    for it in items:
        if not store.has_decision(it["id"], market_key, effective_model, PROMPT_VERSION):
            pending.append(it)

    if dry_run:
        print(
            f"[news_filter_llm] dry_run=1 market_key={market_key} model={effective_model} "
            f"items={len(items)} pending={len(pending)} sqlite={sqlite_path}"
        )
        return

    llm = NewsLlmFilter(model=effective_model)
    decisions: List[Dict[str, Any]] = []

    for idx, it in enumerate(pending):
        decision = llm.decide(
            item_id=it["id"],
            market_question=market_question,
            resolution_criteria=resolution_criteria,
            title=it["title"],
            description=it["description"],
            source=it["source"] or it["source_type"],
            published_at=it["published_at"],
            query=it["query"] or None,
        )
        decisions.append(decision.to_dict())
        if sleep_ms > 0 and idx + 1 < len(pending):
            time.sleep(max(0.0, sleep_ms / 1000.0))

    res = store.insert_many(
        [{"item_id": d["item_id"], **d} for d in decisions],
        market_key=market_key,
        model=effective_model,
        prompt_version=PROMPT_VERSION,
    )
    print(
        f"[news_filter_llm] market_key={market_key} model={effective_model} "
        f"processed={len(pending)} inserted={res.inserted} skipped={res.skipped} "
        f"sqlite={sqlite_path} jsonl={decisions_jsonl}"
    )


if __name__ == "__main__":
    app()
