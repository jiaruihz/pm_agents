# D-1 旧天气预测模型重估 v1

Generated: 2026-08-04  
Scope: `pre_predict` / `weather_edge_v1` / HeadA `low_price_yes_lottery`  
Action: research only; no production or order change

## 结论

旧模型不能恢复成独立概率/edge 引擎，但有三部分仍值得保留：D-1 时钟、城市固定
forecast source、以及 forecast-vs-market disagreement 作为连续特征。下一版应是
`market logit + weather residual`，而不是继续用旧 `model_p_yes - ask` 直接下单。

当前最接近可继续研究的是窄版 HeadA，不是 5 月的全价带 `mid_price_core`：

- 2026-07-16..2026-08-03 frozen would-live：141 张已结算 / 15 个 target dates；
- fixed 5 shares、fresh ask、Weather taker fee：成本 `$84.63`，PnL `+$10.37`，ROI
  `+12.26%`，target-date bootstrap 95% CI `[-27.8%, +52.4%]`；
- 最近 2026-07-29..2026-08-03：37 张 / 6 日，ROI `+2.99%`，CI
  `[-71.2%, +135.6%]`；
- 结论仍是 `inconclusive / keep zero-notional shadow`，不能恢复 live。

## 1. 找到的两条历史线

### A. 5 月实际运行的早期模型

`weather_edge_v1 / mid_price_core_v1` 使用单次 GFS/ECMWF forecast maximum，加城市
历史 `actual - forecast` 误差分布，映射成 exact-bracket `model_p_yes`，再按
`model side probability - price` 选 25–75c 的 YES/NO。它不是后来的 WCIR 或
market-offset 模型。

历史证据已经否定把它当独立概率真相：

- 2026-05-27..06-01 holdout：raw model Brier `0.22261`，market `0.17154`；
- city-day distribution holdout：raw normalized logloss `1.3957`，market `0.7647`；
- T-24..26 live/city-day ROI 从 2026-05-26 前 `+30.24%`，变为 05-26..31
  `-12.71%`，06-01 后 `-16.53%`。

这说明早期盈利主要没有稳定穿越时间，而不是缺一个止盈规则。

### B. 7 月实际小额运行、随后转 shadow 的 HeadA

HeadA 把同一概率骨架收窄到 D-1 22–24h、BUY YES、ask 5–20c、`dist>0`、每
city-date 一张。2026-07-15 后 selector 冻结为 zero-notional would-live，因而
2026-07-16 以后的结果可用于真正的 forward 检验。

## 2. 最新 frozen HeadA 结果

固定 signal funnel：149 would-live entries，其中 141 已结算、8 未结算；没有用结算结果
重新选阈值。执行评估为 5 shares 在 fresh ask 吃单，并计
`0.05 * ask * (1-ask)` 每股 Weather taker fee。

| 窗口 | tickets / dates | win rate | ROI | date-bootstrap 95% CI |
|---|---:|---:|---:|---:|
| 07-16..08-03 | 141 / 15 | 13.48% | +12.26% | [-27.8%, +52.4%] |
| 07-16..07-25 | 104 / 9 | 14.42% | +15.02% | [-28.6%, +56.6%] |
| 07-29..08-03 | 37 / 6 | 10.81% | +2.99% | [-71.2%, +135.6%] |

15 个交易日中 9 日亏损，5 日 ROI 不高于 -50%；最大单日亏损 `$6.12`。去掉最大
winner 后 ROI 仍为 `+6.68%`，说明并非完全由一张票撑起，但日期 CI 仍很宽。

## 3. 概率层：旧 raw model 仍然过度自信

在完全相同的 141 张票上，以 fresh bid/ask midpoint 作为同刻 market baseline：

| probability | coverage | mean p | Brier | logloss | AUC |
|---|---:|---:|---:|---:|---:|
| fresh market midpoint | 141 | 10.67% | 0.11275 | 0.37697 | 0.6937 |
| old raw `model_p_yes` | 141 | 40.13% | 0.18617 | 0.56001 | 0.6421 |
| score/dist calibrated p | 141 | 16.27% | 0.11285 | 0.37794 | 0.6903 |

