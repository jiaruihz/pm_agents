# Market Ladder Kink / Exact-Bracket Mispricing v1

> observed_at_utc: 2026-08-09T08:10:43.193461+00:00
> verdict: `inconclusive`; zero-notional only; no live change.

## 数据快照

- 数据源：canonical `/Volumes/jrs/pm_agents/runtime/weather.db`（device=16777247, inode=54444）的 `tmax_v2_ladder_snapshots/rung_quotes` + `settlement_outcomes`；fresh coverage 使用 `/Volumes/jrs/weather_data_feed_service_runtime/output/market_implied_tail_residual_shadow_v1/checkpoints.jsonl`。
- canonical build：fact candidates `2026-08-08T08:57:10.205205+00:00`；ladder `2026-07-04..2026-07-28`；settlement 到 `2026-08-07`。
- 本轮 evaluation slice：`2026-07-11..2026-07-28`；raw complete PIT snapshots 210,194，固定同快照分母 7,201 snapshots / 14 dates / 47 cities。
- fixed snapshot rows：all rungs 70,831；direct rungs 65,912；winner-direct scorable 65,806；interior kink rows 50,921。
- evidence gap：18 个固定 snapshot 的 winning rung 没有 direct two-sided quote，完整保留为 coverage gap、未进入 proper score。actual fill=0。
- shadow raw：20,482 rows / 4 target dates / 47 cities，`2026-08-07..2026-08-10`；mode=['shadow_zero_notional']，notional=[0.0]。
- 数据完整性自检：manifest `db_route=healthy`、storage audit healthy；controller 为 WARNING，唯一当前 warning 是 `snapshot_city_state_coverage:missing_non_trading_weather_state`，与本研究 market-only ladder 分母无关。docs debt precheck 因工作区既有未跟踪报告与 repeated-function ceiling 失败，本脚本未引用这些文件。

## 单轮 brief / Readiness

- hypothesis：在同一 PIT exact-bracket ladder 中，局部 log-concavity 凹陷对最终 winning bracket 有超过 market level/calibration 的连续排序增量。
- data scope：2026-07-11..2026-07-28 canonical complete PIT ladders；包含已查看的 development window；clean forward 尚未读。
- acceptance：kink-adjusted 对 raw market 与 calibration-only 的 Brier/logloss date-block CI 都小于 0；AUC/rank 同号；fee expression CI 不跨 0；clean forward 同号。
- 唯一动作：固定 market-only expanding-date OOF A/B，并冻结 zero-notional forward artifact。
- 不在范围：forecast、METAR/source reversal、任何 live/order 修改、city-specific selector、额外阈值搜索。

| readiness | 状态 | 证据 / gap |
|---|---|---|
| PIT state + clocks | READY | canonical `pit_verified_capture`; source snapshot/available/book fetch clocks retained |
| canonical/build identity | READY | DB route same inode; identity frozen above |
| quote freshness/depth | READY with gaps | direct bid/ask/size/depth/quote age retained; missing winner remains gap |
| settlement/label | READY through 7/28 evaluation | canonical settlement extends beyond evaluation end |
| independent target dates | READY for OOF | 9 scored dates after 5 prior dates |
| clean frozen-forward | BLOCKED | model is first frozen in this report; existing 8/8+ telemetry predates this artifact and is not untouched labeled forward |
| WS reconstruction | N/A | primary uses immutable REST/direct-book canonical snapshots, not raw WS deltas |
| sampling grain | READY | first qualifying same city-date-local-2h snapshot; every rung shares one snapshot identity |

## Target 与连续 score

```text
kink_score_i = 0.5*(log market_p[i-1] + log market_p[i+1]) - log market_p[i]
p_calibration ∝ market_p^gamma
p_kink_adjusted ∝ market_p^gamma * exp(beta*kink_score)
```

正 kink 表示中心档相对左右邻档便宜。边界档或缺邻档的 score 为 unavailable，不以 0 冒充观测；模型投影时只让它没有局部调整。`gamma` 吸收 favorite/longshot 与 base-rate calibration，`beta` 才是 ladder microstructure 增量。全 ladder softmax 重新归一化，保持 exact outcomes 概率和为 1。

expanding OOF 共 9 folds；最后一折 kink beta=+0.0325。全 development freeze：gamma=1.060939，beta=+0.029506，ridge=0.25（固定单规格，K=1，未用 ROI 选参）。

