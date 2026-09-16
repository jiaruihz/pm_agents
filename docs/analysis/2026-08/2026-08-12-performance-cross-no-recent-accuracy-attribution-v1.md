# CrossNO 最近正确率归因：工程、数据链还是天气 regime

## 数据快照

| 项目 | 本次固定值 |
|---|---|
| observed at | `2026-08-12 20:30` 北京时间 / `2026-08-12 12:30 UTC` |
| canonical DB | `/Volumes/jrs/pm_agents/runtime/weather.db`；repo 入口同 device/inode `16777247/54444` |
| DB build / coverage | `MAX(fact_built_at_utc)=2026-08-12T07:00:08.539417Z`；`fact_trades` target_date 到 `2026-08-12`；`pm_history` settlement 到 `2026-08-11` |
| raw signal evidence | `/Volumes/jrs/weather_data_feed_service_runtime/output/fast_source_prev_no_trial/events.jsonl`，mtime `2026-08-12T11:53:47Z` |
| rows | raw journal 3,652；去重 first expression 530；settled 528；actual live fill expressions 104 |
| unsettled / missing | 本次分析截止 `2026-08-11`；530 个 expressions 中 2 个 settlement coverage gap，不进入正确率 |
| canonical integrity | production manifest `db_route.status=healthy`、0 critical；storage audit healthy；CLOB fill gate `gate_pass=true` |
| 策略身份 | raw `fast_source_prev_no_trial_v1`；canonical stable `strategy_id=live_weather_edge_v1_c16645cc1165` |

signal grain 是第一次发出的 `city × target_date × previous bracket` CrossNO expression，按 `condition_id`
去重。raw event journal 只作为 legacy first-seen signal evidence，不冒充 canonical
`fact_signal_candidates`；label 来自 canonical `settlement_outcomes(pm_history)`，真实成交与 fee-adjusted
PnL 来自 `fact_trades`。

## 结论与动作

结论：**最近 5 天的观感是真的，但不是一条已证实的新提升。长期工程 hardening 把启动期的信号正确率从约
91.7% 抬到 95% 左右；8/7–8/11 再冲到 97.4%/90.5%（signal/fill），主要是最近 terminal false cross
少、客观天气实现更顺，加上小样本波动。没有证据表明 8 月的新数据链优化又把策略 accuracy 抬了一档。**

- 最近 10 天对前 10 天：全 signal `140/147=95.24%` vs `112/117=95.73%`，delta `-0.49pp`
  （95% CI `[-5.03pp,+4.03pp]`）；真实 fill `32/39=82.05%` vs `27/32=84.38%`，delta `-2.32pp`
  （95% CI `[-18.40pp,+12.69pp]`）。没有提升。
- 最后 5 天对前 5 天：全 signal `75/77=97.40%` vs `65/70=92.86%`，delta `+4.55pp`
  （95% CI `[-1.82pp,+11.51pp]`）；真实 fill `19/21=90.48%` vs `13/18=72.22%`，delta `+18.25pp`
  （95% CI `[-5.76pp,+43.75pp]`）。点估明显、统计不稳。
- 最后 5 天 fee-adjusted live ROI 为 `+$24.2100 / $188.0348 = +12.88%`；但最近 10 天合计仍是
  `-$2.7008 / $368.4163 = -0.73%`。不能用最后 5 天替代完整 recent window。
- 动作：`不改 live / 不加 size / 不把本轮命中率升级成 confirmed alpha`。继续按 terminal false cross、
  source→settlement basis 与 same-denominator market baseline 累积 forward。

## 固定分母与窗口

| 项目 | earlier | recent | 是否同口径 |
|---|---|---|---|
| 主 10 日窗口 | `2026-07-23..08-01` | `2026-08-02..08-11` | 是；各 10 个 target dates |
| 观感 5 日窗口 | `2026-08-02..08-06` | `2026-08-07..08-11` | 是；各 5 个 target dates |
| signal semantics | v2、`persistent_candidate_margin_v5` / `single_candidate_margin_v1` | 同左 | 是 |
| strong observation | 5 日 earlier `100%` | 5 日 recent `100%` | 是 |
| settlement | previous exact bracket YES 是否为 0 | 同左 | 是 |
| execution | canonical live_real BUY_NO expression；SELL exit 计入 net PnL | 同左 | 是 |
| fee | `fact_trades.pnl_usd_at_fill` canonical fee | 同左 | 是 |

