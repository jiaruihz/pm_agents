# Copy Trade Wallet Research Execution Plan

## 1. 策略定位

当前阶段做 `wallet-centric smart money research`，不做重型 entity clustering。

核心流程：

```text
发现钱包地址 -> 地址画像和标签 -> 监控新仓/加仓 -> AI 二次研究市场 -> paper 跟单决策
```

这不是高频套利，也不是无脑复制。钱包动作只作为研究触发器，最终是否跟单取决于市场规则、事实、价格、流动性、钱包主题匹配度和 address/entity 风险。

## 2. 基本前提

必须始终记住：

```text
Address != Entity
```

一个地址可能只是某个真实交易主体的一部分，可能存在小号、对冲腿、试单号、proxy wallet / funder 分离。MVP 不尝试解决完整 entity clustering，但每次 review 和 signal research 都要保留这个风险字段。

处理原则：

- 不因为单地址历史盈利就自动跟单。
- 不因为 leaderboard 排名高就直接进入 watch。
- 如果发现疑似同事件反向腿、同 funder、外部标签或强同步行为，降权或只观察。
- 外部平台证据可以作为人工标签，不作为自动合并 entity 的硬依赖。

## 3. MVP 数据表

在现有 `runtime/db/research.db` 中使用 `copy_trade_` 前缀，避免污染已有 market/event 表。

```text
copy_trade_wallets
  wallet_address
  display_name
  status                  candidate / watch / paper / live / rejected
  tags_json
  traits_json
  first_seen_at
  last_seen_at
  notes
```

```text
copy_trade_wallet_discoveries
  discovery_id
  run_id
  wallet_address
  method                  leaderboard / active_market_holder / active_market_trade / thematic_scan / seed_expand / manual
  source_ref
  evidence_json
  discovered_at
  strength
  tags_json
```

```text
copy_trade_wallet_reviews
  review_id
  wallet_address
  reviewed_at
  score
  verdict                 reject / watch / paper_candidate / live_candidate
  metrics_json
  reasons_json
  summary
```

```text
copy_trade_wallet_signals
  signal_id
  wallet_address
  detected_at
  signal_type             OPEN_BUY / ADD_BUY / REDUCE_SELL / EXIT_SELL
  condition_id
  token_id
  market_slug
  title
  outcome
  delta_size
  delta_notional
  wallet_avg_price
  observed_price
  raw_json
```

```text
copy_trade_signal_research_reports
  report_id
  signal_id
  researched_at
  decision                follow / observe / reject
  confidence
  max_entry_price
  suggested_notional
  address_entity_risk     low / medium / high / unknown
  report_json
  summary
```

## 4. 地址搜索策略

### 4.1 Leaderboard Scan

用途：广覆盖历史盈利账户。

参数：

```text
categories = OVERALL / POLITICS / CRYPTO / ECONOMICS / TECH / SPORTS
periods = ALL / MONTH / WEEK
offset = 0..N step 50
```

规则：

- `SPORTS` 不直接丢弃，标记 `sports_noise`。
- `ALL` 高 PnL 地址必须检查大选污染。
- `MONTH/WEEK` 更适合发现近期活跃地址。
- 多 category / 多 period 重复出现的地址更值得深扫。

### 4.2 Active Market Scan

用途：发现当前正在下注的活跃资金。

流程：

```text
Gamma: active + closed=false + order=volume24hr
Data API: /holders?market=<condition_id>
Data API: /trades?market=<condition_id>
```

重点主题：

- geopolitics: Iran / Ukraine / Russia / China / Taiwan / Israel
- macro: Fed / CPI / inflation / tariff / recession
- crypto: BTC / ETH / SOL / stablecoin
- tech/culture: OpenAI / Nvidia / Tesla / Oscar / Grammy

### 4.3 Thematic Scan

用途：围绕有信息优势可能的主题深挖。

第一批主题：

```text
Iran / Ukraine / Fed / CPI / BTC / ETH / OpenAI / Nvidia / Tesla
```

### 4.4 Seed Expand

用途：从已知好钱包扩展同圈层。

流程：

```text
seed wallet -> open positions / top profitable closed positions -> condition_id -> holders/trades -> neighbor wallets
```

Seed 扩展只产生 candidate，不直接产生 watch。

### 4.5 Manual Intel

用途：人工从 Arkham、Nansen、社区、profile、Twitter 看到地址时录入。

外部平台只是 evidence：

```text
method = manual_external_label
tags = external_label, needs_verification
```

## 5. 地址评审规则

候选地址必须用 `/closed-positions` 全量分页，offset 步进 50。

初始门槛：

| 指标 | 门槛 |
|------|------|
| closed_positions_count | >= 30 |
| non_election_pnl | > 0 |
| profit_factor | > 1.5 |
| sports_ratio | < 50% |
| single_event_dependency | < 70% |
| trade_frequency | low / mid |

直接 reject：

- 净 PnL 为负。
- 非大选 PnL 为负。
- 体育/盘口占比极高。
- 利润来自单一事件。
- 高频买卖比接近 1。
- 当前长期不活跃。

进入 watch：

- 非大选盈利。
- 主题清晰。
- 中低频。
- 方向性强。
- 近期有 open positions。
- 无明显做市/对冲污染。

## 6. Signal Research

钱包动作不直接触发下单，只触发研究。

必须回答：

```text
1. 市场如何结算？
2. 当前价格隐含概率是多少？
3. 公开事实支持哪边？
4. 钱包历史是否擅长该主题？
5. 这笔单是早期建仓还是追涨？
6. 是否有其他 watch 钱包同向？
7. 是否有同事件反向腿或 address/entity 风险？
8. 流动性和价差是否允许跟？
9. 最大可接受入场价？
10. 退出条件？
```

输出：

```text
decision = follow / observe / reject
confidence = 0..1
max_entry_price
suggested_notional
address_entity_risk
exit_plan
```

## 7. Agent 分工

### Agent 1: Wallet Discovery

职责：

- 跑 leaderboard scan。
- 跑 active market scan。
- 跑 thematic scan。
- 跑 seed expand。
- 写入 `copy_trade_wallets` 和 `copy_trade_wallet_discoveries`。
- 生成 candidate shortlist。

不做：

- 不做 live 决策。
- 不做复杂 entity 合并。
- 不做最终跟单判断。

### Agent 2: Wallet Analyst

职责：

- 对 shortlist 做 closed-positions 全量分页。
- 计算 wallet metrics。
- 打 tags 和 traits。
- 写入 `copy_trade_wallet_reviews`。
- 监控 `copy_trade_wallet_signals`。
- 对新信号生成 AI research report。

必须检查：

- `Address != Entity` 风险。
- 单事件污染。
- 大选污染。
- 体育/做市污染。
- 追高风险。
- 规则结算风险。

## 8. P0 落地目标

```text
1. 初始化 copy_trade_* 表
2. 跑一轮 discovery，拿 500-2000 个候选地址
3. 深扫 top 100
4. 选出 10-30 个 watch 钱包
5. 每 60 秒监控 watch 钱包 open positions
6. 新信号触发 AI research report
7. 所有 follow 决策只进 paper
8. 每日汇总 paper PnL、markout、误判原因
```

P0 成功标准：

| 指标 | 标准 |
|------|------|
| watch 钱包数 | 10-30 |
| 每周有效信号 | >= 5 |
| AI 报告覆盖率 | 100% OPEN/ADD 信号 |
| paper 记录完整性 | 每笔有入场理由和退出计划 |
| reject 可解释性 | 每个拒绝都有明确原因 |
| address/entity 风险 | 每个 follow 信号都有风险等级 |

