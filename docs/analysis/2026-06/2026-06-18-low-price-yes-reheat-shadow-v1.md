# Low-Price YES Reheat Reversal Shadow v1

> generated_at_utc: `2026-06-18T12:45:48+00:00`
> Status: implementation / runner exists / forward shadow blocked by stale reheat feature inputs / no live policy change.

## Target Metric

`low_price_yes_reheat_reversal_shadow_v1` = 为低价 YES 二次升温反转分支写入 forward would-order journal。它只记录“如果当时按规则会买哪一档 YES”，不下单、不签名、不改变 N100/live 配置。

冻结规则：

```text
rule_id = d1d2_adjusted_edge_ge_008_ask_le_025_v1
BUY_YES only
target_yes_ask <= 0.25
target bracket > observed running max
distance_bucket in {d1, d2}
p_target_yes_wins - target_yes_ask >= 0.08
hypothetical_notional_usd = 2.00
```

其中 `p_target_yes_wins = raw_model_p_yes * p_reheat_context`，label 仍是后续评估用的 `target_yes_wins`，不是 `current_yes_wins`。

## 已落地

Runner:

```text
scripts/ops/low_price_yes_reheat_reversal_shadow_v1.py
```

Runtime journal:

```text
runtime/weather_edge_v1/low_price_yes_reheat_reversal_v1/shadow_candidates.jsonl
```

Runtime summary:

```text
runtime/weather_edge_v1/low_price_yes_reheat_reversal_v1/shadow_summary.json
```

首次启动命令：

```bash
./.venv/bin/python scripts/ops/low_price_yes_reheat_reversal_shadow_v1.py
```

首次启动结果：

```json
{
  "strategy_id": "low_price_yes_reheat_reversal_shadow_v1",
  "rule_id": "d1d2_adjusted_edge_ge_008_ask_le_025_v1",
  "execution_mode": "zero_notional_shadow",
  "appended": 0,
  "known_shadow_decision_ids": 0,
  "source_rows": 8731,
  "source_min_target_date": "2026-05-20",
  "source_max_target_date": "2026-06-14",
  "effective_min_target_date": "2026-06-14",
  "selected_rows_before_dedupe": 0
}
```

解释：当前本地 v1 scored feature rows 只覆盖到 `2026-06-14`，而最新 target date 没有满足冻结规则的 d1/d2 low-price YES。因此 runner 已可运行，但尚无 forward would-order row。

## 2026-06-19 勘误

不要把“runner 已创建并运行过一次”理解成“6/19 forward shadow 已经持续在跑”。

2026-06-19 复查发现：

- `runtime/weather.db.fact_signal_candidates` 已覆盖到 `2026-06-19`。
- raw orderbook snapshot 目录也已有 `2026-06-19`。
- 但 `docs/analysis/2026-06/generated/low_price_yes_reheat_reversal_v1/scored_rows.csv` 仍只到 `2026-06-14`。
- 上游 observed running-max 输入 `wu_obs` cache 当前最大观测时间约为 `2026-06-09T23:xxZ`，重新物化 observed detail 也只能到 `2026-06-10`。

所以当前不是“低价 YES 反转策略连续没有信号”，而是“reheat-conditioned shadow 还没有接入新日期的 observed/reheat feature 生产链路”。在补齐 fresh observed/reheat feature 前，`shadow_candidates.jsonl` 的 0 行不能解释为策略无信号。

## Journal 字段原则

shadow row 只写入事前可见字段：

- decision identity: `city`, `target_date`, `decision_snapshot_ts_utc`, `decision_hour_local`, `bracket`, `token_id`
- execution proxy: `target_yes_ask`, `target_yes_ask_size`, `target_yes_bid`, `target_yes_spread`
- probabilities: `raw_model_p_yes`, `p_reheat_context`, `p_target_yes_wins`, `raw_edge`, `blended_edge`, `reheat_adjusted_edge`
- reheat context: `running_native`, `current_native`, `decline_native`, `target_distance_native`, `minutes_since_running_max`
- weather/forecast: dewpoint/RH/wind/cloud/temp trends and GFS/ECMWF peak-clock backfill fields
- paired alternatives: same-state current YES ask, d1 NO ask, d2 NO ask

它不写 `target_hit`、`target_yes_pnl`、`current_yes_pnl`、`d1_no_pnl`、`d2_no_pnl` 等结算后字段，避免 forward journal 被 hindsight 污染。

## 当前阶段

`runner_ready_locally / forward_shadow_blocked / not_live`。

还差的是 forward 数据生产链路：

1. 先补齐 fresh observed running-max / reheat feature factory 到当前日期。
2. 再用 settled rows 训练、对未结算/今日 rows 打分，而不是只输出已结算研究样本。
3. 每次 sync + feature factory/scoring 更新后运行 shadow runner。
4. 有 journal row 后，每天 settlement 后评估 `target_yes_wins`、同状态 current YES、d1 NO、d2 NO。
5. 等 forward settled 样本覆盖多个 active dates，且 edge 不靠 top dates，才讨论 tiny-live。

## 三道门

- significance=NA：本文件是 shadow 证据层启动，不是收益结论。
- baseline=NA：没有新增 live alpha claim。
- forward=BLOCKED：fresh observed/reheat feature production 尚未接上当前日期。
- conclusion=`runner_ready_locally / forward_shadow_blocked / no_live_policy_change`。
