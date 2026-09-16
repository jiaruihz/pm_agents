# 绩效分析：d1 YES high-mid 全历史 regime 审计 v1

> 窗口：历史 PIT replay `2026-05-19..2026-07-08`；clean live forward `2026-07-16..2026-07-21`  
> 策略身份：instance `d1_yes_high_mid_live_v1` / strategy `live_weather_edge_v1_df8eb1679a26` / run `pm_agent_local_strategy_runtime_d1_yes_high_mid_live_v1_live`  
> evidence layer：PIT intraday regime atlas + current Mac raw runner + canonical `runtime/weather.db`

## 结论与动作

`d1 YES mid>=0.80` 不是一个已经找到稳定 weather regime 的 alpha。历史 first-signal 分母 218 个 city-day / 48 dates，fee-adjusted ROI `+2.24%`，date-block 95% CI `[-0.90%,+5.07%]`；clean live forward 排除 7/19 observation-continuity 事故后，9 个 bounded city-day / 6 dates 只有 `5/9` 命中，按首次 signal executable ask 回放 ROI `-36.62%`，表面 CI `[-76.54%,-0.53%]`。真实 clean fills 为 12 fills / 8 city-day，PnL `-$16.6036`、ROI `-32.29%`，CI `[-76.74%,+6.25%]`。

regime 维度没有产生可上线的分离：10 个维度、38 个满足 `rows>=10 / dates>=5` 的切片做 BH-FDR 后，7 个 q<0.05 格子全部仍是 `<30 rows`、`<10 dates` 或 feature-unknown coverage 组；满足 contract 正式样本门槛且显著的 regime 为 **0**。不能把这些小格子变成 live hard gate。

研究动作：不 size-up、不新增 regime gate、不把事故盈利计入 alpha；策略结论降为 `inconclusive / frozen-forward failed`。现有用户授权 tiny-live 是否暂停属于另一个资金/部署动作，本报告不自动改生产。

```text
significance=FAIL_LOW_SAMPLE_AND_LIVE_FILL_CI; baseline=FAIL_NO_REGIME_RESIDUAL_MODEL;
forward=FAIL_DIRECTION_REVERSED; conclusion=inconclusive
```

## 数据快照

| 项目 | 值 |
|---|---|
| historical raw | atlas 14,368 state rows / 50 dates / 36 cities；SHA256 `b504589c...efbec` |
| historical first signal | 218 city-day / 48 dates / 33 cities；全部有 PIT quote + exact settlement |
| live raw 覆盖 | runner 到 `2026-07-22T01:56:15Z`；15 个 first signals |
| canonical build | `fact_trades 2026-07-22T01:59:34Z`；`fact_signal_candidates 2026-07-22T01:57:12Z` |
| live settlement | 17/17 fills settled；0 missing / unsettled |
| CLOB gate | `gate_pass=true`；1105 DB/cache fills，差异 0；missing/over-order 0 |
| live fee evidence | 17 fills 均 canonical `exact` |
| opportunity freshness gap | canonical decision snapshot 只到 `2026-07-21T14:02:55Z`；本分析 d1 opportunity 用 strategy raw，不拿全局 candidate 表冒充分母 |

本次实际执行增量补数：同步 Mac market mirror；抓取 7/21 的 47 个有市场城市、517 个 settlement brackets；重物化 recent candidate partition 与 `fact_trades`；再次运行 CLOB gate 通过。Boston/Jakarta/Lagos/Minneapolis/Phoenix 当天无市场，5 个 `not_found` 是正常 universe gap。

## Target metric 与固定分母

- historical grain：每个 `(city,target_date)` 首个 d1 YES mid≥0.80 的 PIT 小时。
- live grain：runner 首个 bounded d1 city-day；fill 绩效另按 canonical fill 统计。
- exact label：最终 winning bracket 必须正好等于 d1；overshoot 到 d2+ 与停在 current 都输。
- historical executable cost：`YES ask = 1 - d1 NO bid`，再加 `0.05*p*(1-p)` Weather taker fee。
- live cost：signal 层使用首次 journal executable ask；actual 层只用 `fact_trades.pnl_usd_at_fill`。
- baseline：同 row market mid / executable ask；regime 必须解释 `P(d1 exact)-market` residual，不是只提高天气命中率。
- frozen split：train `<2026-06-21`；historical forward `>=2026-06-21`；7/16 后 raw/live 为额外 fresh forward。
- first-row 实现使用排序后 `drop_duplicates(keep=first)` 保留整行；不使用会按列跳过 NULL 的 `groupby.first()`，避免从同 city-day 后续小时偷取非空 forecast/regime 特征。

