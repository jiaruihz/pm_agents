from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional

import typer

from agents.connectors.news_rss import GoogleNewsRssClient
from agents.connectors.news_store import NewsStore


app = typer.Typer(add_completion=False)


def _env(name: str, default: str) -> str:
    value = os.getenv(name)
    if value is None:
        return default
    return value


@app.command()
def google_rss(
    query: List[str] = typer.Option(..., "--query", "-q", help="Search queries (repeatable)"),
    when: str = typer.Option("1d", help="Google News RSS when: (e.g. 1h, 1d, 7d)"),
    once: bool = typer.Option(True, help="Run once and exit"),
    interval_sec: int = typer.Option(300, help="Polling interval when once=false"),
    out_dir: str = typer.Option("local_news_cache", help="Local storage directory"),
) -> None:
    """
    Lightweight Google News RSS polling with local de-duplication.

    Examples:
      python scripts/python/news_ingest.py google-rss -q "Tesla SpaceX merger" -q "Elon Musk sources say"
    """

    sqlite_path = os.path.join(out_dir, "news.sqlite")
    jsonl_path = os.path.join(out_dir, "news.jsonl")
    store = NewsStore(sqlite_path=sqlite_path, jsonl_path=jsonl_path)
    client = GoogleNewsRssClient(
        timeout_sec=float(_env("NEWS_RSS_TIMEOUT_SEC", "20")),
        user_agent=_env("NEWS_RSS_USER_AGENT", "pm_agents/0.1"),
    )

    def run_once() -> None:
        total_inserted = 0
        total_skipped = 0
        for q in query:
            items = client.fetch(query=q, when=when)
            payload: List[Dict[str, Any]] = []
            for it in items:
                payload.append(
                    {
                        "id": it.id,
                        "query": it.query,
                        "title": it.title,
                        "url": it.url,
                        "published_at": it.published_at,
                        "description": it.description,
                        "source": it.source,
                        "raw": {
                            "id": it.id,
                            "query": it.query,
                            "title": it.title,
                            "url": it.url,
                            "published_at": it.published_at,
                            "description": it.description,
                            "source": it.source,
                        },
                    }
                )
            res = store.insert_many(payload, source_type="google_news_rss")
            total_inserted += res.inserted
            total_skipped += res.skipped
        print(
            f"[news_ingest] queries={len(query)} inserted={total_inserted} skipped={total_skipped} "
            f"sqlite={sqlite_path} jsonl={jsonl_path}"
        )

    if once:
        run_once()
        return

    while True:
        run_once()
        time.sleep(max(1, interval_sec))


if __name__ == "__main__":
    app()

