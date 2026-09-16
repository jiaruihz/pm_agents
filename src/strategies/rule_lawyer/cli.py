import asyncio
import json
import logging
from pathlib import Path
from typing import Optional

import typer
from rich.logging import RichHandler

from .config import get_settings
from .db import init_db
from .pipeline import (
    compute_scores_and_candidates,
    get_market_details,
    run_single_market_flow,
    run_enrich,
    run_llm_parse,
    run_sync_gamma,
)
from .nodes.executor import PipelineExecutor
from .nodes.filter import FilterNode
from .nodes.constants import (
    DEFAULT_LIQUIDITY_THRESHOLD,
    DEFAULT_MIN_DESCRIPTION_LEN,
    DEFAULT_MIN_QUESTION_LEN,
    DEFAULT_MIN_RULES_LEN,
    DEFAULT_PRICE_MAX,
    DEFAULT_PRICE_MIN,
)
from src.platform.notification.telegram import send_telegram_message


app = typer.Typer(help="Polymarket research-only coarse screening CLI")


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(rich_tracebacks=True, markup=True)],
    )


@app.callback()
def main(log_level: Optional[str] = typer.Option("INFO", help="Logging level")) -> None:
    setup_logging(log_level)


@app.command("init-db")
def init_db_cmd() -> None:
    """Initialize SQLite database and tables."""
    init_db()
    typer.echo("Database initialized or already present.")


@app.command()
def sync(
    active: bool = typer.Option(True, help="Filter active markets"),
    closed: Optional[bool] = typer.Option(None, help="Filter closed markets"),
    pages: int = typer.Option(5, help="Number of pages to fetch"),
    page_size: int = typer.Option(100, help="Page size for pagination"),
    resume: bool = typer.Option(True, help="Resume pagination from last offset"),
):
    """Fetch Gamma markets/events, store raw + canonical."""
    res = run_sync_gamma(active=active, pages=pages, page_size=page_size, closed=closed, resume=resume)
    msg = f"Synced markets: raw={res['raw_markets']} events={res['raw_events']} canonical={res['canonical']}"
    if "skipped_expired" in res:
        msg += (
            f" | seen={res.get('seen_markets', 0)} kept={res.get('kept_markets', 0)} "
            f"skipped_expired={res.get('skipped_expired', 0)}"
        )
    if "markets_pages" in res:
        msg += (
            f" | pages(markets/events)={res.get('markets_pages', 0)}/{res.get('events_pages', 0)} "
            f"end_reached(markets/events)={res.get('markets_end_reached')}/{res.get('events_end_reached')}"
        )
    typer.echo(msg)


@app.command()
def enrich(
    prices: bool = typer.Option(True, help="Fetch prices/mid"),
    orderbooks: bool = typer.Option(True, help="Fetch orderbooks"),
    limit: int = typer.Option(500, help="Max token_ids to fetch"),
    top_n: int = typer.Option(None, help="Top N levels to store (default from config)"),
    archive: bool = typer.Option(False, help="Archive full orderbooks to gzip files"),
    two_stage: bool = typer.Option(False, help="Fetch prices first, then orderbooks for qualified tokens"),
    orderbook_min_mid: float = typer.Option(DEFAULT_PRICE_MIN, help="Min mid to fetch orderbooks"),
    orderbook_max_mid: float = typer.Option(DEFAULT_PRICE_MAX, help="Max mid to fetch orderbooks"),
    market_statuses: Optional[str] = typer.Option(None, help="Comma-separated market statuses to enrich"),
):
    """Fetch CLOB prices/orderbooks for recent active markets."""
    settings = get_settings()
    effective_top_n = top_n or settings.orderbook_top_n
    status_list = [s.strip() for s in market_statuses.split(",") if s.strip()] if market_statuses else None
    res = run_enrich(
        limit=limit,
        top_n=effective_top_n,
        archive_books=archive,
        fetch_prices=prices,
        fetch_books=orderbooks,
        two_stage=two_stage,
        orderbook_min_mid=orderbook_min_mid if two_stage else None,
        orderbook_max_mid=orderbook_max_mid if two_stage else None,
        market_statuses=status_list,
    )
    msg = f"Enriched tokens={res['tokens']} saved prices={res['prices']} levels={res['levels']} archives={res['archives']}"
    if "book_tokens" in res:
        msg += f" book_tokens={res['book_tokens']}"
    if "price_source" in res:
        msg += f" price_source={res['price_source']}"
    typer.echo(msg)
    try:
        from .pipeline import preview_token_metrics

        previews = preview_token_metrics(sample_size=3)
        if previews:
            typer.echo("Sample tokens (bid/ask/mid/spread/depth):")
            for p in previews:
                typer.echo(
                    f"{p['token_id']}: mid={p.get('mid')} bid={p.get('best_bid')} ask={p.get('best_ask')} spread={p.get('spread')} depth1% bid/ask={p.get('depth_1pct_bid')}/{p.get('depth_1pct_ask')}"
                )
    except Exception:
        typer.echo("Preview unavailable.")


