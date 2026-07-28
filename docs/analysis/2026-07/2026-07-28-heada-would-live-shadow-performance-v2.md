# HeadA 尾部彩票 Would-Live Shadow 绩效 v2

> 窗口：target_date `2026-07-16..2026-07-28`
> 策略身份：`low_price_yes_lottery_tiny_live_v1` / `forecast_quality.low_price_yes_lottery` / zero-notional shadow
> 主绩效有效窗：`2026-07-16..2026-07-25`；`2026-07-26..28` 因 fresh-book 链路失败不进入绩效分母

## 数据快照

| 项目 | 值 |
|---|---|
| 数据源 | `would_live_entries.jsonl` + `shadow_decisions.jsonl`；canonical `runtime/weather.db.settlement_outcomes` |
| 数据快照时间 | DB mtime `2026-07-28T15:47:11+08:00`；评估生成 `2026-07-28T07:47:28Z` |
| canonical 覆盖 | `settlement_outcomes` 到 target_date `2026-07-27`，40,714 rows；本轮增量补入 7/24–27 共 1,880 bracket outcomes |
| captured / intended rows | 104 captured would-live；84 个重算后满足 approved `dist>0` |
| 独立 target dates / cities | intended cohort 8 dates / 35 cities |
| unsettled / missing settlement | `0 / 0` |
| actual shadow order / fill | `0 / 0`；这是 would-live opportunity，不是真实成交 PnL |
| CLOB coverage gate | `NA`，没有发布 `live_real` 指标 |
| fee | 假设 taker at captured fresh best ask；Weather 官方曲线 `0.05*p*(1-p)*shares` |
| runtime raw 覆盖 | would-live 最后写入 `2026-07-24T13:13:49Z`（target 7/25）；shadow decision 最后写入 `2026-07-27T22:02:36Z`（target 7/28） |

本轮先同步 Mac current raw，再对 7/24–27 执行增量 settlement backfill、canonical ingest 和最近 event-date partition materialization；未运行全量 rebuild。分析 freshness 仍对 decision snapshot age 报 warning，但 settlement 已覆盖本绩效窗口。

## 结论与动作

策略的 fee-adjusted point estimate 仍为正，但没有成为 confirmed alpha：

```text
intended dist>0 cohort = 84 tickets / 8 target dates
wins = 12; win rate by count = 14.3%; win rate by entry notional = 17.2%
fee-adjusted PnL = +$9.17 on $50.83 cost; ROI = +18.0%
target-date block-bootstrap 95% CI = [-47.2%, +71.9%]
same-row market-fee baseline ROI = -4.2%
excess ROI = +22.2pp; 95% CI = [-43.0pp, +76.0pp]

significance=FAIL
baseline=FAIL
forward=FAIL_LOW_SAMPLE
conclusion=inconclusive
action=不恢复 live，不根据 source/edge 切片改 selector 或 sizing；先恢复一条干净、可执行的 shadow 证据链
```

7/24–25 是上一版报告后的 frozen extension：25 张、4 胜、ROI `+32.1%`，但只有两个日期，日期重采样区间约为 `[-100.0%, +93.7%]`；7/24 全亏、7/25 高盈利，显示的仍是彩票 payout 波动而不是稳定性。

## Target metric 与固定分母

- unit/grain：每个 `signal_id` 首个 would-live entry；绩效按 `target_date` block。
- universe：当前 runner 的 frozen `ask 0.05..0.20`、raw edge `>=0.20`、fee edge `>=0.15`、`hts 22..24`，并在评估时重算 approved `forecast_to_bracket_low_native > 0`。
- label：canonical `(city,target_date,bracket)` `settlement_outcomes.final_price`。
- price：捕获时 fresh best ask；5 shares；entry taker fee 已扣。
- 主指标：fee-adjusted ROI、win rate、PnL；概率层以同 rows market ask 为基准。
- forward：7/16–23 为首报窗口；7/24–25 为不调参 extension；7/26–28 因 execution coverage gap 排除。

## 每日结果

“单数”是 would-live tickets，不是真实下单。`captured` 包含运行时误收的 `dist<=0`；主绩效只看 `intended`。

