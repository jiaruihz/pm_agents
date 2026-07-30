# Current-YES Core Carry filtered delta analysis v1

## 数据快照

- 父研究：
  `2026-07-30-current-yes-core-carry-residual-taker-ab-v1`。
- 固定差量定义：bounded/executable/mid-domain checkpoint 中
  `market_mid < p_v3_no_peak_clock <= five_share_taker_cost`。
- 粒度：expanding-OOF checkpoint；`318` rows / `236` city-days / `31`
  target dates / `34` cities。
- 所有 exact labels 已结算；unsettled `0`，missing bracket `0`。
- taker 口径：历史 full 5-share ask ladder 加官方 Weather fee。
- 本报告是 research replay，不是 actual fills。

## 结论

这 `318` 个被过滤 checkpoint 不是一组被误删的 taker alpha：

- 全部买入：`295/318` 胜，正确率 `92.77%`，但平均成本 `0.9507`，
  5-share PnL `-$36.59`、ROI `-2.42%`。
- 其中只有 `181` 个是当前策略完全不会覆盖的新 city-day；每个 city-day 只取第一笔，
  `166/181` 胜，正确率 `91.71%`，PnL `-$18.99`、ROI `-2.24%`。
- 另外 `55` 个 city-day 后续本来就会出现 current-policy signal；其 `70` 个 filtered
  checkpoint 若额外叠仓，正确率虽为 `95.71%`，仍因成本太高而 ROI `-0.69%`。

所以差量负收益同时来自“新增低质量 city-day”和“同 city-day 高成本重复叠仓”，不是只由
一种计数口径造成。

## 差量组成

| filtered cohort | checkpoints | city-days | win rate | avg taker cost | 5-share PnL | fee ROI | date-block 95% CI |
|---|---:|---:|---:|---:|---:|---:|---:|
| 全部差量 | 318 | 236 | 92.77% | 0.9507 | -$36.59 | -2.42% | [-6.02%, +0.93%] |
| 真正新增 city-days，全部 checkpoint | 248 | 181 | 91.94% | 0.9470 | -$34.26 | -2.92% | [-7.16%, +1.18%] |
| 真正新增 city-days，只取第一笔 | 181 | 181 | 91.71% | 0.9381 | -$18.99 | -2.24% | [-6.14%, +1.82%] |
| 与当前 policy 重叠 city-days | 70 | 55 | 95.71% | 0.9638 | -$2.33 | -0.69% | [-7.46%, +4.34%] |

## 为什么模型高于 mid，taker 仍亏

差量组平均：

- `p_model - market_mid = +1.11pp`；
- `taker_cost - market_mid = +2.47pp`；
- 因而 `taker_cost - p_model = +1.36pp`。

模型发现的 residual 平均只有约 1.1pp，但 spread、ladder slippage 和 fee 合计需要约
2.5pp；执行成本是 residual 的两倍以上。

即使做一个不可执行的“所有单都按 midpoint、零 fee 成交”上限：

| 假设 | PnL | ROI |
|---|---:|---:|
| 318 个 filtered checkpoints 全部 mid fill | +$2.64 | +0.18% |
| 181 个新增 city-day 第一笔全部 mid fill | +$4.70 | +0.57% |

这不是 maker 回测：没有 queue、fill probability 或 adverse selection。它只说明即使给出
非常理想的成交价，毛空间也很薄。

## 按 taker shortfall

`shortfall = taker_cost - p_model`：

| shortfall | n | win rate | PnL | ROI | 95% CI |
|---|---:|---:|---:|---:|---:|
| 0–0.5c | 89 | 96.63% | +$2.52 | +0.59% | [-3.23%, +3.73%] |
| 0.5–1c | 81 | 95.06% | -$1.72 | -0.45% | [-6.31%, +4.07%] |
| 1–2c | 68 | 91.18% | -$12.54 | -3.89% | [-10.60%, +2.08%] |
| 2–5c | 73 | 86.30% | -$26.46 | -7.75% | [-16.45%, +1.21%] |
| >5c | 7 | 100.00% | +$1.61 | +4.82% | 仅 5 dates，post-hoc 小样本 |

最接近 gate 的 `0–0.5c` 只有微弱正点估，CI 跨 0；frozen last-8 dates 为
`-$0.10 / -0.10%`。因此连“允许差 0.5c”也没有稳定 forward 证据。

## 按 market mid

| market mid | n | win rate | PnL | ROI | 95% CI |
|---|---:|---:|---:|---:|---:|
| 0.80–0.85 | 38 | 73.68% | -$26.19 | -15.76% | [-33.22%, +0.64%] |
| 0.85–0.90 | 54 | 92.59% | +$3.74 | +1.52% | [-8.39%, +9.86%] |
| 0.90–0.95 | 86 | 95.35% | -$0.66 | -0.16% | [-4.68%, +3.62%] |
| >0.95 | 140 | 96.43% | -$13.48 | -1.96% | [-5.61%, +0.98%] |

没有一个足够宽、CI 和 forward 都成立的价格带可直接改成新 taker gate。

## 时段与城市诊断

- 13/14/15/16/17 点 ROI 分别为
  `-0.93% / -0.96% / -2.53% / -4.82% / -1.09%`；所有小时点估均负，
  16 点最差，但这不足以事后新增 hour filter。
- 最大负 PnL 城市贡献包括 SaoPaulo `-$12.60`、Jeddah `-$8.61`、
  Shanghai `-$8.20`、Wellington `-$7.66`、Atlanta `-$7.14`。
- 正贡献也分散存在：Tokyo `+$4.64`、Amsterdam `+$4.00`、Miami `+$3.50`、
  Manila `+$3.33`、Istanbul `+$3.10`。城市样本小且本轮为 post-hoc 多切片，
  不据此添加 city hard gate。

## 动作

- 不把这组差量转成 taker，下单 gate 不变。
- maker-only 可以继续 zero-notional shadow，但主问题不是“是否能挂到 mid”，而是
  filled subset 是否比全量更 adverse；所有单 mid-fill 的理论 ROI 也只有 `+0.18%`。
- 不从本次 shortfall、price、hour 或 city 切片追加 live filter。

结论：
`rejected_for_taker_expression / maker-only remains inconclusive shadow hypothesis`。
