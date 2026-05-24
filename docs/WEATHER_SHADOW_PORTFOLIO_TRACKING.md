# Weather Shadow Portfolio Tracking Architecture

> **与相关文档的区别**
> - 本文档：离线模拟多组**信号过滤 + 仓位规则**组合的表现对比（signal 是否入池、用什么 filter），关注哪套规则长期优于 baseline
> - [`WEATHER_LEDGER_POSITION_ANALYSIS.md`](WEATHER_LEDGER_POSITION_ANALYSIS.md)：信号集固定不变，只模拟不同**仓位 sizing 策略**（fixed shares / fixed dollar / price bucket 等）
> - [`WEATHER_CITY_DAY_PORTFOLIO_OPTIMIZER_DESIGN.md`](WEATHER_CITY_DAY_PORTFOLIO_OPTIMIZER_DESIGN.md)：同一城市同一日期的 YES+NO 多腿**组合优化**，关注 bracket 之间的组合效益

## 1. Purpose

Shadow portfolio tracking 用来在不改变原始信号记录和生产 paper ledger 的前提下，同时跟踪多组候选过滤/仓位规则的表现。

核心原则:

```text
raw signals stay complete
shadow portfolios are derived views
production changes require evidence over time
```

这套体系的目标不是立即选出最优策略，而是让后续 2-4 周的数据积累能回答:

- 哪些候选组合稳定优于 baseline。
- 哪些组合只是靠 Warsaw 或少数 top winners。
- 哪些组合降低了 worst day / high-price BUY_NO tail loss。
- 哪些组合牺牲了过多样本，存在过拟合风险。
- 哪些组合适合进入真实 paper execution gating 或 sizing。

## 2. Relationship To Existing Analysis

| Document / Output | Role |
|---|---|
| `t24_paper_ledger_trades.csv` | 实际 paper ledger 结算结果，执行和仓位主口径 |
| `t24_paper_snapshot_replay_trades.csv` | 完整 snapshot replay，策略研究主口径 |
| `docs/WEATHER_LEDGER_POSITION_ANALYSIS.md` | 仓位 sizing 模拟设计 |
| `docs/WEATHER_CITY_DAY_PORTFOLIO_OPTIMIZER_DESIGN.md` | 同城同日 YES/NO 组合优化与复盘设计 |
| `runtime/.../daily_reviews/` | 每日人工复盘记录 |
| This document | 多组 shadow portfolio 的架构和跟踪规范 |

Shadow portfolio 是在已有 trades 上派生出来的 view，不重新生成信号。

## 3. Design Principles

### 3.1 Preserve Baseline

不要因为某个候选组合看起来更好，就停止记录原始信号。

Baseline 必须保留:

- 用于发现新规律。
- 用于衡量过滤组合的机会成本。
- 用于判断 candidate 是否过拟合。
- 用于后续重算新的 portfolio。

### 3.2 Portfolio Definitions Must Be Versioned

每个 portfolio 都要有:

- `portfolio_id`
- `version`
- `description`
- `filter rules`
- `sizing rules`
- `created_at`
- `status`: `active`, `paused`, `deprecated`

规则一旦用于历史比较，不应静默修改。需要调整时创建新版本。

### 3.3 Evaluate On Both Ledger And Snapshot Replay

每个 portfolio 每天都要输出两套结果:

- `ledger`: 真实运行捕获样本。
- `snapshot_replay`: 完整 snapshot 回放样本。

判断规则:

- 两者方向一致: 可信度较高。
- ledger 好、replay 差: 可能是运行样本偶然。
- replay 好、ledger 差: 可能是运行捕获不足或样本差异。
- 两者都差: 淘汰或暂停。

### 3.4 Prefer Simple Portfolios First

当前样本少，优先跟踪少量解释性强的组合。复杂组合可以记录，但不要过早 promote。

## 4. Portfolio Registry

建议维护一个 registry 文件:

```text
runtime/weather_edge_v1/market_data/research/shadow_portfolios/portfolio_registry.json
```

示例结构:

```json
{
  "generated_at": "2026-05-14T00:00:00+08:00",
  "portfolios": [
    {
      "portfolio_id": "baseline_all",
      "version": 1,
      "status": "active",
      "description": "All settled baseline signals with no filter.",
      "filter": {"type": "all"},
      "sizing": {"type": "original_shares"}
    }
  ]
}
```

第一版可以先把 registry 写在脚本常量里，等组合稳定后再文件化。

## 5. Initial Portfolio Set

### 5.1 `baseline_all`

定义:

```text
include all settled trades
sizing = original shares
```

