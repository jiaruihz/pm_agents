# Current-YES 模型地图与版本主线

日期：2026-06-21
状态：当前人工阅读入口 / 主线梳理

## 一句话主线

这条方向是 **current-YES / no-reheat**：当某城市当前 running max 所在温度 bracket 看起来能守到结算时，买这个 bracket 的 YES。

现在的问题不是“有没有模型”，而是模型、规则、执行、LLM 审单混在了一起。正确拆法是：

```text
weather / market snapshot
  -> 状态头：fade-confirmed 或 peak-forming
  -> 概率模型 / 规则阈值
  -> 确定性执行 guard
  -> 可选 LLM/Codex 下单前审单
  -> CLOB order
```

核心区分：

- **概率/规则模型**：判断这笔 current-YES 是否有足够 EV。
- **执行 guard**：挡 stale observation、METAR 更新前后竞态、fresh book 变差、重复敞口。
- **LLM/Codex preflight**：最后一层人工式 reviewer；它不是基础概率模型，目前也没有启用 live blocking。

## 数据快照

本文件更新前已重新同步并重建：

- `scripts/ops/sync_weather_remote.sh`：完成，最新 market snapshot 到 `2026-06-21 22:30` 北京时间附近。
- `scripts/weather_dashboard/run_stack.sh`：完成，`runtime/weather.db` 已重建。
- `fact_trades`：4400 rows，`fact_built_at_utc=2026-06-21T14:54:43.916236+00:00`。
- `fact_signal_candidates`：34677 rows，event_date 覆盖 `2026-05-05`..`2026-06-23`。
- CLOB fill coverage gate：`gate_pass=true`，DB/cache fill cost 差异为 0。

注意：current-YES split 实例的 `strategy_instance` 不在 `fact_trades` 字段里；下面实盘分实例结果来自 raw live order JSONL，并用 `runtime/weather.db:settlement_outcomes` join 结算。

## 现在真正 live 的是什么

N100 当前跑的是 split live，两条 profile 分开记账：

| instance | entry profile | live 角色 | 概率来源 | 当前状态 |
|---|---|---|---|---|
| `theta_current_yes_fade_confirmed_tiny_live_v1` | `fade_confirmed` | 原始 current-YES live sleeve：看到 running max 后真实回落再买 | 默认 base v8/v9 logistic artifact | live tiny-size |
| `theta_current_yes_peak_forming_micro_tiny_live_v1` | `peak_forming_micro` | 仍在 running max 附近时尝试提前买 | 同一 base v8/v9 概率层 + peak-forming guard | live micro-probe / fragile |

N100 当前 caps（2026-06-21 22:56 北京时间附近远端读取）：

| 项 | fade-confirmed | peak-forming |
|---|---:|---:|
| max order notional | 1.5 | 1.5 |
| max city-day notional | 1.5 | 1.5 |
| max taker cushion | 0.02 | 0.02 |
| max obs age | 20 min | 20 min |
| pre-METAR update blackout | 6 min | 6 min |
| min forecast peak hour local | 12 | 12 |
| LLM preflight | disabled | disabled |
| LLM mode | advisory | advisory |
| prompt version if enabled | `current_yes_codex_v2_veto_loss_detector` | same |

当前实盘 LLM 没有启用。N100 也没有 Codex CLI，所以不能在 N100 上直接跑 Codex 审单。

## 最近实盘结果

窗口：split current-YES live 从 `2026-06-18` 到 `2026-06-21` 的 raw live orders。

官方已结算口径只统计 `settlement_outcomes` 已有 settled 的 matched orders；`2026-06-21` 的 12 笔 matched 目前在本机 settlement table 仍是 missing，不计入正式 win/ROI。

| profile | raw orders | matched | settled matched | W-L | settled PnL | settled cost | ROI |
|---|---:|---:|---:|---:|---:|---:|---:|
| fade-confirmed | 8 | 6 | 3 | 3-0 | +$1.972134 | $11.00 | +17.93% |
| peak-forming micro | 31 | 30 | 21 | 15-6 | -$5.685329 | $91.00 | -6.25% |
| total | 39 | 36 | 24 | 18-6 | -$3.713195 | $102.00 | -3.64% |

按 target date：

