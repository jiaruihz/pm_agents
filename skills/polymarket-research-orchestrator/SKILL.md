---
name: polymarket-research-orchestrator
description: 对一个 Polymarket 市场执行完整研究链路：规则审计、评论区情报、top holders、smart wallets、关键钱包历史审计，并生成综合结论。输入支持市场 URL、slug 或 condition id。
---

# Polymarket Research Orchestrator

适用于这些请求：

- “完整分析这个市场”
- “把规则风险、评论、holder 和聪明钱综合一下”
- “给我一个完整的研究报告，不只是评论区”

## Inputs

- `target_market`（必填）
  - 市场 URL / slug / condition id
- `comment_limit`（可选）
- `comment_mode`（可选）
- `holders_depth`（可选）
- `top_wallets`（可选）
- `wallet_score_mode`（可选）
- `profile_audit_mode`（可选）
- `out_dir`（可选）

## Run

```bash
python3 skills/polymarket-research-orchestrator/scripts/run_market_analysis.py \
  --target-market "https://polymarket.com/event/..."
```

## Output

默认输出到 `runtime/market_analysis/<slug>-<timestamp>/`：

- `summary.json`
- `report.md`
- `rule_analysis.json`
- `comments.json`
- `smart_wallets.json`
- `wallet_audits/...`

## Rules

- 最终结论必须先经过规则风险门控。
- 评论和钱包偏向只是证据层，不是唯一裁判。
- 若规则高歧义，最终结论必须降级。