| target_date | captured | intended `dist>0` | wins | win rate | cost | PnL | fee-adjusted ROI | 说明 |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| 2026-07-16 | 8 | 8 | 2 | 25.0% | $5.32 | +$4.68 | +87.8% | valid |
| 2026-07-17 | 0 | 0 | — | — | — | — | — | 无 would-live |
| 2026-07-18 | 1 | 0 | — | — | — | — | — | 唯一 captured 为 `dist=0` |
| 2026-07-19 | 5 | 5 | 1 | 20.0% | $2.51 | +$2.49 | +99.4% | valid |
| 2026-07-20 | 12 | 11 | 3 | 27.3% | $7.84 | +$7.16 | +91.3% | 1 张 `dist<0` 剔除 |
| 2026-07-21 | 14 | 9 | 0 | 0.0% | $4.98 | -$4.98 | -100.0% | valid cohort 全亏 |
| 2026-07-22 | 20 | 15 | 2 | 13.3% | $9.39 | +$0.61 | +6.5% | 5 张 `dist<0` 剔除 |
| 2026-07-23 | 15 | 11 | 0 | 0.0% | $5.66 | -$5.66 | -100.0% | valid cohort 全亏 |
| 2026-07-24 | 11 | 9 | 0 | 0.0% | $4.81 | -$4.81 | -100.0% | valid cohort 全亏 |
| 2026-07-25 | 18 | 16 | 4 | 25.0% | $10.32 | +$9.68 | +93.7% | active prod 仍误收 2 张 `dist<=0` |
| 2026-07-26 | 0 | NA | NA | NA | NA | NA | NA | 25 个独立候选全部 book timeout |
| 2026-07-27 | 0 | NA | NA | NA | NA | NA | NA | 16 个独立候选全部 book timeout |
| 2026-07-28 | 0 | NA | NA | NA | NA | NA | NA | 8 个独立候选全部 book timeout |

## Signal funnel

| 层 | grain | rows | dates | 说明 |
|---|---|---:|---:|---|
| captured would-live | first `signal_id` | 104 | 9 | 运行时 journal 捕获 |
| intended mechanism | first `signal_id`, `dist>0` | 84 | 8 | 主绩效固定分母 |
| frozen extension | first `signal_id` | 25 | 2 | 7/24–25，不调参 |
| post-7/25 pre-book candidates | unique candidate | 49 | 3 | 25/16/8，均未取得 fresh book |

## Evidence funnel

| 层 | grain | rows | dates | coverage gap |
|---|---|---:|---:|---|
| PIT forecast / selector | intended signal | 84 | 8 | active runtime 未真正 enforce geometry |
| PIT fresh quote | intended signal | 84 | 8 | 7/26–28 的 49 candidates 全部缺 quote |
| canonical settlement | intended signal | 84 | 8 | 0 missing |
| hypothetical taker expression | intended signal | 84 | 8 | 按 ask + 官方 fee |
| actual fill | fill | 0 | 0 | zero-notional shadow，预期为 0 |

## Probability / ranking quality

| candidate | rows | mean p | realized | Brier | logloss | AUC |
|---|---:|---:|---:|---:|---:|---:|
| HeadA raw `model_p_yes` | 84 | 39.2% | 14.3% | 0.190 | 0.569 | 0.594 |
| same-row fresh market ask | 84 | 11.6% | 14.3% | 0.118 | 0.387 | NA |

市场在同一批票上的 Brier 和 logloss 都明显更好。HeadA 仍严重 overconfident，raw `model_p_yes-ask` 不能解释成已验证 residual，也不支持 probability/edge sizing。

## Fee-adjusted trade performance

| slice | rows | wins | dates | cash cost | fees | PnL | ROI | 95% CI |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| BUY YES intended `dist>0` | 84 | 12 | 8 | $50.83 | $2.12 | +$9.17 | +18.0% | [-47.2%, +71.9%] |
| same-row market-fee baseline | 84 | — | 8 | $50.83 | $2.12 | -$2.12 | -4.2% | — |
| excess vs baseline | 84 | — | 8 | $50.83 | — | +$11.29 | +22.2pp | [-43.0pp, +76.0pp] |