这不是 probability model proper-score A/B；accuracy 只回答 source cross 最终是否离开 previous exact bracket，
不等于相对 market 的 alpha。

## 双漏斗

### Signal funnel（2026-07-09..08-11）

| 层 | grain | rows | 独立 target dates | 说明 |
|---|---|---:|---:|---|
| emitted runner journal | repeated event observation | 3,652 | 34 | 同一 bracket 可被 persistence observation 重复写入 |
| first CrossNO expression | city-date-previous bracket | 530 | 34 | 按 condition_id 取 first-seen |
| settled expression | expression | 528 | 34 | 2 个是 settlement coverage gap |
| current-policy recent 10d | expression | 147 | 10 | v2 + 当前两类 confirmation policy |
| latest 5d | expression | 77 | 5 | 75 correct / 2 terminal false |

### Evidence funnel（2026-07-09..08-11）

| 层 | expressions | 说明 |
|---|---:|---|
| settlement covered | 528 | canonical `settlement_outcomes` |
| first-event book present | 376 | coverage，不是策略筛除 |
| first-event ask≤policy cap | 142 | 价格可执行性近似；不声称完整 depth fill |
| actual live BUY_NO expression | 104 | canonical live_real；可能含 partial child fills |
| canonical fill fact rows | 151 | BUY/SELL child fills；不作为 accuracy 分母 |

## 三类归因

### 1. 工程/规则：解释了“比启动期好”，解释不了“这 5 天突然好”

启动 legacy era（7/9–14）全 signal 为 `111/121=91.74%`；7/15–22 hardening transition 已升到
`137/143=95.80%`，7/23–8/1 current-policy cohort 为 `112/117=95.73%`。这与真实工程变更方向一致：

- 7/12–15 引入 AMOS/persistent margin、连续 observation 与 false lower-bound 修正；
- 7/14 固定 Seoul primary runway 并校准 source profile；
- 7/21–24 统一 first-seen wake，Tokyo/Helsinki 收紧为 single `+0.7°C`，Seoul 使用 T-10 revalidation；
- 7/22 后 visible-depth sizing 和 fresh-book retry 改善实际执行。

因此，相对刚上线的旧链路，工程 hardening 很可能贡献了约 4pp 的 signal accuracy 基线改善。可是
8/2–6 和 8/7–11 已经是相同 schema、相同 confirmation policy、相同 strong-print 口径。8/10 新增
Amsterdam 也只有 1 个 actual fill；剔除 Amsterdam 后最后 5 天仍是 `18/20=90%`。所以本轮 5 日跳升
不能归因于新 selector 或新城市。

### 2. 数据/执行链：延迟更好，但 accuracy 没有同步结构性抬升

| telemetry | 8/2–6 | 8/7–11 | 判断 |
|---|---:|---:|---|
| schema v2 | 100% | 100% | 无语义变化 |
| source detect→runner p50 | 0.426s | 0.149s | 工程明显更快 |
| source observation lag p50 | 1.207m | 1.477m | 未改善 |
| first-event book coverage | 74.29% | 77.92% | 仅 +3.63pp |
| ask≤cap share | 25.71% | 23.38% | 反而略低 |

更快 wake/runner 和更稳 JRS/proxy 能提高“有没有及时看到、有没有盘口”的覆盖，但不会改变已经发生的 source
cross 最终是否被 settlement 保留。且最近 10 天 signal accuracy 没有比前 10 天提高，故不能把命中率抬升归因
到数据链。

另外发现一个反向证据：`2026-08-05..08-12` 的 60 条 CrossNO `fact_trades` rows 虽然 stable
`strategy_id` 正确，但 `instance_id` 全为 NULL。根因是 canonical runtime-order migration 从目录名
`fast_source_prev_no_trial` 推导 instance，而登记 identity 是 `fast_source_prev_no_trial_v1`；builder 在
instance 未命中时写 NULL，没有采用 raw row 已有的 `strategy_instance`。影响半径是任何按 instance 过滤的报告
都会把 8/5 以后整段漏掉；PnL/settlement 本身仍可按 stable strategy_id 正确读取。这个 metadata lineage 缺口说明
数据链不是“整体变好了”，本报告已按 strategy_id 纠偏，但未在本次只读分析中改生产数据。

