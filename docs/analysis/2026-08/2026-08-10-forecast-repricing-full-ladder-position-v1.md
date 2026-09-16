# Forecast Repricing：full-ladder position policy v1

significance=FAIL（entry relative-markout 相对 M0 的 OOF/holdout CI 均跨 0）

baseline=market-level relative-markout M0

forward=NA（截至 2026-08-09 的 reconstructed holdout，不是 collector-exact frozen forward）

execution=maker-fill-gated；conditional dynamic ROI `+36.39%`，actual fills=`0`；同 rows 不胜 fixed 60m

production: live_action=none; orders_changed=0

## 结论

旧报告只到 `2026-07-07` 不是原始数据只到该日，而是重建适配器漏接数据层迁移：

- legacy 阶段把各 rung 的 bid/ask 内嵌在 `strategy_snapshot`；
- 2026-07-08 后 `strategy_snapshot` 多为 hot/partial book，完整盘口迁入独立的
  `orderbook_snapshots`、后续 `market_books + market_ladder_snapshots`；
- 旧 loader 仍要求 snapshot 内嵌完整报价，因此把已有盘口静默计为
  `incomplete_quote_or_probability`。

现在 loader 会按采集代际联接同批次 companion orderbook，并把 decision clock 推进到盘口真实
`fetched/available` 时刻；当前 raw 则复用 canonical full-ladder join。已经重建到默认 T-1
`2026-08-09`。旧的 39 positions / 6 dates / `+13.13%` 只代表漏数训练片，不再用于当前决策。

策略本身仍是可加载、zero-notional 的完整 position policy：

```text
D-1 forecast revision
  -> 所有 ladder rungs 同时评分
  -> NO_TRADE / POST_MAKER
  -> actual fill 才建立真实 position
  -> 30m full-ladder continuation score
  -> EXIT / HOLD
  -> 60m hard exit
```

新回放表面上更高的 `+36.39%` 仍不是已确认 alpha：它以 maker bid 成交为条件，但历史没有 queue/fill
证据；同一批 selected rows 用 taker ask 入场为 `-48.25%`。而且在相同 48 笔 position 上，dynamic
exit ROI `25.05%`、fixed 60m `26.12%`，delta `-1.07pp`，CI `[-7.46pp,+5.01pp]`。因此盈利点估
来自假设能在宽 spread 的 bid 被动成交，以及少量大赢家，不是退出模型已经有效，也不是 weather head
已战胜 M0。

## 数据迁移修复与影响半径

输入：`forecast_event_rungs.csv`，SHA-256
`4f7b0f002b19dafd414aaac69a437d03c79f5f6146e2c2de6b5a58ca1ff449a3`。

| funnel | 旧适配器 | 修复后 | delta |
|---|---:|---:|---:|
| raw event-rungs | 28,038 | 62,444 | +34,406 |
| D-1 rungs | 26,986 | 61,392 | +34,406 |
| D-1 forecast events | 2,666 | 5,902 | +3,236 |
| D-1 target dates | 40 | 65 | +25 |
| 30m direct-bid scoreable | 18,399 | 44,132 | +25,733 |
| 60m direct-bid scoreable | 18,086 | 45,451 | +27,365 |
| actual fills | 0 | 0 | 0 |

修复后的 state 目标日期范围是 `2026-05-21..2026-08-09`。36,032 个保留 states 的盘口血缘为：

| quote/clock lineage | states |
|---|---:|
| legacy inline snapshot | 14,017 |
| historical same-capture companion orderbook | 20,764 |
| current canonical joined full ladder | 1,251 |

这不是无损补全。完整 full-ladder collector 从 2026-07-15 才开始覆盖，故 D-1 可评分 target date
从 2026-07-16 恢复；`2026-07-08..2026-07-15` 仍是已登记的真实 evidence gap。早期 targeted
collector 只覆盖少数 hot 城市，不能伪装成 47 城完整 ladder。历史 provider run/issue timestamp 也仍缺失，
所以本报告只称 reconstructed holdout，不称 formal first-seen forward。

## 模型与同分母基准

Entry label：

```text
60m rung-relative bid move
= held rung bid move - ladder median bid move
```

Challenger：

```text
M0 market level / mode distance / neighbor shape / spread / depth
+ weather probability shock
+ weather mode shift / transport L1
+ rung-relative immediate response
+ neighbor propagation / lead-lag
+ weather shock × mode distance × neighbor propagation
```

修复后 entry 增量仍未通过：