| target_date | matched | settled | W-L | PnL | ROI | unsettled |
|---|---:|---:|---:|---:|---:|---:|
| 2026-06-18 | 1 | 1 | 1-0 | +$1.747638 | +34.95% | 0 |
| 2026-06-19 | 2 | 2 | 2-0 | +$3.324456 | +33.24% | 0 |
| 2026-06-20 | 21 | 21 | 15-6 | -$8.785290 | -10.10% | 0 |
| 2026-06-21 | 12 | 0 | NA | NA | NA | 12 |

已结算亏损全部来自 `peak_forming_micro`，集中在 `2026-06-20`：

| time UTC | city | bracket | price | notional | loss |
|---|---|---:|---:|---:|---:|
| 2026-06-20 05:36 | Busan | 27 | 0.721 | 5.0 | -5.0 |
| 2026-06-20 06:03 | KualaLumpur | 31 | 0.631 | 5.0 | -5.0 |
| 2026-06-20 07:00 | Wuhan | 31 | 0.491 | 5.0 | -5.0 |
| 2026-06-20 08:30 | Lucknow | 40 | 0.741 | 5.0 | -5.0 |
| 2026-06-20 08:48 | Karachi | 34 | 0.771 | 5.0 | -5.0 |
| 2026-06-20 17:04 | BuenosAires | 15 | 0.481 | 3.0 | -3.0 |

`2026-06-21` 未结算 matched 中，Wuhan 两笔最关键：

| time UTC | profile | city | bracket | price | notional | 当前判断 |
|---|---|---|---:|---:|---:|---|
| 2026-06-21 03:03 | peak-forming | Wuhan | 28 | 0.711 | 3.0 | 观测上已被后续 29 打穿，官方结算本机暂未入表 |
| 2026-06-21 03:10 | fade-confirmed | Wuhan | 28 | 0.711 | 3.0 | 同上 |

这说明最近实盘的坏味道不只是“同一城市重复买”，而是：两个 profile 虽然信号形态不同，但最终押的是同一个 proposition：`28C` 是否守住。

## 已发现的问题：执行问题 vs 模型问题

### 执行 / 规则 / 架构问题

| 问题 | 例子 | 性质 | 当前处理 |
|---|---|---|---|
| observation stale race | Wuhan 2026-06-21 第一笔 peak-forming，下单时 obs 已接近/超过预期下一报文窗口 | 执行时序问题 | 已加 `snapshot_rule_peak_forming_stale_after_expected_obs` |
| forecast peak 过早 | Wuhan forecast peak 在 11:00 左右，和城市日内高温常识冲突 | 规则/特征解释问题 | 已加 `snapshot_rule_forecast_peak_too_early`，默认 `min_forecast_peak_hour_local=12` |
| split profile 重复押同一 city/date/bracket | Wuhan 2026-06-21 peak + fade 连续买同一 `28` | portfolio exposure / dedupe 问题 | 还没有彻底解决；当前 size 已降到 1.5，但需要跨 profile 的 shared cap / same proposition cap |
| peak-forming size 过大 | 2026-06-20 多笔 peak-forming 仍是 5 notional 级别，亏损集中 | sizing / rollout 问题 | 已降到 1.5/order 和 1.5 city-day |
| sync 默认不含 split runtime latest_summary | 本机 `remote_pm_agent` split summary 一度旧于 N100 | 运维/镜像问题 | 本文直接从 N100 读 current caps；后续应把 split dirs 加进 sync |
| N100 无 Codex CLI | 无法在 N100 直接跑 Codex preflight | 部署架构问题 | 不建议在 N100 登录 Codex；应做 Mac-side preflight queue |

### 模型 / 信号问题

| 问题 | 说明 | 当前处理 |
|---|---|---|
| peak-forming 与 fade-confirmed 是两个状态头，不能混成一个胜率 | peak-forming 更早、更便宜，但 reheat 风险大；fade-confirmed 更稳但也会遇到 false fade | 已 split live，但模型层还需要更明确的状态建模 |
| 早盘/中午回落不是“高温已结束” | Wuhan 11:00 回落、Singapore 早段 humid/cloudy dip 都说明 decline 本身不够 | `decline/fade modes v1` 已指出 mature fade vs false fade；还未进 live model |
| market 已经吃掉大部分明显信息 | v11 registry 显示 raw market ask 在 live-like slice 很强 | 后续模型必须打赢 market 和 market_iso，而不是只打赢旧 v9 |
| hazard v2 有方向但未过交易门 | peak-forming hazard v2 比 market-only 方向更好，但 ROI CI 仍跨 0 | 保留 research/shadow，不替换 live |
| LLM v3 能抓一部分坏单，但不是稳定模型 | v3 抓到 4/6 个已结算输单，但 Wuhan 第二笔 fade 仍放过 | 只能 advisory / reviewer，不能 live block |

