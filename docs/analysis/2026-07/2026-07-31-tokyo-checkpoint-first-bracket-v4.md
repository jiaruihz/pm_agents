# Tokyo 每 10 分钟更新、每档首次建仓 v4

Artifact routing: new replays require a stable `--run-id` (or an explicit
temporary `--out`) and write immutable machine output beneath the configured
JRS research artifact root. The wrapper no longer injects a dated repository
output directory.

> 2026-07-31 更新：v4 保留为 expression negative control。它虽然修正了“每档
> 第一次”，但仍把 binary “还会升温”概率表达成 current/next exact。第一版已由
> [Tokyo current-bracket stay/break binary v5](2026-07-31-tokyo-current-break-binary-v5.md)
> 接替：只交易 current exact YES/NO，next exact 禁用。

## 数据快照

| 字段 | 值 |
|---|---|
| 数据源 | Tokyo JMA multivariate feature rows、JMA exact collector、raw Polymarket full-ladder books、pm_history settlement |
| 模型训练截止 | `2026-07-15` |
| 诊断窗口 | `2026-07-16..2026-07-30`，15 个完整天气日期 |
| 天气 checkpoints | `1,170` |
| PIT book 覆盖 | `248` book-time states / `12` settled dates |
| coverage gap | `7/17–18` 无 book；`7/30` 无 settlement |
| unsettled | 天气分母 `0/1,170`；交易分母只保留 settled |
| missing_bracket | `0` |
| 交易性质 | `research_counterfactual` / zero-notional；actual order/fill=`0/0` |

## 修正后的目标与持仓规则

天气模型本来就在每个 JMA 10-minute checkpoint 输出：

```text
P(final remaining rise = 0 / 1 / 2 / 3+ | information available at t)
```

例如 `2026-07-25`：

| JST checkpoint | current | P(0) | P(1) | P(2) | P(3+) |
|---|---:|---:|---:|---:|---:|
| 10:00 | 30 | 0.50% | 1.77% | 9.94% | 87.79% |
| 10:10 | 31 | 0.64% | 2.82% | 12.44% | 84.10% |
| 10:20 | 31 | 0.98% | 5.67% | 14.83% | 78.52% |

因此模型层确实回答“这一刻之后还会不会升温、还会升几档”。v3 的问题不在
checkpoint 模型，而在 position selector 错写成了每天只取一笔。

v4 按用户确认改为：

```text
每个 checkpoint 更新 probability 和同刻 executable edge
position key = target_date × exact bracket
每档第一次 edge >= 2% 时建仓
同档后续信号不补仓
新进入另一档后，该档可以首次建仓
```

同一档若同刻 YES/NO 都合格，只保留 fee-adjusted edge 更高的一边，避免同一
condition 双向建仓。盘口仍按 book timestamp as-of 当时已可见的最新 weather
state，最大 state age 30 分钟。

## 旧 0/12 应如何解释

旧 v3 的 `0/12` 是 `first signal per target_date`：12 个有盘口日期每天只留一笔，
所以全部被当天很早的低档 YES 抢占。它是错误 selector 的负例，**不是用户定义
的每档首次策略命中率**，也不应再作为 Tokyo checkpoint strategy 的 headline。

修正后 frozen weather model 不变，selector 变为每档首次：

| selector | champion trades | dates | wins | hit rate | fee-adjusted PnL | ROI |
|---|---:|---:|---:|---:|---:|---:|
| v3 每天首次（错误目标） | 12 | 12 | 0 | 0% | -$0.5399 | -100% |
| v4 每档首次、禁止补仓 | 62 | 12 | 6 | 9.68% | -$10.0689 | -25.13% |

v4 的 date-block ROI 95% CI 为 `[-61.97%,+27.82%]`，跨零。因为 selector 是看过
旧窗口后按用户意图修正的，v4 的这组交易数字只能叫
`post_forward_user_corrected_selector_diagnostic`，不能冒充新的 frozen forward。

## v4 下单结构

champion 的 62 个唯一 `(target_date, bracket)` positions 全部可显示深度执行：

| expression | trades | wins | fee-adjusted ROI |
|---|---:|---:|---:|
| current YES | 19 | 1 | +141.06% |
| next YES | 33 | 2 | -42.95% |
| current NO | 2 | 0 | -100% |
| next NO | 8 | 3 | -24.98% |

入场时间从 `05:00` 延伸到 `14:30 JST`，平均 `09:25 JST`；因此现在确实包含
10:00、10:10、10:20 等连续更新后的决策，而不是每天只看清晨一次。每个
date-bracket 恰好一笔，`62 trades = 62 unique position keys`，没有补仓。

这组 side/ROI 仍不构成策略结论：current YES 的正 ROI 来自 `1/19` 低价票，
而整体 CI 跨零；同时 v4 规则尚未在新日期前瞻验证。

## 当前结论与下一窗口

- checkpoint probability layer 保留：它正确实现每 10 分钟条件更新。
- v3 `0/12` headline 对用户定义的策略作废，保留为错误 selector 负例。
- v4 position policy 冻结为“每档首次、同档不补仓”。
- `2026-07-16..30` 只作 selector mechanics diagnostic。
- 下一次可宣称 forward 的窗口从 `2026-07-31` 之后开始；完整记录全部
  checkpoints、eligible/blocked、first position 和后续 would-add telemetry，
  但第一版不执行 add-on。
- 不修改任何 live runner、plan/order/fill/exit。

门状态：

```text
significance=FAIL
baseline=FAIL
forward=NA
conclusion=inconclusive
```

复现：

```bash
.venv/bin/python \
  scripts/analysis/market_structure_edge/research_tokyo_checkpoint_first_bracket_v4.py
```