### 3. 客观天气 regime：是最后 5 天抬升的主要解释，但仍只是短窗

前 5 天有 5 个 terminal false city-days / 21 city-days（23.81%）；最后 5 天只有 2/26（7.69%）。
前窗 5 个实际亏损表达中，3 个集中在 8/5，且横跨 Busan 35、Helsinki 21、Tokyo 30；这更像同日多城的
边缘 cross/回落天气实现，而不是单城工程故障。最后 5 天的两个失败都在 Busan（8/7 37、8/11 31）。

最近窗口 first-cross margin p50 从 `+0.8` 升到 `+0.9` native units，方向上也符合更干净的跨档；但
multi-cross city-day share 反而从 `90.48%` 降到 `76.92%`，local first-cross hour 几乎不变
（10.53→10.60）。因此只能说“最近 terminal retention 更有利”，不能把它升级成已经识别出的稳定天气 regime。

## Fee-adjusted execution

| target_date window | expressions | win by count | win by BUY notional | fees | net PnL | net ROI |
|---|---:|---:|---:|---:|---:|---:|
| 8/2–6 | 18 | 13/18 = 72.22% | 77.89% | $1.2693 | -$26.9108 | -14.92% |
| 8/7–11 | 21 | 19/21 = 90.48% | 98.04% | $1.1152 | +$24.2100 | +12.88% |
| 8/2–11 | 39 | 32/39 = 82.05% | 88.18% | $2.3845 | -$2.7008 | -0.73% |
| 7/23–8/1 | 32 | 27/32 = 84.38% | 91.50% | — | +$26.2229 | +8.68% |

8/4 后新增的 Busan routine-nonconfirmation exit 会改变 net PnL 路径，但不改变这里的 settlement correctness；
不能把 exit 工程优化冒充天气方向预测变准。

## Probability / market baseline

本次没有可用的同 rows market probability proper-score baseline。`previous NO` 的高正确率很大部分是跨过较低
bracket 后的 base rate；尤其同一 city-day 多档相关，不能把 95% accuracy 当成 95% alpha。market baseline、
source→settlement probability head 和 fee-adjusted selection uplift 三者仍未过门。

## Forward / multiple testing

- train selection：无；窗口按用户“最近明显变好”的观察事后定义。
- frozen forward：NA；8/7–11 是观察窗，不是预注册 holdout。
- variants K：4 个窗口摘要（5d/10d × earlier/recent）；未做多重检验校正。
- uncertainty：按 `target_date` block bootstrap 20,000 次，seed `20260812`。

## 三门与 8 环

```text
significance=FAIL
baseline=FAIL
forward=NA
conclusion=inconclusive / keep current probe only / no live change
```

覆盖：①描述性绩效、②target-date bootstrap、⑤实际 fill/fee、⑦同日相关性、⑧base-rate 诊断。
缺：③排序能力、④proper probability、⑥容量、⑧same-row executable market baseline。因此不允许用这份报告
支持增仓或扩大 live。

最终一句：在 `2026-08-07..11` first-expression 分母，CrossNO 相对 `2026-08-02..06` 的 signal accuracy
delta 为 `+4.55pp`（95% CI `[-1.82pp,+11.51pp]`），fill accuracy delta 为 `+18.25pp`
（95% CI `[-5.76pp,+43.75pp]`），forward `NA`，结论 `inconclusive`，动作 `不改 live`。

## 复现

```bash
.venv/bin/python scripts/analysis/live_performance/weather_cross_no_accuracy_attribution_v1.py
.venv/bin/python -m pytest -q tests/research_tests/test_weather_cross_no_accuracy_attribution_v1.py
```

紧凑 artifact：
`docs/analysis/2026-08/generated/cross_no_accuracy_attribution_20260812_v1/{summary.json,signal_expressions.csv,fill_expressions.csv}`。
