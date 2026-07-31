# Tokyo continuous ladder：15-day frozen forward v3

## 数据快照

| 字段 | 值 |
|---|---|
| 目标 | 在完全冻结的最近 15 个 target dates 上，评估 Tokyo remaining-rise probability、同分母 market baseline，以及 current/next exact-bracket 的 zero-notional taker 回放 |
| 训练截止 | `2026-07-15` |
| forward | `2026-07-16..2026-07-30`，完整 15 日 |
| 训练数据 | `62,214` checkpoints / `804` target dates |
| forward 天气分母 | `1,170` checkpoints / `15` target dates |
| PIT 盘口+结算覆盖 | `793` states / `12` dates |
| 盘口 coverage gap | `2026-07-17`、`2026-07-18` 无 snapshots；`2026-07-30` 无 settlement |
| collector exact | `431` states / `8` dates（`2026-07-22..29`） |
| unsettled | 天气模型分母 `0/1,170`；盘口 score/trade 分母只保留 settled，`0/793` unsettled |
| trade class | `research_counterfactual` / zero-notional；actual orders=`0`，actual fills=`0` |
| feature source | `tokyo_jma_multivariate_path_v1/feature_rows.csv.gz`，mtime `2026-07-31 02:31:42 +08:00` |
| exact lineage | `tokyo_jma_exact_enriched.csv`，mtime `2026-07-30 00:28:48 +08:00` |
| market snapshot | `five_city_weather_microstructure/v1/snapshot=20260730T120000Z/state_rows.csv.gz`，mtime `2026-07-30 20:13:27 +08:00` |
| fee/execution | 5 shares、selected-side taker ask、Weather `feeRate=0.05`、每股 fee 五位小数、raw ask depth `>=5` |

production manifest 于 `2026-07-31T09:03:05Z` 返回 `warning`，唯一 finding 是
部分 JRS tmux sessions 未进入 instance registry。本研究没有读取 canonical
`fact_trades` 或发布 `live_real` PnL，只使用上述 research feature、raw book、
settlement 与 exact collector 证据，因此继续 raw research replay；未同步或重建 DB。

## 冻结规则

这次不是把最近几天算完后再从训练集里“解释性剔除”，而是训练前固定：

```text
train target_date <= 2026-07-15
forward target_date = 2026-07-16..2026-07-30
```

forward 的 `1,170` 行没有进入：

- 模型 fit；
- hyperparameter selection；
- temperature calibration；
- champion 选择；
- edge threshold、side、bracket 或 entry timing 选择。

模型结构与温度沿用进入 forward 前已冻结的 Tokyo v2 规格；只把当时能够知道的
截至 7 月 15 日全历史重新 fit。artifact 明确写入
`forward_labels_used_in_fit=false`、training cutoff、feature semantic hash、
model-spec hash 与 model artifact hash。

预注册交易政策同样固定：

```text
outcomes = current exact / next exact
sides = YES / NO
edge >= 2%
每个 model / target_date 取第一个合格 checkpoint
5-share taker ask + official Weather fee
```

forward 只复核，未根据结果增加 local-hour、price、probability 或 weather filters。

## 结论

模型的“天气分类准确率”不错，但当前交易表达完全失败：

- checkpoint exact-class accuracy `83.59%`；
- within-one accuracy `96.67%`；
- stay/leave accuracy `95.38%`；
- 但有盘口的 12 个 forward dates 上，预注册策略 `0/12` 命中，
  fee-adjusted PnL `-$0.5609`，ROI `-100%`；
- weather model 的同分母 Brier `0.1929`，market 为 `0.1194`；
  delta `+0.0735`，95% CI `[+0.0156,+0.1320]`，显著差于 market。

因此：

```text
significance=FAIL
baseline=FAIL
forward=FAIL
conclusion=rejected_for_expression
action=停止“每日首个 current/next 2% edge 即买”表达；
       保留 probability model / collector / artifacts，不改 live
```

这里否定的是当前 entry expression，不是否定 JMA/METAR path feature 或整个
Tokyo 模型方向。

## 模型准确率

所有指标按 target date 等权；accuracy CI 为 target-date block bootstrap。

