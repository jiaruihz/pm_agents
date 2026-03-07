---
name: polymarket-market-intel
description: 分析一个 Polymarket 市场的评论区、top holders、smart wallets 和关键钱包历史表现，输入支持市场 URL、slug 或 condition id。
---

# Polymarket Market Intel

适用于这些请求：

- “去评论区找有价值的信息”
- “看看这个 market 的 top holder 和聪明钱在做什么”
- “把评论和钱包历史结合起来给我一个偏向判断”

## Inputs

- `target_market`（必填）
  - 市场 URL / slug / condition id
- `comment_limit`（可选）
- `comment_mode`（可选）
  - `top_only | top_and_newest`
- `holders_depth`（可选）
- `top_wallets`（可选）
- `wallet_score_mode`（可选）
  - `pnl_proxy | resolved_trades`
- `profile_audit_mode`（可选）
  - `normal | full`
- `out_dir`（可选）

## Run

```bash
python3 skills/polymarket-market-intel/scripts/run_market_intel.py \
  --target-market "https://polymarket.com/event/..."
```

## Output

默认输出到 `runtime/market_intel/<slug>-<timestamp>/`：

- `summary.json`
- `report.md`
- `comments.json`
- `smart_wallets.json`
- `wallet_audits/...`

## Rules

- 评论抓取失败时自动降级到 holder/wallet 视角。
- 评论情绪不能代替规则分析。
- 关键钱包历史质量要和当前市场偏向分开描述。