@app.command()
def parse(
    llm: bool = typer.Option(True, help="Enable LLM extractor"),
    batch: int = typer.Option(100, help="Max markets to parse"),
    ready_only: bool = typer.Option(False, help="Only parse READY_TO_PARSE (state machine)"),
    allow_reparse: bool = typer.Option(False, help="Reparse if already parsed (non-state machine)"),
    concurrency: int = typer.Option(3, help="Concurrent LLM requests (READY_TO_PARSE only)"),
    progress: bool = typer.Option(True, help="Show progress bar (READY_TO_PARSE only)"),
):
    """Run LLM extractor for unparsed markets."""
    if not llm:
        typer.echo("LLM disabled; nothing to do")
        raise typer.Exit(code=0)
    if ready_only:
        from .nodes.parser import ParserNode

        init_db()
        res = asyncio.run(ParserNode(batch=batch, concurrency=concurrency, show_progress=progress).execute())
    else:
        res = run_llm_parse(batch=batch, allow_reparse=allow_reparse)
    if "error" in res:
        typer.echo(f"LLM parse skipped: {res['error']}")
    else:
        msg = f"Parsed {res['parsed']} markets"
        if "skipped_existing" in res:
            msg += f" (skipped_existing={res.get('skipped_existing')})"
        if "failed" in res:
            msg += f" failed={res.get('failed')}"
        typer.echo(msg)
        try:
            from .pipeline import preview_market_rule_parses

            previews = preview_market_rule_parses(sample_size=3)
            if previews:
                typer.echo("Sample parses (trigger_type, clarity_score, rule_score, ambiguity_flags):")
                for p in previews:
                    typer.echo(
                        f"{p['market_id']}: trigger={p.get('trigger_type')} clarity={p.get('clarity_score')} rule={p.get('rule_score')} ambiguity={p.get('ambiguity_flags')}"
                    )
        except Exception:
            typer.echo("Preview unavailable.")


@app.command()
def candidates(
    min_volume: float = typer.Option(20000, help="Minimum volume filter"),
    max_spread: float = typer.Option(0.06, help="Maximum spread filter"),
    clarity_min: float = typer.Option(0.7, help="Minimum clarity score"),
    dispute_max: float = typer.Option(0.4, help="Maximum dispute risk score"),
    output: str = typer.Option("output/candidates.csv", help="Output CSV path"),
    markdown: bool = typer.Option(False, help="Also emit markdown preview"),
):
    """Filter candidates and export."""
    res = compute_scores_and_candidates(min_volume, max_spread, clarity_min, dispute_max)
    candidates = res.get("candidates", [])
    import pandas as pd
    from pathlib import Path

    Path(output).parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(candidates)
    if not df.empty:
        df.to_csv(output, index=False)
    typer.echo(f"Scores saved: {res.get('scores_saved')} candidates: {len(candidates)} written to {output}")
    if not df.empty and markdown:
        md_path = Path(output).with_suffix(".md")
        md_path.write_text(df.head(20).to_markdown(index=False))
        typer.echo(f"Markdown preview: {md_path}")
    if not df.empty:
        typer.echo(df.head(20).to_string(index=False))


@app.command("notify-telegram")
def notify_telegram(
    text: str = typer.Argument(..., help="Telegram message content"),
    chat_id: Optional[str] = typer.Option(None, help="Override TELEGRAM_CHAT_ID"),
    parse_mode: Optional[str] = typer.Option(None, help="Telegram parse mode: MarkdownV2/HTML/Markdown"),
    silent: bool = typer.Option(False, help="Send silently without push notification"),
):
    """Send a Telegram bot message."""
    res = asyncio.run(
        send_telegram_message(
            text=text,
            chat_id=chat_id,
            parse_mode=parse_mode,
            disable_notification=silent,
        )
    )
    msg = res.get("result", {}) if isinstance(res, dict) else {}
    typer.echo(f"Telegram sent: chat_id={msg.get('chat', {}).get('id')} message_id={msg.get('message_id')}")


@app.command()
def filter(
    limit: int = typer.Option(1000, help="Max markets to scan"),
    liquidity_threshold: float = typer.Option(DEFAULT_LIQUIDITY_THRESHOLD, help="Min liquidity"),
    price_min: float = typer.Option(DEFAULT_PRICE_MIN, help="Min price filter"),
    price_max: float = typer.Option(DEFAULT_PRICE_MAX, help="Max price filter"),
    min_question_len: int = typer.Option(DEFAULT_MIN_QUESTION_LEN, help="Minimum question length"),
    min_rules_len: int = typer.Option(DEFAULT_MIN_RULES_LEN, help="Minimum rules length"),
    min_description_len: int = typer.Option(DEFAULT_MIN_DESCRIPTION_LEN, help="Minimum description length"),
    ignore_categories: Optional[str] = typer.Option(None, help="Comma-separated categories to ignore"),
    require_best_bid_ask: bool = typer.Option(False, help="Require bestBid/bestAsk from orderbook prices"),
    metadata_only: bool = typer.Option(True, help="Filter with market metadata only"),
    all_markets: bool = typer.Option(False, help="Scan all markets (ignore status filter)"),
):
    """Coarse filter markets and mark READY_TO_PARSE."""
    init_db()
    categories = None
    if ignore_categories:
        categories = [c.strip() for c in ignore_categories.split(",") if c.strip()]
    effective_limit = None if all_markets else limit
    res = FilterNode(
        limit=effective_limit,
        liquidity_threshold=liquidity_threshold,
        price_min=price_min,
        price_max=price_max,
        min_question_len=min_question_len,
        min_rules_len=min_rules_len,
        min_description_len=min_description_len,
        ignore_categories=categories,
        require_best_bid_ask=require_best_bid_ask,
        metadata_only=metadata_only,
        scan_all=all_markets,
    ).execute()
    typer.echo(
        f"Filter scanned={res['scanned']} ignored={res['ignored']} expired={res.get('expired', 0)} ready_to_parse={res['ready_to_parse']}"
    )
    if res.get("ignored_reasons"):
        typer.echo(f"Ignored reasons: {res['ignored_reasons']}")