## 版本家族

### A. Base current-YES replay / live gate

| version | 目的 | 数据 / holdout | 关键结果 | 决策 |
|---|---|---|---|---|
| `v8 full replay` | 建干净的 current-YES 全量机会分母 | 3239 rows，1527 train / 1712 holdout，2026-05-19..2026-06-14 | `weather_plus_price` 有判别力；best fixed rule 强，但 prefix walk-forward 弱 | 作为后续模型的 feature source |
| `v9 live gate` | 冻结一个可 tiny-live 的 fade-confirmed 规则 | 原始 live-like holdout 约 31 orders / 11 days | YES ROI 约 +16% 到 +18%，通过当时 tiny-live gate | 升 tiny-live，不放量 |
| `v11 model registry` | 停止临时命名，统一比较 market、market_iso、live v9、residual、HGB | 1712 holdout rows / 14 dates；live-like 278 rows / 13 dates | live-like 里 raw market ask 很强，v9 合理但没明确打赢 market | 作为模型治理层，不替换 live |

人话：**v9 是当前 live base，但 v11 规定以后不能只和旧 v9 比，必须打赢 market。**

### B. Forecast clock / peak timing

| version | 目的 | 关键结果 | 决策 |
|---|---|---|---|
| `v12 forecast clock model` | 把 forecast peak timing 作为模型特征 | 点估有变化，但 live-like 里没有稳定打赢 market ask / v9 | research only |
| `v13 observation/execution guard` | 测 obs age、METAR blackout、minutes since max | 半小时 replay 证明不了收益，但这些仍是事故防护 | 风控/telemetry guard，不是 alpha proof |
| `v14 forecast peak scorecard` | 比较 v9 fade、after-peak filter、early peak-forming | forecast clock 能解释风险形状，但过滤会缩样本；peak-forming 不稳 | forward telemetry required |
| `v15 live readiness` | 判断 forecast peak clock 能否推动 live upgrade | historical v9 过，forecast upgrade/forward telemetry 不过 | 保持 v9 tiny-live / telemetry |

人话：**forecast clock 是上下文和风险特征，还不是 promoted model。**

### C. Fade-confirmed branch

| version | 目的 | 关键结果 | 决策 |
|---|---|---|---|
| `fade_confirmed specialist v1` | 只在已回落样本上训练 specialist | base model holdout live-like ROI 约 +18.3%，specialist 约 +13.7% | specialist shadow only，live 默认 base |
| `decline/fade modes v1` | 区分 mature fade 和 false fade | `h15-21 + decline>=0.5C + minutes_since_running_max>=90` 更像真 fade；早段 humid/cloudy dip 是 trap | 加入研究/shadow 特征，不做 live hard rule |

人话：**fade-confirmed 仍是最干净的 live head，但不是所有 decline 都代表当天高点结束。**

### D. Peak-forming branch

Peak-forming 是“温度仍在 running max 附近”就买，比 fade-confirmed 更早，价格可能更好，但下午 reheat 风险更大。

| version | 目的 | 关键结果 | 决策 |
|---|---|---|---|
| `peak-forming v1` | 第一次拆 peak-forming vs post-decline | broad peak-forming 弱，`h13` 早段有点意思 | shadow/probe only |
| `peak-forming hazard v1` | 第一版 survival hazard | 已被 v2 supersede | research only |
| `peak-forming hazard v2` | 用 market、METAR、forecast clock、GFS/ECMWF gap、plateau 特征预测 current high 是否守住 | 判别力改善，但交易 ROI CI 仍跨 0 | shadow/research，不替换 live |

hazard v2 holdout 对比：

| 模型 / 规则 | holdout dates | orders | win | ROI | 95% date bootstrap |
|---|---:|---:|---:|---:|---:|
| market only | 17 | 559 | 78.4% | -5.2% | [-11.5%, +1.7%] |
| 当前 live base v9 peak rule | 17 | 234 | 79.5% | -1.0% | [-12.1%, +9.5%] |
| hazard v2 peak rule | 17 | 283 | 80.6% | -0.9% | [-8.9%, +7.5%] |
| hazard v2 stalled peak | 17 | 133 | 79.7% | -0.3% | [-12.1%, +10.5%] |
| train-selected h13 p>=0.75 edge>=0.08 | 17 | 121 | 77.7% | +1.3% | [-14.6%, +15.1%] |