## 双漏斗

signal funnel：

- raw universe：210,194 complete PIT ladder snapshots / 18 dates。
- fixed sampling：7,201 city-date-local-2h same-snapshot states。
- OOF probability states：4,135 snapshots / 9 dates。
- policy denominator：见下表 common snapshots；positive-edge 只用经济零点，不新增 tuned gate。

evidence funnel：

- direct quote rows 65,912 → winner-direct scorable rows 65,806 → interior kink rows 50,921。
- executable replay = direct YES ask + official fee；actual fills=0；clean settled forward=0。

## Probability：同分母 expanding-date OOF

| arm | rows | snapshots | dates | Brier | logloss | AUC | top1 | Brier Δ vs market | 95% CI | Brier Δ vs calibration | 95% CI |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|---|
| raw_market | 41,124 | 4,135 | 9 | 0.703845 | 1.437448 | 0.8787 | 39.39% | +0.000000 | [+0.000000, +0.000000] | +0.000239 | [-0.000382, +0.000947] |
| market_calibration_only | 41,124 | 4,135 | 9 | 0.703606 | 1.436314 | 0.8787 | 39.39% | -0.000239 | [-0.000917, +0.000406] | +0.000000 | [+0.000000, +0.000000] |
| kink_adjusted | 41,124 | 4,135 | 9 | 0.703612 | 1.436190 | 0.8787 | 39.42% | -0.000233 | [-0.000870, +0.000417] | +0.000006 | [-0.000119, +0.000154] |

主判断看 `kink_adjusted` 相对 `market_calibration_only`，不是只看相对 raw market。前者隔离真正 local-kink 增量，后者可能只是把 favorite/longshot base rate 再校准。CI 按 target_date block bootstrap，日期内 snapshot 等权。

### Kink 排序

| kink quintile | rows | dates | mean score | win | market p | raw residual | OOF adjustment |
|---|---:|---:|---:|---:|---:|---:|---:|
| Q1_premium | 6,467 | 9 | -0.7867 | 26.70% | 26.23% | +0.48% | -0.09% |
| Q2 | 6,466 | 9 | -0.4349 | 19.18% | 19.87% | -0.70% | +0.01% |
| Q3 | 6,466 | 9 | -0.1894 | 12.03% | 11.82% | +0.21% | +0.04% |
| Q4 | 6,466 | 9 | +0.1285 | 3.62% | 3.93% | -0.31% | +0.03% |
| Q5_discount | 6,466 | 9 | +0.7390 | 1.27% | 1.07% | +0.20% | +0.02% |

## Trade expression（research replay，不是 fill）

每个 common snapshot 都比较 max-kink center、左右邻档、三档 basket、最低价 YES、cold_1、market mode 与 kink-adjusted max edge。1 share/leg，entry=direct YES ask，fee=`0.05*p*(1-p)`；不使用 future touch 或 maker fill 假设。

| policy | selected/common snapshots | dates | cities | win | cost | PnL | fee ROI | date CI | top-date abs PnL | top-city abs PnL |
|---|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|
| max_kink_center | 4130/4130 | 9 | 47 | 1.33% | $69.50 | $-14.50 | -20.86% | [-61.24%, +21.08%] | 24.2% | 18.7% |
| kink_left_neighbor | 4130/4130 | 9 | 47 | 3.00% | $234.55 | $-110.55 | -47.13% | [-59.95%, -35.88%] | 19.6% | 5.7% |
| kink_right_neighbor | 4130/4130 | 9 | 47 | 3.29% | $216.92 | $-80.92 | -37.30% | [-64.16%, -5.84%] | 19.4% | 6.8% |
| kink_local_3_basket | 4130/4130 | 9 | 47 | 7.63% | $520.97 | $-205.97 | -39.54% | [-53.95%, -23.01%] | 18.3% | 5.3% |
| ordinary_lowest_ask_yes | 4130/4130 | 9 | 47 | 0.34% | $14.12 | $-0.12 | -0.86% | [-87.35%, +114.31%] | 28.9% | 22.4% |
| cold_1_yes | 4130/4130 | 9 | 47 | 24.84% | $1045.08 | $-19.08 | -1.83% | [-16.52%, +16.18%] | 31.9% | 6.9% |
| market_mode_yes | 4130/4130 | 9 | 47 | 39.42% | $1730.34 | $-102.34 | -5.91% | [-14.61%, +5.55%] | 26.0% | 6.4% |
| kink_adjusted_max_edge_all | 4130/4130 | 9 | 47 | 1.50% | $52.52 | $+9.48 | +18.05% | [-30.10%, +87.08%] | 38.7% | 22.7% |
| kink_adjusted_positive_edge | 86/4130 | 9 | 30 | 40.70% | $34.48 | $+0.52 | +1.51% | [-26.72%, +36.38%] | 31.5% | 14.4% |

