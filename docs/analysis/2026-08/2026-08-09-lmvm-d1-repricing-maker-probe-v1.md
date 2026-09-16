# LMVM D-1 forecast-repricing maker probe v1

significance=`POINT_POSITIVE_CI_FAIL`：secondary holdout conditional ROI `+0.96%`，target-date bootstrap 95% CI `[-1.87%, +2.71%]`

calibration=holdout 全行 predicted/actual 30m net markout mean `-0.50c/-0.67c`，MAE `0.96c`；这不是 probability calibration，也不能替代真实 fill 校准

baseline=同一 holdout、同一 first-cross city-day 分母的 mechanical maker-conditional baseline ROI `-3.82%`；challenger 相对改善 `+4.78pp`

forward=模型、30m horizon 与 q80 threshold 已锁定；仍需 fresh zero-notional queue/fill forward

execution=`maker_conditional / fill-unverified`；taker 全部拒绝

production:
live_action=`none`
orders_changed=`0`

## 1. Research brief

- **唯一假设**：D-1 forecast 更新后的天气变化、当前 ladder 状态和盘口变化联合起来，可以识别未来 30–60 分钟 bid repricing；若 taker spread 吃掉 edge，则只保留 maker 条件候选并用 fresh fill 证据验收。
- **主指标**：entry 后 30/60 分钟、扣官方 fee 的 executable markout ROI；target-date block bootstrap。
- **分母**：`forecast_innovation_argmax × D-1 × PIT snapshot`，每个 `city × target_date` 只允许首个 threshold cross，避免同一仓位重复计数。
- **唯一动作**：若 taker 不通过、maker 条件结果在 development 与 secondary holdout 都为正且胜过机械 maker baseline，则冻结为 zero-notional maker queue/fill probe；不改 live。

## 2. 数据恢复与 readiness

迁移后的 hot source 只剩 2,898 个旧 paper snapshots、6 个 D-1 target dates，无法支持原计划训练。只读追查确认 N100 历史目录原有 2,643 文件，但磁盘出现真实 `Input/output error` 和读取中途文件缩短。没有把缺失伪装成策略筛选，也没有让损坏 JSON 进入训练。

恢复后的研究 universe：

- current strategy snapshots：3,080 files；
- N100 partial recovery：931 files，其中 833 valid JSON、98 invalid JSON；
- valid union：3,913 files；
- raw records：3,139,991；ladder groups：309,345；D-1 groups：166,938；
- complete D2/D1 ladders：19,673；forecast update events：2,805；paired updates：2,767。

恢复 inventory：`/Volumes/jrs-archive/pm_agents/research/raw_recovery/n100_paper_snapshots_20260809_inventory.json`。已知 evidence gap 是 N100 物理 I/O 故障导致未能完整恢复 2,643 个文件；本报告的“历史”仅指上述可核验 union，不代表项目全部历史。

## 3. Signal / evidence funnels

| funnel | stage | count | unit / note |
|---|---|---:|---|
| signal | paired forecast updates | 2,767 | update event |
| signal | D-1 innovation candidates | 2,666 | candidate |
| signal | first q80 cross, development OOF | 75 | city-day signal，10 dates |
| signal | first q80 cross, secondary holdout | 83 | city-day signal，7 dates |
| evidence | 30m markout covered | 1,807 | candidate rows，30 dates |
| evidence | 60m markout covered | 1,783 | candidate rows，31 dates |
| evidence | actual maker fills | 0 | 当前 blocker |
| evidence | fresh frozen-forward fills | 0 | 尚未启动生产 probe |

5m 没有有效 markout，15m 只有 4 个 target dates，120m 只有 29 个日期；它们因 coverage 不足而不进入模型选择，不是被策略条件筛掉。

## 4. 固定训练设计

- development：前 32 个 target dates；严格 expanding-date OOF，每次按 3 个 target dates 出块，训练日期始终早于评分日期；
- secondary holdout：最后 8 个 target dates，`2026-06-26..2026-07-07`；模型、horizon、threshold 冻结后一次性打开；
- 日期等权，避免快照密集日控制目标；
- 最多 10 个模型版本：weather / market-context / joint feature，Ridge / shallow strongly-regularized HGB；6 个 taker negative controls、4 个 conditional-maker challengers；
- threshold 只从 development OOF 的 q80/q90/q95 选；先执行预注册稳定性 gate，再按 CI 下界排序，不能用 holdout 调 threshold；
- 选择规则：development 与 holdout ROI 均为正、holdout 胜过 matching baseline、两段 positive-date rate 均不低于 50%，holdout 至少 20 signals / 6 dates。