| split | rows / dates | MSE delta challenger-M0 | target-date bootstrap 95% CI |
|---|---:|---:|---:|
| development expanding OOF | 28,046 / 29 | `-0.00000004` | `[-0.00000178,+0.00000141]` |
| secondary holdout `2026-07-29..2026-08-09` | 10,043 / 12 | `-0.00000058` | `[-0.00000195,+0.00000074]` |

Terminal probability head 也仍失败：legacy weather model Brier `0.08423`，同期 market `0.06716`，
delta `+0.01707` CI `[+0.01532,+0.01877]`；logloss `2.8524` vs `1.4024`。这条 terminal 负结论
与短周期 repricing 检验分开，但两者都不支持宣称独立 weather alpha。

## Maker threshold 与 position replay

Threshold 只在 development OOF 选择，为 `0.0043484`；development conditional 为 157 positions / 27 dates，
ROI `+11.41%`。Secondary holdout 固定为 `2026-07-29..2026-08-09`：

| policy | positions | active dates | conditional ROI | 95% CI |
|---|---:|---:|---:|---:|
| full-ladder dynamic exit | 49 | 11 | `+36.39%` | `[+17.20%,+52.44%]` |
| fixed 30m diagnostic | 49 | 12 | `+30.74%` | `[+8.98%,+47.90%]` |
| fixed 60m diagnostic | 48 | 11 | `+26.12%` | `[+10.11%,+40.58%]` |
| dynamic vs fixed 60m，严格同 48 rows | 48 | 11 | `-1.07pp` | `[-7.46pp,+5.01pp]` |

直接比较 49 vs 48 会被一笔没有 60m quote 的 position 污染，因此决策只采用最后一行 paired delta。

### 盈利来源与执行反事实

49 笔 dynamic positions 的 maker bid 成本合计 `1.106`，future bid 合计 `1.577`，退出 fee
`0.0686`，conditional PnL `+0.4024`。同一 selected rows 改用当时 ask taker 入场并扣入场 fee：

| execution expression | PnL | ROI |
|---|---:|---:|
| maker entry conditional + future bid exit | `+0.4024` | `+36.39%` |
| taker ask entry + future bid exit | `-1.4065` | `-48.25%` |

平均 entry bid 约 `2.26c`，ask `5.70c`，spread `3.44c`；16 胜、33 负，29 城。少量低价大 markout
贡献较多利润，Kuala Lumpur 单笔贡献约 `+0.178`。在没有真实 queue、partial fill、expire 与 adverse-selection
分母前，maker 条件收益只能决定“继续采集”，不能决定“可真实交易”。

## 可运行入口

重建与训练：

```bash
.venv/bin/python scripts/analysis/market_structure_edge/research_lmvm_forecast_innovation_v2.py \
  --snapshot-dir /Volumes/jrs-archive/pm_agents/research/raw_recovery/lmvm_union_20260809 \
  --output-dir /Volumes/jrs-archive/pm_agents/research/artifact_store/active/forecast_repricing/full_ladder_base_20260810_tminus1 \
  --report /Volumes/jrs-archive/pm_agents/research/artifact_store/active/forecast_repricing/full_ladder_base_20260810_tminus1/baseline_report.md \
  --end-target-date 2026-08-09 --workers 8 --draws 5000

.venv/bin/python -m weather_model_evaluation.cli forecast-repricing-position \
  --input /Volumes/jrs-archive/pm_agents/research/artifact_store/active/forecast_repricing/full_ladder_base_20260810_tminus1/forecast_event_rungs.csv \
  --output-dir /Volumes/jrs-archive/pm_agents/research/artifact_store/active/forecast_repricing/full_ladder_position_20260810_tminus1
```

Artifact：

- base：`/Volumes/jrs-archive/pm_agents/research/artifact_store/active/forecast_repricing/full_ladder_base_20260810_tminus1`
- position：`/Volumes/jrs-archive/pm_agents/research/artifact_store/active/forecast_repricing/full_ladder_position_20260810_tminus1`
- model SHA-256：`812adb1e8f548babedc486a2f35f7b5b967c6d3b0c7bc84f3b155eecf5833c16`

现有 zero-notional runner 可继续加载该 artifact，但本轮没有部署、重启生产或提交订单。

## 当前动作

保持 `runnable zero-notional / inconclusive`。继续积累 collector-exact forecast event、maker post/queue/actual fill
及 5 分钟 full-ladder position checkpoints；研究侧必须同时改善 entry relative-markout 和 paired dynamic-exit uplift。
只有 actual-fill 分母及 frozen forward 同时通过，才讨论真实部署。
