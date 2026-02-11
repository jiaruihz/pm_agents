---
name: pmm-market-data-fetcher
description: Fetch Polymarket market snapshots from a market/event URL, including event title, rules-related fields, token ids, and optional live orderbooks for each token.
---

# PMM Market Data Fetcher

Use this skill when the user wants to fetch Polymarket market metadata from a URL and save a local snapshot for strategy analysis, backtesting setup, or manual review.

## Inputs

- `market_url` (required): Polymarket event/market URL, e.g. `https://polymarket.com/event/natural-disaster-in-2026`
- `out` (optional): output JSON path
- `include_orderbook` (optional): fetch orderbook for each token id (default `true`)
- `orderbook_limit` (optional): number of levels to keep per side (default `20`)
- `translate_zh` (optional): add `description_zh` via OpenAI-compatible translation (default `true`)

## Script

Run:

```bash
python skills/pmm-market-data-fetcher/scripts/fetch_market_data.py \
  --market-url "https://polymarket.com/event/natural-disaster-in-2026" \
  --include-orderbook true \
  --translate-zh true
```

## Output

The script writes one JSON snapshot containing:

- `event`: title/slug/description/rules-related fields/end time
- `event.description_zh`: 中文完整翻译（可选）
- `markets`: list of markets under the event, each with `market_id`, `question`, `token_ids`, `outcomes`
- `markets[].description_zh`: 中文完整翻译（可选）
- `orderbooks` (optional): per-token top-of-book and level data
- `meta`: source url, generated time

If `out` is omitted, it writes into:

- `local_market_collection/market_snapshot_<slug>_<timestamp>.json`