## 双漏斗

### Signal funnel

| 层 | grain | rows | dates | 说明 |
|---|---|---:|---:|---|
| raw historical universe | city-date-hour state | 14,368 | 50 | 36 城 PIT atlas |
| historical mechanism rows | state + usable d1 quote/settlement | 11,549 | 49 | coverage，不是策略过滤 |
| `mid>=0.80` trigger rows | state | 296 | 48 | 同 city-day 可重复 |
| historical first signal | city-day | 218 | 48 | 主研究分母 |
| live raw first signal | city-day | 15 | 6 | 13 bounded + 2 Jeddah `X+` shadow |
| clean bounded live signal | city-day | 9 | 6 | 排除 4 个 continuity-incident 伪 d1 |

### Evidence funnel

| 层 | grain | rows | dates | gap |
|---|---|---:|---:|---|
| historical PIT feature + quote | city-day | 218 | 48 | atlas archive coverage；不是 actual fills |
| historical exact settlement | city-day | 218 | 48 | 100% |
| clean live exact settlement | city-day | 9 | 6 | 100% |
| live executable bounded expression | city-day | 8 | 6 | Taipei 只 shadow；Jeddah `X+` 不属于 bounded d1 |
| actual clean fill | fill | 12 | 6 | 8 city-day；maker/taker fill 数不等于 signal 数 |
| incident fills（隔离） | fill | 5 | 1 | 4 city-day / 25 shares；正确 d1 当时均不应触发 |

## 总体表现与 frozen forward

| 分母 | rows / dates | 胜率 | cost | fee-adjusted PnL | ROI | date-block 95% CI |
|---|---:|---:|---:|---:|---:|---|
| historical train | 162 / 33 | 93.21% | 149.904 | +1.096 | +0.73% | — |
| historical forward | 56 / 15 | 98.21% | 51.584 | +3.416 | +6.62% | — |
| historical all | 218 / 48 | 94.50% | 201.488 | +4.512 | +2.24% | `[-0.90%,+5.07%]` |
| clean live signal replay | 9 / 6 | 55.56% | 7.889 | -2.889 | -36.62% | `[-76.54%,-0.53%]`* |
| actual clean fills | 12 / 6 | 8 city-day 中 5 胜 | $51.425 | **-$16.6036** | **-32.29%** | `[-76.74%,+6.25%]`* |
| all actual fills（含事故） | 17 / 6 | — | $76.360 | -$16.5396 | -21.66% | 不作为策略口径 |

`*` 只有 6 个 date blocks，数值 CI 不能越过 low-sample gate。signal replay 每 city-day 统一 1 share；actual fill 受 paired maker/taker 与成交选择影响，两者不能混称。

历史 12 个 loss 中 11 个是 overshoot 到 d2+、1 个停在 current。clean live 的 4 个 loss 为 Taipei/Wuhan/KualaLumpur overshoot 与 Ankara stall；失败机制方向一致，但发生率从历史 `5.5%` 跳到 live `44.4%`。

## Regime 全维度结果

### 1. 剩余 runway / forecast

| day regime | rows | loss | ROI | 95% CI | historical forward ROI | 读法 |
|---|---:|---:|---:|---|---:|---|
| open runway | 62 | 4 overshoot | -0.4% | `[-7.0%,+5.2%]` | -2.7% | runway 没提高 exact-d1 EV；更多热量也会 skip-over |
| marginal runway | 37 | 2 overshoot | +2.8% | `[-5.2%,+9.4%]` | +6.9% | 不显著 |
| forecast capped | 43 | 2 overshoot + 1 stall | +1.2% | `[-6.2%,+7.9%]` | +7.2% | 不显著 |
| forecast busted | 49 | 3 overshoot | +2.2% | `[-4.8%,+8.3%]` | +6.6% | 不显著 |