Maker-limit 的 price-only 反事实为 `+41.0%`，但没有 queue、fill probability 或 adverse-selection 证据，不作为主结果。

## 新发现与运行态完整性

### 1. 7/26–28 是 execution coverage gap，不是 0 信号

`shadow_decisions.jsonl` 显示 49 个独立候选全部在 fresh-book 阶段以 `ConnectTimeout` 失败；重复轮询产生 565 次失败记录。它们没有可执行 ask，不能进入胜率/ROI。当前代理请求已恢复可达，但历史决策时刻盘口不可回填。

### 2. 7/24 的 `dist>0` 修复没有部署到实际 shadow checkout

活跃子进程从 `/Users/deepsleep/projects/pm_agents_prod`（SHA `cb043e47`）运行，而本仓库为 SHA `21ce08ac`。两份 runner hash 不同，prod 脚本缺少 `bracket_distance_features` materialization。

污染窗口 `2026-07-16..25` 共 20 张 `dist<=0` captured tickets（3 胜、17 负，captured-only ROI `+4.3%`）。其中 7/25 又新增：

- MexicoCity `29 YES`，`dist=0`，负。
- Busan `36 YES`，`dist=-0.6`，负。

这 20 张已从主绩效剔除，但运行中的 shadow 证据链本身仍未修复。

### 3. Source split 仍尖锐，但不可升级为 gate

| source | rows | wins | ROI |
|---|---:|---:|---:|
| ECMWF assigned | 54 | 11 | +70.8% |
| GFS assigned | 30 | 1 | -73.2% |

只有 8 个独立 target dates，且这是同一轮 post-hoc 的两个 source slices（K=2，未做多重检验校正）。这个差异是后续 frozen telemetry 的诊断重点，不是现在加 GFS ban 的依据。

### 4. 当前 data-feed 健康不是全绿

7/28 15:30 北京时间的 snapshot/orderbook/forecast curves 已恢复新鲜，但生产 health check 仍为 `fail`：fast observation state stale，observation cache 有 4 个美国西部城市 fetch failure；HeadA 当日还经历过 JRS snapshot 不可见和 stale snapshot 窗口。故 7/26 之后不具备连续 shadow coverage。

## Forward、稳健性与三门

| 检查 | 结果 |
|---|---|
| frozen extension | 7/24–25 ROI +32.1%、excess +36.3pp，但只有 2 日期且一负一正，FAIL_LOW_SAMPLE |
| target-date block bootstrap | 全窗 ROI / excess CI 均跨 0，FAIL |
| top-ticket removal | ROI 仍为 +9.2%，但不能修复宽 CI 与 proper-score 失败 |
| multiple testing | source K=2，未校正，只作探索性描述 |
| coverage sensitivity | 7/26–28 无 fresh quote，排除后只剩 8 个有效日期 |

| 门 | PASS/FAIL/NA | 证据 |
|---|---|---|
| significance | FAIL | ROI 与 excess ROI 日期 CI 均跨 0 |
| same-denominator baseline | FAIL | market ask 的 Brier/logloss 明显优于 HeadA |
| forward | FAIL | extension 仅 2 日期；运行链从 7/26 起缺可执行覆盖 |

8 环覆盖：已覆盖描述性绩效、日期 bootstrap、AUC、Brier/logloss、captured ask、target-date 聚类和 market baseline；缺 actual fill/maker queue、容量与连续 forward execution coverage。

最终判断：在 `2026-07-16..25` 的 84 张 intended `dist>0` would-live tickets 上，HeadA 相对同 rows market-fee baseline 的超额 ROI 为 `+22.2pp`（95% CI `[-43.0pp,+76.0pp]`），forward `FAIL_LOW_SAMPLE`，结论 `inconclusive`；保持 zero-notional，且在恢复干净 runtime 前不能把后续“0 单”当策略证据。

Artifacts（本地生成、默认 gitignored）：`docs/analysis/2026-07/generated/heada_would_live_shadow_v2/`。Evaluator：`scripts/analysis/forecast_quality/evaluate_heada_would_live_shadow_v1.py`。
