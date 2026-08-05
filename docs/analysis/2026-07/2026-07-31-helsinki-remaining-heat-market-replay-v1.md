# Helsinki remaining-heat v2 × Polymarket replay v1

Status: `research counterfactual / displayed-book taker replay / no live change`

## 人话结论

当前真正能做深度成交回放的 Helsinki 盘口只有 `2026-07-15` 到 `2026-07-29`，共 `15` 个已结算日。
按冻结 v2 模型、每个 `(日期, 当前官方最高档 X)` 只取第一次正 edge、买 5 shares，共触发 `35` 次：投入 `$111.02`，结算净收益 `$13.98`，ROI `12.6%`，胜 `25` / 负 `10`。
同一批信号改成 10 shares：投入 `$223.08`，净收益 `$26.92`，ROI `12.1%`。
但 5-share ROI 的 target-date bootstrap 95% CI 是 [`-6.3%`, `25.7%`]，跨 0；而且同分母概率分数仍落后 market，所以现在只能叫“这 14 天账面赚钱”，不能叫已确认优势。
这不是实盘成交记录，而是按当时盘口 ask 深度扫单、计 5%×p×(1-p) Weather fee 的研究重放。

## 完整例子

| 日期 | 信号时间（Helsinki） | 当时状态 | 模型判断 | 买入 | 最终最高档 | 结果 | 净收益 |
|---|---|---|---|---|---:|---|---:|
| 2026-07-15 | 2026-07-15T16:02:25.188618+03:00 | 官方当前 X=26；FMI 25.7°C；距预报峰值 60m | P(再升档)=27.7%，fee后 edge=6.3% | E1 current X NO，5份，VWAP 0.206，含费成本 $1.07 | 26 | 输 | $-1.07 |
| 2026-07-16 | 2026-07-16T11:02:35.167394+03:00 | 官方当前 X=22；FMI 21.6°C；距预报峰值 60m | P(再升档)=89.7%，fee后 edge=35.5% | E1 current X NO，5份，VWAP 0.530，含费成本 $2.71 | 22 | 输 | $-2.71 |
| 2026-07-22 | 2026-07-22T14:42:41.733438+03:00 | 官方当前 X=18；FMI 17.7°C；距预报峰值 140m | P(再升档)=87.9%，fee后 edge=57.9% | E1 current X NO，5份，VWAP 0.290，含费成本 $1.50 | 19 | 赢 | $3.50 |
| 2026-07-29 | 2026-07-29T16:21:39.273743+03:00 | 官方当前 X=24；FMI 23.4°C；距预报峰值 40m | P(再升档)=7.8%，fee后 edge=0.5% | E1 current X NO，5份，VWAP 0.070，含费成本 $0.37 | 24 | 输 | $-0.37 |

所有触发逐笔见 `primary_trades.csv`，不是只展示上面几个例子。

## 模型概率有没有打败盘口

| 同分母指标 | v2 | market | v2-market | 95% target-date bootstrap |
|---|---:|---:|---:|---:|
| brier | 0.0897 | 0.0625 | +0.0272 | [-0.0290, +0.0899] |
| logloss | 0.2703 | 0.2179 | +0.0525 | [-0.0822, +0.1975] |

同分母只有 `534` 个 checkpoint / `14` 个日期；负 delta 才代表模型更好。样本仍小，CI 若跨 0 就不能说稳定打败 market。

## 所有表达与分情况总账

| 表达 | 触发 | 胜 | 投入 | 净收益 | ROI |
|---|---:|---:|---:|---:|---:|
| E1/E2 primary router | 35 | 25 | $111.02 | $13.98 | 12.6% |
| E3 current X YES（diagnostic） | 31 | 6 | $35.11 | $-5.11 | -14.6% |
| E4 X+1 YES（diagnostic） | 39 | 9 | $41.70 | $3.30 | 7.9% |

primary 35 次最终全部选择 direct current-X NO；upper-YES strip 在任何首次正 edge 时点都没有更便宜。
E3/E4 是平行诊断，不能与 primary 收益相加，不能假装成同时可持有的总策略。

| 分情况（primary 5 shares） | 笔数 | 胜率 | 净收益 | ROI |
|---|---:|---:|---:|---:|
| hour_bucket=00-10 | 6 | 66.7% | $-2.17 | -9.8% |
| hour_bucket=10-14 | 16 | 93.8% | $6.59 | 9.6% |
| hour_bucket=14-18 | 12 | 50.0% | $9.57 | 46.8% |
| hour_bucket=18-24 | 1 | 0.0% | $-0.01 | -100.0% |
| peak_clock=within_peak_60m | 9 | 22.2% | $-1.65 | -14.2% |
| peak_clock=before_peak_gt60m | 26 | 88.5% | $15.63 | 15.7% |

这些切片只用于找结构性错误：旧字段实际是
`forecast_minutes_to_future_peak <= 60m`（范围 0–730m，并非严谨的 signed ±60m），
该组 9 笔合计为负，是当前最明显的坏场景；没有把它事后改成过滤阈值。
后续 v3 已改用 signed day-peak clock 单独报告峰前、峰中、峰后。

## 漏斗

signal funnel 与 evidence funnel 已分开写入 `funnels.csv`。晚上/已过峰值但仍不升温的 10 分钟 checkpoint
仍在 signal 分母；book 缺失、深度不足和 settlement 缺失只记 evidence gap，不伪装成策略过滤。
`2026-07-28` 有天气观测和盘口，但缺 FMI 原始 first-seen 到达时间；因此保留在 15 日 signal 分母，不能进入 PIT 成交回放。

## 资格

目前 settled dates=15，未达到预注册 forward 的 30 dates；first-positive executable=35，未达到 50。
因此可以继续 collector/shadow 研究，但不具备改 live 或真实下单资格。