| grain | states / dates | exact class | within one | stay/leave | Brier | RPS |
|---|---:|---:|---:|---:|---:|---:|
| checkpoint | 1,170 / 15 | **83.59%** [79.83%,87.01%] | **96.67%** [94.53%,98.63%] | **95.38%** [92.99%,97.44%] | 0.2395 | 0.05475 |
| transition | 508 / 15 | 71.97% [64.19%,79.24%] | 94.34% [90.48%,97.63%] | 91.67% [86.42%,96.08%] | 0.3833 | 0.08296 |
| state-entry | 93 / 15 | 64.08% [55.33%,72.95%] | 89.44% [83.93%,94.88%] | 89.57% [83.94%,94.81%] | 0.4320 | 0.10395 |

这解释了为什么只报 checkpoint accuracy 会看起来“模型很准”：同一天大量下午
pullback/fade 行容易判断；真正的 transition/state-entry 精度低很多。

coherent-checkpoint challenger 在 checkpoint 上点估略好：

- exact accuracy `84.27%`；
- Brier `0.2365`；
- 相对 frozen champion 的 Brier delta `-0.00293`，
  95% CI `[-0.01365,+0.00849]`。

CI 跨零，不能利用这 15 天事后改 champion。它保留为下一窗口的预注册
challenger。

## 模型与 market 的同分母比较

| evidence | states / dates | model Brier | market Brier | delta model-market | 95% CI |
|---|---:|---:|---:|---:|---:|
| 全部可用 forward book | 793 / 12 | 0.1929 | **0.1194** | +0.0735 | **[+0.0156,+0.1320]** |
| collector exact | 431 / 8 | 0.1548 | **0.1236** | +0.0311 | [-0.0199,+0.0809] |

全可用盘口中，天气模型显著差于 market；collector-exact 子集点估仍差，
只是 8 日期 CI 太宽。概率层没有 residual alpha，selected ROI 无权绕过该失败。

## 策略执行准确率与 ROI

“执行准确率”拆成两个不同概念：

1. **depth eligibility**：被选信号在 raw book 是否有至少 5 shares；
2. **settlement hit rate**：假设按 ask 成交后 exact bracket 是否最终获胜。

它不是 actual fill accuracy，本轮没有真实下单或成交。

| 分母 | selected | 5-share executable | settlement wins | hit rate | cost | fee-adjusted PnL | ROI |
|---|---:|---:|---:|---:|---:|---:|---:|
| 15-day forward 中有 settled book 的日期 | 12 | 12 / 12 = **100%** | 0 | **0%**，Wilson 95% CI [0%,24.25%] | $0.5609 | **-$0.5609** | **-100%** |
| 上述已选订单中 trigger 本身为 collector exact | 3 | 3 / 3 = 100% | 0 | 0%，CI [0%,56.15%] | $0.2255 | -$0.2255 | -100% |

按完整 15-day 分母、未覆盖日期记零交易，champion 平均
PnL/date 为 `-$0.0370`，target-date bootstrap 95% CI
`[-$0.07825,-$0.00770]`。这不是“ROI 因样本少不确定”，而是当前表达在
available forward 上方向明确地错。

collector exact 审计是先在完整可用 stream 选单，再标记哪些 trigger 为 exact。
禁止先过滤到 exact 行、再从较晚时刻重新选“首单”；后者会改变策略并制造
collector-coverage survivor bias。

## 下单概率、赔率与 edge 分布

champion 的 12 个拟下单全部是 `YES`：

| 维度 | 分布 |
|---|---|
| expression | current YES `9`；next YES `3` |
| model `p_win <5%` | `6` |
| `5%<=p_win<10%` | `3` |
| `10%<=p_win<25%` | `2` |
| `25%<=p_win<50%` | `1` |
| selected-side ask | 12/12 全部 `<5%` |
| mean model p_win | `9.11%`，range `2.38%..39.54%` |
| mean ask | `0.89%`，range `0.10%..4.90%` |
| mean fee-adjusted edge | `8.18%`，range `2.28%..39.43%` |
| entry local hour | mean `06:20 JST`，range `05:00..10:50 JST` |

所有看起来最大的 residual 都来自模型在清晨给低档 current/next 一个不小的
尾部概率，而 market 已把这些档位压到 `0.1%–4.9%`。12 单最终全输。

forward 全 checkpoint 的 calibration 也显示该方向系统性高估：

| outcome | model probability bin | mean p_model | actual rate |
|---|---:|---:|---:|
| current / delta 0 | 0–5% | 1.90% | **0.49%** |
| current / delta 0 | 5–10% | 7.19% | **4.55%** |
| current / delta 0 | 10–25% | 15.98% | **6.74%** |
| next / delta 1 | 0–5% | 1.89% | **0.00%** |
| next / delta 1 | 5–10% | 7.19% | **2.31%** |
| next / delta 1 | 10–25% | 15.23% | **8.37%** |

