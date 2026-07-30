# Current-YES Core Carry v3 live signal funnel v1

## 数据快照

- 数据源：当前 Mac production raw
  `/Volumes/jrs/pm_agents/runtime/weather_edge_v1/current_yes_core_carry_tiny_live_v2/pre_live_scores.jsonl`。
- 窗口：完整 target dates `2026-07-27..2026-07-29`；只取
  `current_yes_core_carry_model_v3_no_peak_clock` 且已完成概率评分的 checkpoint。
- 粒度：city-day-local-hour checkpoint；共 `324` rows、`3` 个独立 target dates。
- 本报告只回答 signal frequency，不发布 settlement/PnL；unsettled 与 missing bracket 不适用。
- 复跑：
  `.venv/bin/python scripts/analysis/reheat_risk/research_current_yes_core_carry_live_signal_funnel_v1.py`。

目标：判断 Core Carry 低触发率来自 live 异常、价格域、概率 residual，还是 immediate
5-share taker 表达。

## 结论

低频符合冻结策略本身，不是 live 漏跑。三个完整 target dates 共 `11` 个 eligible
city-day signal，逐日为 `4 / 4 / 3`，平均 `3.67` 个/日；历史 frozen replay 为
`104 / 31 = 3.35` 个/日，两者同量级。

表达确实窄，但最大的可量化收缩不只是概率模型：`128` 个价格域内 checkpoint 中，
模型相对 market mid 有正 residual 的有 `42` 个；full 5-share taker ladder 与官方
fee 后只剩 `11` 个正 EV。即 `31/42` 个正 market residual 没有成为 taker signal：
`30` 个被 spread/depth/fee 变为负 EV，`1` 个没有足够 5-share ask depth。

## Signal funnel

| stage | checkpoint rows | 上层占比 |
|---|---:|---:|
| completed frozen-v3 checkpoints | 324 | 100.0% |
| market mid 0.80–0.9895 | 128 | 39.5% |
| model probability > market mid | 42 | 32.8% |
| model probability > full taker cost | 11 | 26.2% |
| eligible first-positive city-day signal | 11 | 100.0% |

相对全部 completed checkpoints，最终 signal rate 为 `3.40%`。

价格域外的 `196` rows 分为：mid `<0.80` 的 `147` rows、mid `>0.9895`
的 `49` rows。价格域是第一层大收缩；进入价格域以后，immediate taker execution
cost 是第二层大收缩。

## Daily funnel

| target date | completed | cities | in-domain | positive model-mid | positive taker EV | signals |
|---|---:|---:|---:|---:|---:|---:|
| 2026-07-27 | 100 | 26 | 35 | 15 | 4 | 4 |
| 2026-07-28 | 118 | 38 | 53 | 21 | 4 | 4 |
| 2026-07-29 | 106 | 37 | 40 | 6 | 3 | 3 |

## 解释与动作

Core Carry 是高置信 late-persistence residual，不是全日 Tmax 分布策略。它只表达：

- current exact bracket BUY YES；
- local 13–17；
- 每小时首次 `minute>=30`；
- bounded bracket、market mid 0.80–0.9895；
- frozen probability 超过 full 5-share taker cost；
- first positive EV 后锁 city-day。

概率头也刻意保持窄：market logit 加 local hour、dewpoint depression、wind；此前加入
暖平流、露点趋势、强风混合等语义没有在同分母 OOF/frozen forward 稳定改善 proper
score，因此不能为了增加交易数直接塞回模型。

动作：保持 Core Carry live 不变。若目标是增加可执行机会，应并行验证而不是放宽同一
策略：

1. `post-rebracket` zero-notional event shadow，验证小时 checkpoint 漏掉的升档窗口；
2. maker-only residual shadow，覆盖 `p_model > mid` 但 `p_model <= taker cost` 的
   31 个状态，并显式建 fill/queue/adverse-selection；
3. 需要 current/d1/d2/tail 的宽表达时，继续使用 Tmax distribution family，不把它
   混进 Core Carry。

结论等级：`inconclusive / no live change`。本报告是 signal-funnel 与表达诊断，不构成
新 alpha 或扩大 live 的证据。
