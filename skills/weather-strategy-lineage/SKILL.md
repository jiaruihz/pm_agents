---
name: weather-strategy-lineage
description: 追踪 weather 的 EventEnvelope→DecisionContext→ModelOutput→SignalCandidate→TradeIntent→plan→order→fill→settlement 完整血缘，复盘单日、单城、单 market、单 order 或异常执行。用于为什么下这单、为什么没下/没成交、逐笔复盘、duplicate/overbuy/share-cap、exchange rejection。先读精确 raw，再用 fact_trades 补 fill/fee/PnL；禁止为单笔问题全量重建或重写 PnL。
---

# Weather strategy lineage

## 先锁问题 grain

优先使用最窄键：`order_id` / `execution_id` / `fill_id`；其次是 `(strategy instance, city, target_date, condition_id)`。明确用户问的是目标日、下单日还是成交日。

## 证据顺序

1. 当前 Mac runtime 的 event/checkpoint/decision bundle/candidate/intent/handoff 与
   `plans/orders/fills` 原始记录。
2. authenticated exchange response / order state / transaction hash。
3. canonical `fact_signal_candidates → plans → orders → fills`，以及 WCIR compatibility bridge。
4. `fact_trades` 的 settlement、fee、cost、PnL、valuation。
5. N100 镜像只用于历史期。

单笔或单日 raw lineage 不需要 sync/rebuild。只有 settlement/PnL 的 canonical 窗口确实缺失时才转 `weather-fact-rebuild`。

需要 canonical 补证前先运行 `.venv/bin/python scripts/ops/weather_production_manifest.py --strict`。若 DB split/consumer route 为 critical，raw lineage 仍可继续，但必须停止 canonical settlement/fee/PnL 结论，不能任选 local/JRS DB 补齐。

## 查询前自检

```sql
PRAGMA table_info(signals);
PRAGMA table_info(fact_signal_candidates);
PRAGMA table_info(plans);
PRAGMA table_info(orders);
PRAGMA table_info(fills);
PRAGMA table_info(fact_trades);
```

不要照抄旧 schema。当前 canonical 里订单实例字段为 `orders.instance_id`；fee-adjusted fill 结果在 `fact_trades.fees_usd` / `pnl_usd_at_fill`。
查询前保存 DB realpath、device/inode、canonical `build_id`/build time 与 `observed_at_utc`；同一次复盘不得混用不同 build。

## WCIR 决策边界

新 WCIR 实例按以下链路复盘；legacy 实例通过显式 adapter 投影，不伪造缺失层：

```text
EventEnvelope -> DecisionContext -> ModelOutput -> SignalCandidate
-> TradeIntent -> ExecutionIntent/record_only handoff
-> plan -> order -> fill -> settlement
```

每层必须连接 event/checkpoint/candidate/intent/execution/order identity，并保存：

- event/available/first-seen/observation 四时钟与 revision parent。
- model/artifact/config/schema/runtime contract hash 及 `candidate_grain_version`。
- source/official/expression bracket anchor。
- `feature_book_snapshot_id` 与 `execution_book_snapshot_id`；模型看到的书不是默认成交价。
- `TradeIntent` 的 token/side/shares/profile/cap/TTL/dedupe 与 blocker。
- `record_only`/zero-notional 不得被解释为正 shares 或 venue call。

## 链路检查

对每个 candidate/order 输出：

- thesis：source、snapshot、forecast/path state、market exact bracket、model/market probability、edge。
- eligibility：机制条件、freshness、city/source basis、策略状态。
- decision contract：checkpoint/candidate grain、model/runtime identity、intent/blocker、feature/execution quote identity。
- plan：side、desired shares、notional、limit、execution policy、skip reason。
- order：requested/posted price、shares、maker/taker、status、exchange response、created/placed time。
- fill：各 partial fill 的 shares/price/fee/tx，累计量不得超过权威 order state。
- settlement：source grain、join method、final bracket、fee-adjusted PnL。

异常订单必须把四件事拆开：

1. submitted order size
2. actual fill size（含 partial fills）
3. scoped cap/gate state
4. exchange rejection/status text

不能用“下单 5 shares”概括一个实际成交 13 shares 的链，也不能把 `matched` 当成 fee 已经完整。

## 常见异常

- candidate 有、intent 无：policy blocker、token/expression 不可无损映射或 coverage-only。
- intent 有、handoff/plan 无：record-only、execution-profile mapping、risk/dedupe blocker。
- legacy signal 有、plan 无：selector/eligibility/duplicate gate；不得反推伪造 `TradeIntent`。
- plan 有、order 无：risk/quote/executor skip。
- order 有、fill 无：resting/cancel/reject，不是成交。
- replacement overbuy：cancel 后未读 authenticated `size_matched` 或 fill cache 延迟。
- FOK share-cap 异常：BUY amount 语义与 shares/cash 混淆。
- stale thesis：第一次提交与每次 reprice 使用的 weather snapshot 不一致。
- quote clock drift：把 `feature_book_snapshot_id` 当成实际下单时 `execution_book_snapshot_id`。
- source-basis false cross：快源温度不是 settlement-facing source 的确定标签。
- settled 且 PnL NULL、fee evidence 缺失：数据链异常，暂停结论。

## 修复任务的默认尾项

定位并修复根因后，必须重放污染窗口：给出受影响 orders/fills 数、逐条清单、正确口径下不会下/会少买/会错过的单，并更新数据治理记录。

报告模板：`docs/analysis/templates/lineage.md`。即时单笔诊断可直接回答；涉及事故或多单影响时写入当月 analysis 文档。