raw model 对实际 13.48% 命中率报出平均 40.13%，校准错误很大。相对 market 的
target-date bootstrap delta：Brier `+0.0368..+0.1070`、logloss
`+0.0750..+0.2777`，显著更差。

`score_dist_probability` 已把过度自信压回合理区间，但与 market 基本打平，Brier
delta CI `[-0.0062,+0.0063]`。它是可用的风险/仓位概率，不是已证明的新 alpha。

07-29 后 37 张票具备新 p_cal：

| probability | Brier | logloss | delta verdict vs market |
|---|---:|---:|---|
| market midpoint | 0.09604 | 0.34725 | baseline |
| `p_cal_no_city` | 0.09548 | 0.33374 | point better, CI crosses 0 |
| `p_cal_city_diag` | 0.09322 | 0.32346 | point better, CI crosses 0 |

city diagnostic 的样本只有 6 个日期，且有 city/source memory 风险，不能用于 live selector。

## 4. 哪些升级有价值

1. **保留 D-1 prior，但 market anchored。** 以 fresh ladder midpoint 的 logit 作固定
   offset，只学习 forecast 对市场的连续修正；不再让 raw forecast distribution 覆盖市场。
2. **把 forecast innovation 与 forecast level 分开。** 使用严格 first-seen forecast
   revision、prior rolling bias、lead time 和 forecast uncertainty；不把一个每日 Tmax point
   forecast 伪装成完整稳定分布。
3. **source/city 只作连续可靠性。** 当前 ECMWF 85 张 ROI `+57.9%`、GFS 56 张
   `-55.8%`，但 ECMWF CI 仍跨 0，而且历史 GFS 曾为正；这是 challenger 特征，不是禁 GFS gate。
4. **加入有物理意义的分布宽度。** rain/convective、cloud、wind、station basis、exact
   bracket distance 应进入 residual probability；已有 rain cohort 是正点估但 forward CI 未过。
5. **两阶段表达。** D-1 只建立很小的 cheap inventory；target day 用实际温度路径、peak
   clock、remaining heat 和 fresh market 重新算 residual。退出条件应是更新后的
   `P_yes < executable bid + exit cost`，不是固定持有 20 分钟或机械等到结算。

## 5. 下一轮固定比较

在同一 D-1 complete-ladder denominator 上冻结四臂：

1. market midpoint baseline；
2. old raw empirical-error probability（negative control）；
3. rank-preserving calibrated old disagreement；
4. market-logit offset + forecast innovation/bias/uncertainty/physical-width residual。

先比较 expanding OOF Brier/logloss 与 date-block CI，再比较 fee-adjusted settlement hold 和
30/60/120m executable-bid repricing。maker 只在有 order lifecycle/queue evidence 时单列，不能
把挂单价当成交价。冻结 2026-08-05 以后日期作为未看 forward buffer。

## 数据与复现

- production manifest / controller health：2026-08-04 均 healthy；canonical DB route 同
  device/inode，无 critical finding；
- frozen journal：`runtime/weather_edge_v1/low_price_yes_lottery_tiny_live_v1/would_live_entries.jsonl`；
- evaluator：`scripts/analysis/forecast_quality/evaluate_low_price_yes_lottery_shadow_settlement_v1.py`；
- output：`docs/analysis/2026-08/generated/heada_would_live_settlement_20260804/`；
- settlement overlay：Polymarket CLOB market endpoint，149 condition calls，0 errors；不写 canonical settlement。

```text
significance = FAIL
baseline = raw model FAIL; calibrated heads TIE/INCONCLUSIVE vs market
forward = PARTIAL (15 target dates; latest p_cal only 6 dates)
conclusion = keep D-1 research + zero-notional shadow; no live change
```