命名澄清：

- 表里的“当前 live base v9 peak rule”不是原始 live v9 fade-confirmed 规则。
- 它是在 peak-forming holdout population 上，用当前 live base probability artifact 做 baseline。
- 原始 live v9 是 fade-confirmed 分支，分母更小、更干净。

人话：**hazard v2 比 market-only peak-forming 方向更好，但还不够替换 live。**

### E. Residual / weather feature challengers

| version | 目的 | 关键结果 | 决策 |
|---|---|---|---|
| `proper-form tail features v1` | 用正确 target form 测 tail weather features | 天气有信号，但 market/base 后 residual edge 变薄 | 不改 live |
| `residual calibrator + alti v1` | 测 `market + METAR core`、气压 `alti/d_alti_3h`、非线性模型 | 最佳 logistic 点估改善 logloss，但日期 bootstrap CI 跨 0；HGB 退化 | research only |

人话：**天气特征不是没用，难点是 market 已经价格化一部分显而易见的信息。**

## 现在有哪些规则判定

### 两个 profile 共用的执行/风控规则

- local live window：默认 `13-15`。
- max order notional：当前 N100 为 `1.5`。
- max city-day notional：当前 N100 为 `1.5`。
- max obs age：`20 min`。
- pre-METAR-update blackout：`6 min`。
- forecast peak 太早 veto：`forecast_peak_hour_local >= 12`。
- fresh CLOB：fresh ask 不能比 snapshot ask 贵超过 `0.02`。
- cross tick buffer：`0.001`。
- d1 sibling / market structure 必须可见，否则跳过。
- duplicate / city-day cap：当前是 profile 内约束；跨 profile 同 city/date/bracket proposition cap 仍需补。

### fade-confirmed 当前规则

- `decline_c >= 0.5C`。
- `yes ask >= 0.55`。
- `p_yes_win >= 0.5`。
- `edge >= 0.05`。
- model mode 默认 `base`，不是 specialist。

### peak-forming 当前规则

- `decline_c <= 0.25C`。
- `0.50 <= yes ask <= 0.97`。
- `p_yes_win >= 0.60`。
- `edge >= 0.02`。
- `minutes_since_running_max >= 10`。
- stale-after-expected-obs hard reject。
- forecast peak too early hard reject。

## 哪些规则更适合进入模型层

现在不少东西是“为了安全先用 hard rule 挡住”。长期看，其中一部分应该进入模型/状态层，而不是永远写死：

| 当前形态 | 更好的模型层表达 |
|---|---|
| `forecast_peak_hour_local >= 12` hard rule | `forecast_remaining_max`、`reheat_after_now`、peak hour delta、forecast model agreement 作为连续特征 |
| `decline_c >= 0.5` | mature fade vs early false fade 状态分类：local hour、minutes since max、decline depth、湿度/云/风 |
| `minutes_since_running_max >= 10` | plateau duration、first max touch hour、last max touch age、plateau_obs_count |
| obs age / next obs blackout | source freshness risk、cadence phase、minutes_to_next_obs、source profile reliability |
| peak-forming ask/edge threshold | hazard model 输出 `p_survive` 后做 expected value / sizing，而不是固定 one-size threshold |
| city-day cap | portfolio layer 的 same-proposition exposure：city/date/bracket across profiles |

仍然应该保留为 hard guard 的东西：

- fresh book 变差 / depth 不足；
- observation 明显 stale；
- METAR 更新前后竞态；
- 资金上限、重复下单、真实 CLOB 安全边界。

## LLM / Codex 怎么用

LLM preflight 不是和 hazard v2 竞争的概率模型。它比较的是真实 would-trade / matched order baseline，角色是“下单前 reviewer”。

它所在环节：

```text
candidate passed deterministic rules
  -> fresh CLOB quote accepted
  -> city-day cap accepted
  -> LLM preflight
  -> plan/order
```

当前 prompt 版本结果：

| prompt version | 角色 | 2026-06-18..2026-06-21 matched replay 结果 | 状态 |
|---|---|---|---|
| `current_yes_codex_v1_reheat_guard` | 保守 reheat reviewer | 保留 9-1 settled，ROI +16.2%，但 veto precision 只有 35.7%，误杀 9 个赢家 | 太激进 |
| `current_yes_codex_v2_veto_loss_detector` | 只 veto 明确输单 | 保留 12-4 settled，ROI -6.9%，只抓到 2/6 个输单 | 太弱 |
| `current_yes_codex_v3_price_aware_veto` | price/edge-aware reviewer | 保留 15-2 settled，ROI +15.3%，抓到 4/6 个输单，误杀 3 个赢家 | 最好但仍未 live-ready |