`forecast_gap_to_running_native` 预测失败的 AUC `0.500`，几乎随机；forecast peak delta AUC separation 也只有 `0.524`。因此不能靠剩余热量或 peak-clock 单变量修好这条表达。

### 2. 路径 / running-max maturity

| regime | rows | loss | ROI | 95% CI | 结论 |
|---|---:|---:|---:|---|---|
| active warming | 178 | 10 | +2.1% | `[-1.5%,+5.4%]` | 占分母 82%，标签基本等于策略本体，分离力弱 |
| fresh running high | 168 | 9 | +2.5% | `[-1.2%,+6.1%]` | 不显著 |
| near-high plateau | 21 | 1 | +2.8% | `[-4.2%,+9.1%]` | 低样本 |
| stalled high | 12 | 1 stall | -2.8% | `[-19.7%,+7.6%]` | 方向合理但不可下 gate |
| pullback from high | 10 | 0 | +9.2% | `[+5.8%,+11.8%]` | 仅 10 rows / 8 dates，FDR 后仍低样本 |

连续特征里 `minutes_since_running_max` 的 failure/win 中位数为 `0.9 / 16.9 min`，AUC separation `0.683`；这是可继续做连续 overshoot hazard 的线索，不是“刚创新高就禁买”的已验证规则。

### 3. 时段 / peak clock

| regime | rows | loss | historical ROI | 95% CI | clean live |
|---|---:|---:|---:|---|---|
| late morning | 28 | 0 | +6.6% | `[+4.0%,+9.6%]` | KL `0/1`，已反向；历史格低样本 |
| solar peak | 106 | 9 | +0.1% | `[-4.6%,+4.6%]` | `2/5`，signal ROI `-54.2%` |
| afternoon decay | 79 | 3 | +3.6% | `[-1.7%,+7.9%]` | `3/3`，signal ROI `+11.4%` |
| peak ahead | 93 | 6 | +1.0% | `[-5.1%,+6.3%]` | 无可靠 live forecast feature parity |
| near peak | 31 | 3 | -1.2% | `[-12.9%,+8.9%]` | 无可靠 parity |
| peak passed | 67 | 3 | +2.8% | `[-2.5%,+7.8%]` | 无可靠 parity |

`afternoon_decay` 是当前最值得继续观察的机制候选，但历史 CI 跨 0，live 只有 3 行；不能据此过滤 solar-peak 或只留 afternoon。

### 4. 水汽 / 云 / 风

| dimension | 大样本结果 | 薄样本极端 | 结论 |
|---|---|---|---|
| moisture/cloud | mixed 111 rows ROI +1.0%；dry 74 rows +4.5%，两者 CI 均跨 0 | humid+overcast 3 rows 1/3、ROI -65.8%；cloud suppression 11/11、humid risk 18/18 | 两端都有小样本极端，不能下天气 gate |
| wind | light 135 rows +1.3%；moderate 72 rows +4.7%，CI 均跨 0 | windy 10 rows -2.6% | 不显著；live 也未复现单调性 |
| humidity continuous | failure/win 中位 `49.1% / 51.8%`，AUC `0.501` | — | 无分离力 |

### 5. 城市气候族

| family | rows | loss | ROI | 95% CI |
|---|---:|---:|---:|---|
| continental dry/hot | 68 | 3 | +2.1% | `[-3.4%,+6.4%]` |
| humid low-latitude | 64 | 6 | -0.3% | `[-7.0%,+6.1%]` |
| Europe cloud-break | 48 | 2 | +4.0% | `[-2.8%,+9.2%]` |
| southern/maritime | 38 | 1 | +4.6% | `[-2.4%,+8.7%]` |

所有 family CI 都跨 0。live loss 集中出现于 Taipei/Wuhan/KualaLumpur 并不足以支持砍城市；这是 3 个事件，不是城市池结论。

### 6. 市场价格 regime

| market mid | rows | loss | ROI | 95% CI | historical forward ROI |
|---|---:|---:|---:|---|---:|
| 0.80–0.85 | 54 | 7 | +2.0% | `[-7.0%,+11.0%]` | +16.9% |
| 0.85–0.90 | 46 | 2 | +6.8% | `[-0.4%,+12.0%]` | +11.9% |
| 0.90–0.95 | 53 | 1 | +3.8% | `[-0.1%,+6.2%]` | -1.7% |
| 0.95+ | 65 | 2 | -1.8% | `[-6.3%,+1.4%]` | +1.5% |

