# Core Carry full-ladder prior v1

**结论：`reject_full_ladder_challenger_v1`；不改 live。**

## 研究问题与 readiness

- 目标仍是 Core Carry 的 `P(current exact bracket holds)`；没有混入 first-seen/markout 策略。
- 固定母表 1,349 checkpoints / 31 dates；完整 ladder 可评分 898 / 66.6%，覆盖 29 dates。
- 每行只读取母表保存的 exact decision-time orderbook snapshot；缺证据记 coverage gap。
- 这个机制在 2026-08-08 才冻结，因此最后 8 dates 只是 historical secondary holdout，不是 clean forward。

## Primary：historical secondary holdout

| model | state entries | Brier | logloss | mean p | actual |
|---|---:|---:|---:|---:|---:|
| p_market_hold | 119 | 0.058716 | 0.226402 | 91.855% | 93.517% |
| p_ladder_feasible_hold | 119 | 0.060780 | 0.235352 | 91.038% | 93.517% |
| p_core_hold | 119 | 0.056583 | 0.212863 | 90.852% | 93.517% |
| p_core_plus_ladder | 119 | 0.056335 | 0.210812 | 91.528% | 93.517% |

- challenger − Core Brier：-0.000249（95% CI [-0.000842, +0.000440]）
- challenger − Core logloss：-0.002051（95% CI [-0.004135, +0.000061]）
- challenger − market Brier：-0.002381（95% CI [-0.006501, +0.001395]）
- challenger − market logloss：-0.015590（95% CI [-0.032802, +0.001606]）

## 为什么没有增量

- no-fit feasible-ladder prior 相对 raw market 的 Brier/logloss delta 为 +0.002064 / +0.008950，方向更差。
- 冻结模型的两个 ladder 系数都是 `0.000000` / `0.000000`；secondary 的小幅改善只来自 calibration intercept `+0.088288`，不是 ladder shape。
- development OOF 的 challenger−Core Brier/logloss 为 +0.001136 / +0.003272；Brier CI 已完全在 0 以上，历史开发段明确更差。
- 市场本身已经基本清掉不可能的下方档：lower-rung mass 中位数 0.000000、均值 0.000488；全 ladder midpoint 总质量中位数 1.0045，接近 1。静态完整 ladder 因而没有补出当前 token 未包含的新信息。
- 451 个 coverage gap 全是旧 snapshot 只采 2–3 个局部 rungs；没有 missing file、未来 quote 替代或 current-rung join 错误。这是旧 collector scope，不是策略过滤。

## 策略动作

- historical_probability=FAIL；market_baseline=PASS；coverage=FAIL；clean_forward=FAIL_NOT_YET_AVAILABLE。
- execution=NOT_RUN_PROBABILITY_GATE_FAILED。没有用少量 selected trades 倒推模型，也没有修改 eligibility、fixed 10、maker 或生产参数。
- 本轮历史概率门失败：不新增 Core challenger、不接 shadow、不跑执行 ROI，也不为 coverage gap 追补一个事后 3-rung 版本。现有完整 ladder collector照常保留，但不把它接进 Core 概率。

## 产物与复现

- 大产物：`/Volumes/jrs-archive/pm_agents/research/artifact_store/current_yes_core_carry_full_ladder_prior_v1/historical_parent_1349_full_ladder_20260808`
- prereg SHA-256：`9f9464fc3757d8d918ae4e8564c28b026e9843ed2341e4742c688fab0f148def`

```bash
.venv/bin/python scripts/analysis/reheat_risk/core_carry_full_ladder_prior.py
```