经济零点表达 `kink_adjusted_positive_edge`：86/4130 snapshots，fee ROI +1.51%，CI [-26.72%, +36.38%]。没有通过 proper score 前，正 ROI 也只能是探索性 secondary。

## 稳定性与假象拆分

完整 slice CSV 按 lifecycle、mode distance、ask、spread、top ask depth、quote age、quote fraction 与 city 保存。下面列出 kink-vs-calibration Brier delta 绝对值最大的诊断项；这些是解释层，不是 selector。负值才表示 kink 更好。

| dimension | value | rows | dates | cities | kink | Brier Δ | 95% CI | logloss Δ | 95% CI |
|---|---|---:|---:|---:|---:|---:|---|---:|---|
| ask_bucket | 50c+ | 433 | 9 | 37 | -1.0259 | +0.000288 | [-0.000219, +0.000770] | +0.000577 | [-0.000366, +0.001600] |
| spread_bucket | >5c | 177 | 9 | 33 | -0.4103 | -0.000275 | [-0.000622, +0.000006] | -0.000836 | [-0.001918, -0.000086] |
| lifecycle | D0_10_14 | 203 | 8 | 16 | -0.1349 | -0.000237 | [-0.000470, -0.000041] | -0.000644 | [-0.001275, -0.000119] |
| city | TelAviv | 648 | 9 | 1 | -0.0810 | +0.000187 | [-0.000095, +0.000430] | +0.000380 | [-0.000370, +0.001066] |
| city | Munich | 643 | 9 | 1 | -0.0901 | -0.000168 | [-0.000365, -0.000018] | -0.000394 | [-0.000845, -0.000027] |
| city | Madrid | 437 | 8 | 1 | -0.0370 | +0.000160 | [-0.000023, +0.000334] | +0.000462 | [+0.000040, +0.000873] |
| city | Istanbul | 998 | 9 | 1 | -0.0870 | -0.000160 | [-0.000320, +0.000001] | -0.000284 | [-0.000633, +0.000030] |
| city | Austin | 644 | 9 | 1 | -0.1018 | +0.000149 | [+0.000017, +0.000284] | +0.000399 | [+0.000090, +0.000726] |
| depth_bucket | <5 | 318 | 9 | 47 | -0.2808 | +0.000142 | [+0.000017, +0.000267] | +0.000415 | [+0.000014, +0.000822] |
| city | SaoPaulo | 681 | 9 | 1 | -0.0892 | +0.000136 | [-0.000019, +0.000322] | +0.000350 | [-0.000080, +0.000854] |
| city | Jeddah | 201 | 4 | 1 | -0.0922 | +0.000120 | [-0.000119, +0.000470] | +0.000589 | [-0.000272, +0.002071] |
| city | Helsinki | 651 | 7 | 1 | -0.1108 | +0.000120 | [+0.000041, +0.000193] | +0.000360 | [+0.000159, +0.000560] |
| city | Atlanta | 828 | 9 | 1 | -0.1172 | -0.000118 | [-0.000293, -0.000000] | -0.000400 | [-0.001060, +0.000002] |
| city | Milan | 492 | 8 | 1 | -0.1222 | -0.000112 | [-0.000368, +0.000124] | -0.000552 | [-0.001254, +0.000163] |
| city | Paris | 507 | 9 | 1 | -0.1018 | -0.000104 | [-0.000276, +0.000053] | -0.000167 | [-0.000539, +0.000173] |
| city | Singapore | 330 | 7 | 1 | -0.1298 | -0.000102 | [-0.000270, +0.000059] | -0.000178 | [-0.000470, +0.000162] |
| city | MexicoCity | 1,010 | 9 | 1 | -0.0785 | +0.000090 | [-0.000049, +0.000223] | +0.000190 | [-0.000224, +0.000607] |
| city | Lucknow | 630 | 8 | 1 | -0.1159 | +0.000090 | [-0.000010, +0.000224] | +0.000260 | [-0.000005, +0.000578] |
| city | Chicago | 659 | 9 | 1 | -0.1141 | -0.000086 | [-0.000191, +0.000022] | -0.000265 | [-0.000552, +0.000020] |
| city | Dallas | 550 | 9 | 1 | -0.1324 | +0.000080 | [-0.000060, +0.000221] | +0.000265 | [-0.000102, +0.000622] |
| city | NYC | 724 | 9 | 1 | -0.1091 | +0.000080 | [-0.000024, +0.000191] | +0.000208 | [-0.000094, +0.000552] |
| mode_distance | cold_1 | 4,114 | 9 | 47 | -0.4147 | -0.000068 | [-0.000142, +0.000014] | -0.000234 | [-0.000454, +0.000008] |
| mode_distance | hot_1 | 4,114 | 9 | 47 | -0.4392 | +0.000068 | [+0.000005, +0.000138] | +0.000215 | [-0.000018, +0.000492] |
| city | Wuhan | 655 | 9 | 1 | -0.1463 | +0.000065 | [-0.000121, +0.000267] | +0.000179 | [-0.000519, +0.001038] |