失败行的 mid 中位 `0.840`，赢单 `0.912`；mid 的 failure AUC separation `0.722`，是所有单变量最高。但这是市场自己已经在表达风险，不是新增 weather residual。live clean 的 `mid 0.80–0.85` 为 `2/5`、signal ROI `-52.2%`，直接反转历史 forward 的漂亮点估。`ask/mid>0.95` 历史上也没有 fee edge。

## 多重检验与为什么不造 gate

本轮同时检验 10 个维度、38 个有基本支持的 regime。BH-FDR 后 q<0.05 的 7 个格子为：`day_space_unknown`、`peak_clock_unknown`、plateau、pullback、humid-risk、cloud-suppression、late-morning。它们全部命中 low-sample 或 missing-feature coverage，正式可用格子为 0。

因此没有证据支持：

- 加 `ask<=0.95`、只留 afternoon、删 humid city、只做 peak-passed；
- 用 forecast gap / humidity / wind 单阈值挡住 Wuhan/Ankara/KL；
- 把 7/19 四个 continuity-incident current-bracket wins 算进 d1 promotion。

下一步若继续研究，应把 `P(exact d1)` 拆成 `P(stall current) / P(exact d1) / P(overshoot d2+)` 的连续概率模型，并在同 rows 上用 logloss/Brier 对 market ladder；regime 只做特征，不再当规则清单。

## Execution / 事故隔离

| slice | fills | city-day | cost | fees | realized PnL | ROI |
|---|---:|---:|---:|---:|---:|---:|
| clean strategy fills | 12 | 8 | $51.425 | $0.17861 | **-$16.60361** | **-32.29%** |
| 7/19 continuity incident | 5 | 4 | $24.935 | $0.00096 | +$0.06404 | +0.26% |
| raw/canonical all | 17 | 12 | $76.360 | $0.17957 | -$16.53957 | -21.66% |

事故腿实际买的是错误 current bracket，恰好全部结算 YES；修复后的正确 d1 mid 只有 `0.0015..0.0070`，策略应当四笔都不下。这 `+$0.06404` 只能算数据事故持仓结果，不能改善 d1 alpha。

## 三门与残余风险

| 门 | 结果 | 证据 |
|---|---|---|
| significance | FAIL | historical CI 跨 0；actual clean live fill CI 也跨 0；signal replay虽表面全负但只有 6 dates |
| same-denominator baseline | FAIL | 没有 regime probability model 在同 rows proper-score 打败 market；较低 mid 本身是最强失败信号 |
| forward | FAIL | historical forward +6.62%，fresh clean live -36.62%，方向反转 |

8 环覆盖：描述性切片、date-block 推断、market calibration、官方 fee、actual fill、事故 selection、日期相关性与 executable baseline 已覆盖；容量、maker queue/adverse selection 的 regime 交互、完整 live PIT forecast parity 尚缺。缺口不影响“不能 size-up / 不能新增 regime gate”的结论。

最终：在 218 个 historical first signals 上，`d1 YES mid>=0.80` 相对 executable zero-edge baseline 的 fee ROI 为 `+2.24%`（95% CI `[-0.90%,+5.07%]`）；clean live forward 9 个 signals 为 `-36.62%`，方向反转。没有一个满足正式样本门槛的 regime 显著，结论 `inconclusive / forward failed`，动作是不扩仓、不加 hard gate、转向连续三状态 overshoot residual。

## 可复跑产物

- 脚本：`scripts/analysis/market_structure_edge/research_d1_yes_high_mid_regime_v1.py`
- 汇总：`generated/d1_yes_high_mid_regime_v1/summary.json`
- 全 regime：`generated/d1_yes_high_mid_regime_v1/regime_summary.csv`
- 历史 218 行：`generated/d1_yes_high_mid_regime_v1/historical_first_signals.csv`
- clean/incident live first signals：`generated/d1_yes_high_mid_regime_v1/live_forward_first_signals.csv`
- canonical fills：`generated/d1_yes_high_mid_regime_v1/actual_live_fills.csv`
- 连续特征：`generated/d1_yes_high_mid_regime_v1/continuous_feature_diagnostics.csv`