用途:

- 所有组合的比较基准。
- 不允许删除。

### 5.2 `price_clean`

定义:

```text
exclude BUY_YES where entry_price < 0.25
sizing = original shares
```

理由:

- 当前最稳定的负贡献是 `BUY_YES <25c`。
- 简单、解释性强、过拟合风险低。

### 5.3 `mid_price_core`

定义:

```text
0.25 <= entry_price < 0.75
sizing = original shares
```

执行状态:

```text
status = paper_execution_candidate
execution_policy = mid_price_core_v1
effective_from = 2026-05-14
```

理由:

- 同时去掉低价 BUY_YES 噪声和高价 BUY_NO 尾部风险。
- 当前 ledger 和 replay 都显著优于 baseline。
- 2026-05-14 起按人工决策接入 N100 paper trigger runner；这是执行层 gate，不改变 snapshot 全量采集和研究回放口径。

### 5.4 `candidate_conservative`

定义:

```text
exclude BUY_YES where entry_price < 0.25
exclude BUY_YES where 0.30 <= abs_edge < 0.40
exclude BUY_NO where abs_edge >= 0.40
exclude city in {Austin, Beijing, Paris, London}
sizing = original shares
```

理由:

- 当前表现最好的一组保守过滤。
- 但规则较多，过拟合风险高于 `mid_price_core`。

### 5.5 `strong_city_watch`

定义:

```text
city in {Warsaw, LA, Madrid, Shanghai}
sizing = original shares
```

理由:

- 当前强城市组合表现突出。
- 主要用于观察城市 alpha 是否持续，不建议直接 promote。

### 5.6 `weak_city_excluded`

定义:

```text
exclude city in {Austin, Beijing, Paris, London}
sizing = original shares
```

理由:

- 检查弱城市 drag 是否稳定。
- 比 only strong cities 更不激进。

### 5.7 `buy_no_core`

定义:

```text
side == BUY_NO
0.25 <= entry_price < 0.75
sizing = original shares
```

理由:

- 检查 BUY_NO 中价段是否能作为稳定基础仓。

### 5.8 `buy_yes_quality`

定义:

```text
side == BUY_YES
0.25 <= entry_price < 0.75
sizing = original shares
```

理由:

- 验证 BUY_YES 是否只是低价段差，而不是整体不可用。

### 5.9 `city_tier_sizing`

定义:

```text
Strong: Warsaw, LA, Madrid => 1.0x
Watch-positive: Shanghai => 0.75x
Neutral: NYC, Chicago, Tokyo, Miami => 0.5x
Weak: Austin, Beijing, Paris, London, Seoul => 0.0x or 0.25x
```

初版建议:

```text
sizing = original shares * multiplier
```

理由:

- 比 hard filter 更平滑。
- 更适合后续接入仓位分析模块。

## 6. Daily Calculation Flow

每天 settlement 同步后执行:

```text
1. load ledger trades
2. load snapshot replay trades
3. keep settled rows only for performance metrics
4. for each portfolio:
     apply filter
     apply sizing, if any
     recompute pnl/cost/roi
     summarize daily and cumulative metrics
5. write JSON/CSV/Markdown outputs
6. append or create daily review
```

注意:

- 不要修改原始 trades 文件。
- 所有 shadow PnL 都是派生计算。
- 如果 sizing 不是 original shares，必须在输出中明确标注 `simulated`.

## 7. Metrics

### 7.1 Core Metrics

每个 portfolio 每个 source 输出:

- `n`
- `wins`
- `win_rate`
- `cost_usd`
- `pnl_usd`
- `roi`
- `gross_win`
- `gross_loss`
- `profit_factor`

### 7.2 Risk Metrics

- daily PnL
- cumulative PnL
- worst day
- max drawdown
- largest single-trade loss
- largest city-day loss
- exposure by city
- exposure by side
- exposure by price bucket

### 7.3 Robustness Metrics

- PnL excluding Warsaw
- PnL excluding top winner
- PnL excluding top 5 winners
- PnL excluding worst day
- top winner contribution ratio
- trade count retained vs baseline

这些指标用于防止组合看起来好，但其实只靠少数样本。

## 8. Output Layout

建议目录:

```text
runtime/weather_edge_v1/market_data/research/shadow_portfolios/
```

文件:

```text
portfolio_registry.json
shadow_portfolio_summary.json
shadow_portfolio_summary.csv
shadow_portfolio_by_day.csv
shadow_portfolio_by_city.csv
shadow_portfolio_by_side.csv
shadow_portfolio_by_price_bucket.csv
shadow_portfolio_robustness.csv
shadow_portfolio_report_YYYY-MM-DD.md
```