这正是“模型 accuracy 高、交易却全错”的原因：argmax 往往预测对较高的
remaining-rise class，所以总体 accuracy 高；但交易挑选的是被模型高估的
低概率 current/next 尾部，proper-score 与 ROI 都更差。

## 对 Tokyo 策略的具体启示

第一版策略不能采用：

```text
每次 JMA 更新 -> 找 p_model - ask 最大的 current/next -> 首次超过 2% 就买
```

该规则实际上会在平均 `06:20 JST` 捕捉最早的低价 YES，而此时严格 PIT
forecast ceiling / peak clock 尚未进入模型。它把“未来还会升很多档”的质量
分配错误解释成“市场严重低估当前/下一档”。

正确的下一轮不是用本 forward 事后加一个 09:00、ask floor 或 p_win band
filter；那会污染 holdout。下一版应先在训练数据层补：

- conservative issue/run lineage 的 forecast ceiling；
- forecast peak clock 和 remaining heating window；
- current/next 相对 forecast ceiling 的 lattice margin；
- early-day tail calibration。

然后固定新模型和新 entry policy，再用 **7 月 31 日以后从未看过的新日期**
做第二个 forward。当前 15 天只作为 v3 的一次最终审计，不重复调参后再声称
仍是 holdout。

## 双漏斗

Signal funnel：

| stage | unit | rows | dates |
|---|---|---:|---:|
| frozen weather checkpoints | state | 1,170 | 15 |
| joined current/next expressions | expression | 7,461（三模型） | 12 |
| champion first city-day signal | signal | 12 | 12 |

Evidence funnel：

| stage | unit | rows | dates |
|---|---|---:|---:|
| settled PIT/proxy book join | state | 793 | 12 |
| collector-exact book join | state | 431 | 8 |
| champion 5-share executable replay | research trade | 12 | 12 |
| actual fill | fill | 0 | 0 |

盘口缺失的 3 天是 evidence coverage gap，不是策略过滤。

## 8 环与结论等级

| 环 | 状态 |
|---|---|
| 描述性绩效 | 完成 |
| 统计推断 | target-date bootstrap / Wilson CI 完成 |
| 信号判别 | 三 grain accuracy 完成 |
| 概率分布 | Brier、logloss、RPS、calibration 完成 |
| 执行微结构 | selected-side ask、官方 fee、5-share depth 完成；actual fill 缺失 |
| 容量 | 只验证 5 shares，未做放大 |
| 组合相关性 | 单城市，不适用 |
| 基准/反事实 | same-row conditional market 完成 |

当前 current/next first-edge expression：

```text
significance=FAIL
baseline=FAIL
forward=FAIL
conclusion=rejected_for_expression
live_action=none
```

Tokyo continuous probability model：

```text
probability forecasting ability=present
market residual=FAIL
conclusion=inconclusive / research-only
next action=补严格 PIT forecast features，固定后等待新 forward
```

## 产物与复现

代码：
`scripts/analysis/market_structure_edge/research_tokyo_continuous_ladder_forward_v3.py`

测试：
`tests/research_tests/test_tokyo_continuous_ladder_forward_v3.py`

生成目录：
`docs/analysis/2026-07/generated/tokyo_continuous_ladder_forward_v3/`

关键文件：

- `model_scores.csv`
- `daily_model_scores.csv`
- `model_outcome_calibration.csv`
- `market_scores.csv`
- `strategy_summary.csv`
- `strategy_side_summary.csv`
- `selected_trades.csv`
- `selected_trades_exact.csv`
- `order_probability_distribution.csv`
- `order_edge_distribution.csv`
- `prediction_long.csv.gz`
- `models/*.joblib` / `models/*.spec.json`
- `summary.json`

复现：

```bash
.venv/bin/python \
  scripts/analysis/market_structure_edge/research_tokyo_continuous_ladder_forward_v3.py
```

最终一句：在 `2026-07-16..30` 的 frozen 15-day 分母，frozen champion 相对
same-row market 的 Brier delta 为 `+0.0735`
（95% CI `[+0.0156,+0.1320]`），预注册 current/next first-edge 表达
fee-adjusted ROI `-100%`、`0/12` 命中，forward `FAIL`，结论
`rejected_for_expression`，动作是不改 live、保留模型与采集、补 PIT forecast 后
等待下一段真正未见日期。