选中 `R8_maker_joint_ridge_gross`：joint weather + market-context Ridge，30m，q80，predicted-net threshold `-0.000583`。负阈值不是容许预期亏损：模型预测的是有噪声的 future bid move，筛选目标由含 fee 的实际 conditional markout 和日期级稳定性共同决定。

## 5. 结果

| period / policy | signals | dates | cost | PnL | ROI | positive dates | 95% CI |
|---|---:|---:|---:|---:|---:|---:|---:|
| development expanding OOF | 75 | 10 | $20.20 | +$0.57 | **+2.84%** | 60.0% | `[+0.13%, +5.25%]` |
| secondary chronological holdout | 83 | 7 | $17.49 | +$0.17 | **+0.96%** | 57.1% | `[-1.87%, +2.71%]` |
| holdout mechanical maker baseline | 140 | 7 | $140.42 | -$5.37 | **-3.82%** | 0.0% | `[-4.42%, -2.86%]` |

原始 mechanical taker 的 30m/60m ROI 分别为 `-15.53%/-15.44%`；模型赛马中没有 taker 版本达到冻结要求。因此不能通过调 threshold 把这条路包装成可交易策略。

maker 数字是“假设 entry best bid 成交，30m 后以 best bid 卖出并扣 exit taker fee”的条件 quote return。它没有计入：

- maker 是否排到 queue；
- fill 前盘口是否已经不利移动；
- 部分成交与撤单；
- 实际 fill 后的 adverse selection。

所以 `+0.96%` 不是已实现 ROI。真实期望还要由 `fill probability × conditional filled return` 再扣 queue/adverse-selection 成本；这些只能从 zero-notional/极小额 maker probe 的完整 posted→fill/expire→markout 分母得到。

## 6. 判定与冻结边界

结论是 `maker_probe_candidate`：相比原始策略已从稳定负值改善为 development/holdout 点估均正，并在 holdout 显著胜过同口径机械 maker baseline；但 holdout CI 跨 0，而且 actual fills=0，尚不是 confirmed alpha。

本次冻结内容仅包括：

- model spec、feature schema、30m horizon、q80 threshold；
- serialized scorer 与 SHA；
- first-cross candidate 规则；
- fresh forward 不再调参的约束。

未冻结为 live strategy，未启动 collector/runtime，未提交订单。下一阶段应接入现有共享 `weather_market_books` / forecast snapshot 血缘，记录 posted quote、queue proxy、fill/expire、entry/exit book clocks 和 5/10/30/60m markout。该动作会改变生产 collector/runtime 行为，必须另走 `weather-strategy-deploy` 并在部署前取得显式确认。

## 7. 可复跑产物

- training base：`/Volumes/jrs-archive/pm_agents/research/artifact_store/active/lmvm_repricing_challenger/training_base_recovered_20260809_b`
- frozen candidate：`/Volumes/jrs-archive/pm_agents/research/artifact_store/active/lmvm_repricing_challenger/challenger_v4_maker_stable_20260809`
- model artifact：`maker_probe_model.joblib`
- model SHA-256：`ba6621c2641b2415733c979e1b01a4cb5ab869e89a9835cb93ba185e0ae65081`
- one-shot score smoke：`artifact_score_smoke/`，2,666 rows → 435 threshold crosses → 301 first-cross candidates；artifact SHA 校验通过。

训练复跑：

```bash
.venv/bin/python -m weather_model_evaluation.lmvm_repricing_challenger \
  --candidate-csv /Volumes/jrs-archive/pm_agents/research/artifact_store/active/lmvm_repricing_challenger/training_base_recovered_20260809_b/candidate_markouts.csv \
  --output-dir /tmp/lmvm_repricing_challenger_replay
```

冻结 artifact 单次评分：

```bash
.venv/bin/python -m weather_model_evaluation.lmvm_repricing_challenger \
  --candidate-csv /Volumes/jrs-archive/pm_agents/research/artifact_store/active/lmvm_repricing_challenger/training_base_recovered_20260809_b/candidate_markouts.csv \
  --output-dir /tmp/lmvm_repricing_score \
  --score-model-dir /Volumes/jrs-archive/pm_agents/research/artifact_store/active/lmvm_repricing_challenger/challenger_v4_maker_stable_20260809
```

血缘位置：forecast/market snapshot → D-1 paired update → LMVM model score → zero-notional `SignalCandidate`；本次尚未进入 `TradeIntent → plan → order → fill`。
