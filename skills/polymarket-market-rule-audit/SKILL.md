---
name: polymarket-market-rule-audit
description: 审计一个 Polymarket 市场的基础信息、规则、结算条件和歧义风险，输入支持市场 URL、slug 或 condition id。
---

# Polymarket Market Rule Audit

适用于这些请求：

- “这个市场规则怎么结算？”
- “这个盘有没有歧义风险？”
- “把这个 market 的规则和结算条件总结一下”

## Inputs

- `target_market`（必填）
  - `https://polymarket.com/event/...`
  - market slug
  - `0x...` condition id
- `out_dir`（可选）
  - 输出目录

## Run

```bash
.venv/bin/python skills/polymarket-market-rule-audit/scripts/run_market_rule_audit.py \
  --target-market "https://polymarket.com/event/..."
```

## Output

默认输出到 `runtime/market_rule_audits/<slug>-<timestamp>/`：

- `summary.json`
- `report.md`
- `market_snapshot.json`

## Rules

- 先给规则清晰度和争议风险，再给摘要。
- 不能只依赖评论或钱包行为判断规则风险。
- 如果规则文本不充分，必须在报告里明确写出局限性。
