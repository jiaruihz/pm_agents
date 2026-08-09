---
name: polymarket-research-orchestrator
description: 对 Polymarket market 或 event 执行独立研究链路：规则审计、评论区、top holders、smart wallets 和关键钱包历史，并生成综合结论。输入支持 URL、slug 或 condition id；不要把产物当成 weather canonical facts 或生产盘口。
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
.venv/bin/python skills/polymarket-research-orchestrator/scripts/run_market_analysis.py \
  --target-market "https://polymarket.com/event/..."
```

## Output

单 market 默认输出到 `runtime/market_analysis/<slug>-<timestamp>/`：

- `summary.json`
- `report.md`
- `rule_analysis.json`
- `comments.json`
- `smart_wallets.json`
- `wallet_audits/...`

event 默认输出到 `runtime/events/<slug>-<timestamp>/`：

- `summary.json`
- `report.md`
- `event_snapshot.json`
- `markets/<market-slug>/...`（逐个子市场的完整研究产物）

## Rules

- 最终结论必须先经过规则风险门控。
- 评论和钱包偏向只是证据层，不是唯一裁判。
- 若规则高歧义，最终结论必须降级。
- `profile_audit_mode=full` 仍只是在公开端点和当前代码上限内尽量分页；综合报告必须保留账户覆盖窗口和缺口。