判别规则：如果增量只在宽 spread、低 depth、老 quote 或少数 city/date 为正，就归类为薄盘口/陈旧报价假象；如果 calibration-only 已吸收改善，则归类为 market/base-rate；只有 kink-vs-calibration 在宽分母和流动性较好层仍稳定，才叫 microstructure residual。

## Recent / frozen forward / concentration

recent `2026-07-24..2026-07-28` 仍属于已经看过的 development data，只作稳定性诊断，不能冒充 frozen forward。

| arm | rows | snapshots | dates | Brier | logloss | AUC | top1 | Brier Δ vs market | 95% CI | Brier Δ vs calibration | 95% CI |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|---|
| raw_market | 20,213 | 2,037 | 5 | 0.694057 | 1.414319 | 0.8829 | 41.16% | +0.000000 | [+0.000000, +0.000000] | +0.000648 | [-0.000394, +0.001668] |
| market_calibration_only | 20,213 | 2,037 | 5 | 0.693408 | 1.411958 | 0.8829 | 41.16% | -0.000648 | [-0.001691, +0.000394] | +0.000000 | [+0.000000, +0.000000] |
| kink_adjusted | 20,213 | 2,037 | 5 | 0.693345 | 1.411677 | 0.8830 | 41.32% | -0.000712 | [-0.001651, +0.000211] | -0.000064 | [-0.000183, +0.000065] |

recent trade replay：

| policy | selected/common snapshots | dates | cities | win | cost | PnL | fee ROI | date CI | top-date abs PnL | top-city abs PnL |
|---|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|
| max_kink_center | 2032/2032 | 5 | 47 | 1.43% | $35.87 | $-6.87 | -19.14% | [-53.06%, +11.94%] | 30.8% | 19.5% |
| kink_left_neighbor | 2032/2032 | 5 | 47 | 3.30% | $117.35 | $-50.35 | -42.90% | [-59.81%, -29.90%] | 35.6% | 9.5% |
| kink_right_neighbor | 2032/2032 | 5 | 47 | 1.53% | $100.92 | $-69.92 | -69.28% | [-79.15%, -56.74%] | 32.4% | 6.2% |
| kink_local_3_basket | 2032/2032 | 5 | 47 | 6.25% | $254.13 | $-127.13 | -50.03% | [-57.86%, -41.24%] | 29.6% | 5.0% |
| ordinary_lowest_ask_yes | 2032/2032 | 5 | 47 | 0.34% | $7.23 | $-0.23 | -3.22% | [-100.00%, +111.88%] | 43.1% | 36.2% |
| cold_1_yes | 2032/2032 | 5 | 47 | 21.85% | $503.58 | $-59.58 | -11.83% | [-27.39%, +8.97%] | 35.8% | 6.9% |
| market_mode_yes | 2032/2032 | 5 | 47 | 41.73% | $839.83 | $+8.17 | +0.97% | [-14.47%, +17.91%] | 47.4% | 6.1% |
| kink_adjusted_max_edge_all | 2032/2032 | 5 | 47 | 1.33% | $24.07 | $+2.93 | +12.17% | [-45.06%, +79.55%] | 40.7% | 17.0% |
| kink_adjusted_positive_edge | 41/2032 | 5 | 21 | 39.02% | $15.76 | $+0.24 | +1.55% | [-46.36%, +72.84%] | 48.3% | 15.7% |