Wuhan 2026-06-21 诊断：

- v3 会 veto stale 的 `peak_forming_micro`。
- v3 仍会 allow 后一笔 fresh `fade_confirmed`。
- 说明 LLM 没有彻底学会“11:00 回落不等于全天高点结束”。

实盘建议：

- 不要在 N100 登录 Codex。
- 不要每条天气报文全量跑 LLM。
- 只对 would-trade candidate 跑：规则已过、fresh quote 已过、cap 已过，准备下单前。
- 做 Mac-side preflight queue：N100 写 pending candidate，Mac 本地 Codex 读、判断、写回 decision。
- 初期只 advisory/telemetry；只有当它能稳定抓住武汉这类 false-fade / stale-race，才讨论 `block_veto`。
- 缓存 key 应至少包含：`city/target_date/bracket/obs_ts/forecast_hash/price_bucket/profile`。

## 当前 promotion status

| component | status | 原因 |
|---|---|---|
| `fade_confirmed` base v9 tiny-live | live tiny-size | 原始 fixed rule 通过 tiny-live gate；最近 split live 仍小样本正收益 |
| `peak_forming_micro` | live micro-probe / fragile | 最近已结算亏损全部来自它；需要继续降 size / 强化模型和 shared cap |
| `fade specialist v1` | shadow only | holdout live-like ROI 输给 base |
| `forecast clock model v12` | research only | 没有稳定打赢 v9/market |
| `observation guard v13` | risk guard / telemetry | 是事故防护，不是 replay 已证明 alpha |
| `forecast peak scorecard v14` | scorecard / telemetry | 能解释风险，不是 promoted rule |
| `hazard v2 peak-forming` | research / shadow candidate | 模型形态更好，但 ROI CI 跨 0 |
| `residual + alti` | research only | 点估改善，CI 未过 |
| `Codex preflight v1-v3` | code deployed, live disabled | v3 有用，但样本太小且漏掉 Wuhan fade case |

## 推荐主线

1. 保留 split live，但把风险讲清楚：
   - `fade_confirmed` 是当前最干净 sleeve。
   - `peak_forming_micro` 只是 micro-probe，不是可放量策略。

2. 先补 portfolio exposure：
   - 跨 profile 的 same city/date/bracket cap。
   - 同一 proposition 只能有一个主仓位，不要因为 profile 分裂而重复下注。

3. 把 peak-forming 当作主研究前线：
   - hazard v2 继续作为候选概率层。
   - 但 promotion 必须等 forward window ROI 和 date-bootstrap CI 过门。

4. 把 mature fade / false fade 正式做成状态特征：
   - `local_hour`
   - `minutes_since_running_max`
   - `forecast_remaining_max`
   - `reheat_after_now`
   - humidity/cloud/wind regime
   - source freshness / cadence phase

5. LLM 只做 reviewer：
   - 下单前审单；
   - 不替代概率模型；
   - 不在 N100 上跑 Codex；
   - 不启 live block，先 advisory/telemetry。

6. 每个未来模型必须写清楚它改的是哪一层：
   - probability model；
   - state/rule head；
   - execution guard；
   - portfolio exposure；
   - LLM reviewer。

## Source reports

- `2026-06-16-theta-yes-current-full-replay-v8.md`
- `2026-06-16-theta-yes-current-live-gate-v9.md`
- `2026-06-17-theta-current-yes-model-registry-v11.md`
- `2026-06-17-theta-current-yes-forecast-clock-model-v12.md`
- `2026-06-18-theta-current-yes-observation-execution-guard-v13.md`
- `2026-06-18-theta-current-yes-forecast-peak-scorecard-v14.md`
- `2026-06-18-theta-current-yes-live-readiness-v15.md`
- `2026-06-18-current-yes-fade-confirmed-specialist-model-v1.md`
- `2026-06-20-current-yes-residual-calibrator-alti-v1.md`
- `2026-06-21-current-yes-peak-forming-hazard-v2.md`
- `2026-06-21-current-yes-decline-fade-modes-v1.md`
- `2026-06-21-current-yes-codex-prompt-version-comparison-v1.md`
