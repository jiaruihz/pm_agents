---
name: pmm-market-data-fetcher
description: Fetch a one-shot Polymarket event snapshot for the dormant PMM framework, including event metadata, token ids, and optional orderbooks. Use for legacy PMM setup or manual snapshots; do not use as weather canonical market data, continuous capture, or market-rule research.
---

# PMM Market Data Fetcher

Use this skill only for a legacy PMM event snapshot. Use `polymarket-market-rule-audit`
or `polymarket-research-orchestrator` for market/condition research, and the registered
weather collector for weather production data.

## Inputs

- `market_url` (required): Polymarket event URL, e.g. `https://polymarket.com/event/natural-disaster-in-2026`
- `out` (optional): output JSON path
- `include_orderbook` (optional): fetch orderbook for each token id (default `true`)
- `orderbook_limit` (optional): number of levels to keep per side (default `20`)
- `translate_zh` (optional): add `description_zh` via an explicitly configured provider (default `false`)

## Script

Run:

```bash
.venv/bin/python skills/pmm-market-data-fetcher/scripts/fetch_market_data.py \
  --market-url "https://polymarket.com/event/natural-disaster-in-2026" \
  --include-orderbook true \
  --translate-zh true
```

Translation uses `ALIPAY_API_KEY` with the Alipay host/model variables, or
`OPENAI_API_KEY` only when `OPENAI_MODEL` is also set; an OpenAI key must never be
sent to the Alipay default host.

## Output

The script writes one JSON snapshot containing:

- `event`: title/slug/description/rules-related fields/end time
- `event.description_zh`: 中文完整翻译（可选）
- `markets`: list of markets under the event, each with `market_id`, `question`, `token_ids`, `outcomes`
- `markets[].description_zh`: 中文完整翻译（可选）
- `orderbooks` (optional): per-token top-of-book and level data
- `meta`: source url, generated time

If `out` is omitted, it writes into:

- `runtime/pmm/market_snapshots/market_snapshot_<slug>_<timestamp>.json`

This is a one-shot runtime artifact. If the result changes a durable strategy,
rule, or account thesis, create a governed research record and update the
existing family living document/index instead of promoting the snapshot itself.