@app.command("pap-run")
def pap_run(
    pages: int = typer.Option(5, help="Gamma pages to fetch"),
    page_size: int = typer.Option(100, help="Gamma page size"),
    closed: Optional[bool] = typer.Option(None, help="Filter closed markets"),
    resume: bool = typer.Option(True, help="Resume pagination from sync_state"),
    enrich_limit: int = typer.Option(500, help="Max token_ids to enrich"),
    enrich_top_n: int = typer.Option(None, help="Orderbook top-N levels to store"),
    enrich_two_stage: bool = typer.Option(True, help="Fetch prices first, then orderbooks"),
    enrich_price_min: float = typer.Option(DEFAULT_PRICE_MIN, help="Min mid price for orderbooks"),
    enrich_price_max: float = typer.Option(DEFAULT_PRICE_MAX, help="Max mid price for orderbooks"),
    enrich_prices: bool = typer.Option(True, help="Fetch prices"),
    enrich_orderbooks: bool = typer.Option(True, help="Fetch orderbooks"),
    require_best_bid_ask: bool = typer.Option(True, help="Require bestBid/bestAsk in filter"),
    metadata_only_filter: bool = typer.Option(True, help="Filter using market metadata only"),
    parse_batch: int = typer.Option(100, help="Max markets to parse"),
    investigate_limit: int = typer.Option(100, help="Max markets to investigate"),
    edge_threshold: float = typer.Option(0.15, help="Edge threshold"),
    output: str = typer.Option("output/pap_candidates.md", help="Output markdown path"),
):
    """Run PAP state-machine pipeline (ingest -> filter -> parse -> investigate -> score)."""
    init_db()
    res = PipelineExecutor(
        pages=pages,
        page_size=page_size,
        closed=closed,
        resume=resume,
        enrich_limit=enrich_limit,
        enrich_top_n=enrich_top_n or get_settings().orderbook_top_n,
        enrich_two_stage=enrich_two_stage,
        enrich_price_min=enrich_price_min,
        enrich_price_max=enrich_price_max,
        enrich_prices=enrich_prices,
        enrich_orderbooks=enrich_orderbooks,
        require_best_bid_ask=require_best_bid_ask,
        metadata_only_filter=metadata_only_filter,
        parse_batch=parse_batch,
        investigate_limit=investigate_limit,
        edge_threshold=edge_threshold,
        output_path=output,
    ).run()
    typer.echo(f"PAP run complete: {res.get('output_path')}")


@app.command("market-detail")
def market_detail(
    market_ids: str = typer.Option(..., help="Comma-separated market IDs"),
    output: Optional[str] = typer.Option(None, help="Output JSON path"),
    include_analysis: bool = typer.Option(True, help="Include latest market_rule_parses"),
    include_evidence: bool = typer.Option(True, help="Include evidence_locker"),
    include_orderbooks: bool = typer.Option(True, help="Include orderbook levels"),
):
    """Fetch market details with latest price/orderbook per token."""
    init_db()
    ids = [mid.strip() for mid in market_ids.split(",") if mid.strip()]
    details = get_market_details(
        ids,
        include_analysis=include_analysis,
        include_evidence=include_evidence,
        include_orderbooks=include_orderbooks,
    )
    payload = {"count": len(details), "items": details}
    if output:
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_text(json.dumps(payload, indent=2))
        typer.echo(f"Wrote {len(details)} markets to {output}")
    else:
        typer.echo(json.dumps(payload, indent=2))


@app.command("market-run")
def market_run(
    market_id: Optional[str] = typer.Option(None, help="Market ID"),
    market_url: Optional[str] = typer.Option(None, help="Polymarket market URL"),
    market_input: Optional[str] = typer.Option(None, help="Market ID or URL"),
    top_n: Optional[int] = typer.Option(None, help="Orderbook top-N levels"),
    build_prompt: bool = typer.Option(True, help="Generate final prompt"),
):
    """Run single-market full flow (sync -> enrich -> filter -> parse -> prompt)."""
    res = run_single_market_flow(
        market_id=market_id,
        market_url=market_url,
        market_input=market_input,
        top_n=top_n,
        build_prompt=build_prompt,
    )
    typer.echo(json.dumps(res, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    app()
