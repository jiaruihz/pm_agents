# Polymarket Research Capabilities

## 1. 市场规则审计

### 用途

分析一个市场的：

- question
- description
- rules
- 结算条件
- 规则清晰度
- 歧义与争议风险

### 适用场景

- 想先判断这个盘规则是否值得做
- 怀疑某个市场存在模糊结算口径
- 想快速生成规则摘要

### 输入

- 市场 URL
- slug
- condition id

### 输出

- `summary.json`
- `report.md`
- `market_snapshot.json`

### 命令

```bash
python scripts/ops/polymarket_market_rule_audit.py \
  --target-market "https://polymarket.com/event/..."
```

## 2. 账户历史审计

### 用途

分析一个用户/钱包的：

- 历史 trades
- 当前 positions
- closed positions
- PnL 轨迹
- 胜率与回撤

### 适用场景

- 验证某个“神号”是不是历史全对
- 想知道一个地址是否有稳定优势
- 想拉原始数据交给 LLM 二次总结

### 输入

- `@username`
- profile URL
- wallet address

### 输出

- `summary.json`
- `report.md`
- `raw/`

### 命令

```bash
python scripts/ops/polymarket_profile_audit.py --target @cqk
```

## 3. 市场情报

### 用途

分析一个市场的：

- 评论区高价值信息
- top holders
- smart wallets
- 关键钱包历史质量

### 适用场景

- 想先看参与者在干什么
- 想用“聪明钱”过滤评论噪音
- 想快速知道市场参与结构

### 输入

- 市场 URL
- slug
- condition id

### 输出

- `summary.json`
- `report.md`
- `comments.json`
- `smart_wallets.json`
- `wallet_audits/...`

### 命令

```bash
python scripts/ops/polymarket_market_intel.py \
  --target-market "https://polymarket.com/event/..."
```

## 4. 完整市场分析

### 用途

把两层信息合在一起：

- 规则风险
- 评论证据
- holders / smart wallets
- 关键钱包历史

### 适用场景

- 想要完整研究报告
- 不希望只看评论区或只看持仓
- 需要一个先经过规则门控的综合结论

### 输入

- 市场 URL
- slug
- condition id

### 输出

- `summary.json`
- `report.md`
- `rule_analysis.json`
- `comments.json`
- `smart_wallets.json`
- `wallet_audits/...`

### 命令

```bash
python scripts/ops/rule_lawyer_market_analysis.py \
  --target-market "https://polymarket.com/event/..."
```

## 结论标签

当前完整分析使用这些标签：

- `leans_yes`
- `leans_no`
- `mixed`
- `insufficient_edge`
- `comments_unavailable`
- `high_rule_risk`

说明：

- `comments_unavailable` 表示评论层缺失，分析已降级
- `high_rule_risk` 表示规则层已经把结果打回

## 主要限制

- 评论接口属于 best-effort，不保证所有市场都能抓到
- 账户历史分析只基于公开数据
- 高胜率不等于内幕交易
- 规则分析在未启用 LLM 时是启发式摘要，不替代人工审阅