每日复盘继续放:

```text
runtime/weather_edge_v1/market_data/research/daily_reviews/YYYY-MM-DD_review.md
```

Daily review 应引用 shadow portfolio summary，而不是手工重算所有表。

## 9. Suggested CLI

第一版脚本可以很简单:

```bash
python3 scripts/ops/weather_shadow_portfolio_tracking.py \
  --ledger runtime/weather_edge_v1/market_data/research/t24_paper_ledger_trades.csv \
  --snapshot runtime/weather_edge_v1/market_data/research/t24_paper_snapshot_replay_trades.csv \
  --out-dir runtime/weather_edge_v1/market_data/research/shadow_portfolios
```

后续再加:

```bash
--registry runtime/weather_edge_v1/market_data/research/shadow_portfolios/portfolio_registry.json
--as-of 2026-05-14
--min-date 2026-05-06
--max-date 2026-05-14
```

## 10. Promotion Framework

Portfolio 状态:

```text
research_only
shadow_active
candidate
paper_execution_candidate
production_candidate
deprecated
```

### 10.1 Promote To `candidate`

至少满足:

- 连续 5 个结算日表现不明显劣于 baseline。
- ledger 和 replay 方向不冲突。
- retained trades 不低于 baseline 的 25%，除非是专门观察组合。

### 10.2 Promote To `paper_execution_candidate`

至少满足:

- 500+ settled trades 或 15-20 个结算日。
- ledger 和 replay 都优于 baseline。
- worst day 不差于 baseline。
- 不依赖单一城市。
- 去掉 top 5 winners 后仍不明显劣化。

### 10.3 Promote To Production

需要额外人工 review:

- 是否改变实际下单风险。
- 是否影响数据采集完整性。
- 是否保留 filtered-out research logs。
- 是否有 rollback 方案。

当前阶段默认不要 promote 到 production。例外情况必须在 daily review 中显式记录人工 override、effective date、rollback 位置和后续验证指标。

### 10.4 Current Execution Override

2026-05-14 起，`mid_price_core_v1` 被接入 N100 `paper_trigger_runner.py`:

```text
0.25 <= entry_price < 0.75
execution_policy = mid_price_core_v1
```

这次 override 的边界:

- 只影响新增 paper/live ledger orders。
- 不过滤 `output/paper_snapshots/` 的原始 snapshot。
- 不改变 snapshot replay 分析，后续仍可用全量 snapshot 重算 baseline 和所有 shadow portfolios。
- 新增订单应带 `execution_policy` / `entry_price_min` / `entry_price_max` / `entry_price_window` 字段，方便后续分支归因。
- 回滚点: N100 `/home/jiarui/projects/weather-predict/scripts/analysis/paper_trigger_runner.py.bak_mid_price_20260514`。

后续复盘重点:

- `mid_price_core_v1` vs `baseline_all` 的新增样本表现。
- filtered-out rows 的机会成本，尤其 `<25c` 后续是否偶发大赢。
- 是否因为 25-75 gate 过度降低交易数。
- 是否仍然依赖 Warsaw / LA / Madrid 少数城市贡献。

## 11. Handling Overfit

每个 shadow portfolio 都必须展示:

```text
sample_days
settled_trades
retention_rate_vs_baseline
top_city_pnl_share
top_5_winner_pnl_share
ex_warsaw_pnl
ex_top_winner_pnl
```

如果出现以下情况，应标记为 overfit risk:

- settled trades < 50
- active days < 5
- top city contributes > 70% of PnL
- top 5 winners contribute > 100% of net PnL
- only works in ledger or only works in replay

## 12. First Implementation Recommendation

V1 不要做复杂配置系统。建议:

1. 在脚本中硬编码初始 portfolio set。
2. 只支持 original shares 和简单 multiplier sizing。
3. 输出 JSON/CSV/Markdown。
4. 每次 settlement 后手动或自动运行。
5. 每日复盘引用输出结果。

先把数据链路跑稳定，再考虑 registry 文件、参数化和 dashboard UI。

## 13. Open Questions

- `city_tier_sizing` 中 weak city 是 0x 还是 0.25x。
- `mid_price_core` 是否应包含 `75c` 边界。
- `BUY_YES <25c` 是 hard exclude，还是 0.25x lottery sizing。
- 是否要按 city-day cap 限制同城多 bucket 相关风险。
- 是否把 T2/research-only 城市纳入 replay-only shadow portfolios。

这些问题不应在当前样本下定死，应通过 shadow tracking 累积证据。