候选 `kink_adjusted_positive_edge` 日度 PnL（未选择的 snapshot 不伪造成 fill；日内多城相关性由 target-date bootstrap 处理）：

| target_date | selected | cost | PnL | ROI |
|---|---:|---:|---:|---:|
| 2026-07-20 | 7 | $3.02 | $-1.02 | -33.84% |
| 2026-07-21 | 18 | $7.98 | $-0.98 | -12.27% |
| 2026-07-22 | 12 | $4.54 | $+1.46 | +32.26% |
| 2026-07-23 | 8 | $3.18 | $+0.82 | +25.61% |
| 2026-07-24 | 2 | $1.01 | $-0.01 | -1.47% |
| 2026-07-25 | 7 | $2.80 | $+0.20 | +7.10% |
| 2026-07-26 | 10 | $3.68 | $+1.32 | +35.78% |
| 2026-07-27 | 6 | $2.37 | $+2.63 | +111.28% |
| 2026-07-28 | 16 | $5.89 | $-3.89 | -66.05% |

- thin book：top ask size<5 的 Brier delta=+0.000142，CI [+0.000017, +0.000267]，即 kink 在最薄层反而显著更差。
- deep book：top ask size>=20 的 Brier delta=+0.000002，CI [-0.000017, +0.000026]，无稳定增量。
- wide spread：>5c 仅 177 rows，Brier CI 上界 +0.000006，不能把局部 logloss 改善外推为可执行 alpha。
- stale quote：primary OOF interior rows 全部落在 quote_age<=1m；因此本窗没有陈旧报价支撑 kink，8/8+ collector 仍须继续保留 freshness 字段。
- 当前模型与 coefficient 在本报告生成时才冻结；clean forward 起点为 `2026-08-11`。8/8+ raw telemetry 可用于 coverage continuity，但不能倒算成 untouched score。
- 日度 PnL 与 policy/city/date 集中度已写 artifacts；表中的 top-date/top-city absolute-PnL share 用来识别少数日期、城市或赢家噪声。
- multiple testing：主模型 K=1；稳定性 slices 与 8 个 trade policies 未校正，只作诊断，不能提升结论。

## 8 环覆盖

1 描述性=PASS；2 date bootstrap=PASS；3 排序/AUC=PASS；4 proper probability=PASS；5 displayed-book microstructure=PARTIAL（无真实 prints/fills）；6 capacity=PARTIAL（有 top depth，无 fill）；7 日期/城市集中度=PASS；8 same-row market/base-rate/counterfactual=PASS。

## Gate 与唯一动作

```text
significance=FAIL
baseline=FAIL
forward=NA
conclusion=inconclusive
action=保持 zero-notional collector，等待 clean settled forward；不改 live
```

在 `2026-07-11..2026-07-28` 固定同快照 expanding-OOF 分母，kink-adjusted 相对 calibration-only 的 Brier delta 为 +0.000006（95% CI [-0.000119, +0.000154]），forward NA，结论 `inconclusive`；唯一动作：保持 zero-notional collector，等待 clean settled forward；不改 live。

## Bloodline / Artifacts

- family：独立 `market_ladder_kink_v1`，属于 `[2] market_structure_edge`；不是 forecast-tail、METAR reversal 或 source-event family。
- data lineage：canonical ladder snapshot → market-only ModelOutput/research score；当前不生成 `SignalCandidate`、`TradeIntent`、plan/order/fill。
- artifact root：`/Volumes/jrs-archive/pm_agents/research/artifact_store/market_ladder_kink_v1/market_ladder_kink_v1_20260809`
- files：`summary.json`、`model_freeze.json`、`oof_rung_predictions.csv.gz`、`probability_summary.csv`、`recent_probability_summary.csv`、`kink_rank.csv`、`stability_slices.csv`、`trade_summary.csv`、`recent_trade_summary.csv`、`daily_pnl.csv`、`shadow_coverage.csv`、`folds.csv`。
