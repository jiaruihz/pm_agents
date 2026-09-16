# Helsinki `/prices-history` midpoint 代理回放 v1

Status: `descriptive proxy / not executable / no-live-change`

## 结论

本报告只回答扩大 midpoint 覆盖后策略方向是否仍有参考价值，不把 midpoint 当 ask、订单簿或真实成交。

## 数据与双漏斗

- window：2026-07-15..2026-07-29；模型 checkpoint grain，每个 target-date/current-X 只取首次正 edge。
- signal funnel：2,115 weather checkpoints → 1,268 first-seen checkpoints → 1,147 archive-midpoint matches / 14 dates。
- evidence funnel：1,147 midpoint proxy → 0 historical bid/ask → 0 depth → 0 actual fills。
- API 返回 101,962 个分钟点；匹配 age median/p95=29.2/58.1s。
- 与已有 direct book 重叠 522 rows / 14 dates：archive midpoint 对真实 mid 的 median error=0.0000、median absolute error=0.0050；ask-markup proxy 对真实 VWAP5 的 median absolute error=0.0130，且 mean error=-0.0087（仍略乐观）。
- `p_r16_final_descriptive` 使用最终 artifact 回看本窗，存在 post-fit leakage，只作敏感度参考；v7 是天气概率基线。

## 同 rows 概率质量

| probability | Brier | logloss |
|---|---:|---:|
| archive_no_mid_score | 0.04113 | 0.14084 |
| p_break_v7 | 0.05958 | 0.18518 |
| p_r16_final_descriptive | 0.02830 | 0.10396 |

## 5-share 代理交易

`midpoint` 假设5股可在 mid 成交；`ask_markup_median` 按真实 direct-book 同价格带的 median `VWAP5-mid` 加价，再扣官方 Weather fee，但仍不知道当时深度。

| 模型 | 场景 | signals / dates | wins / losses | 盈利/亏损日 | PnL | ROI（target-date bootstrap 95% CI） |
|---|---|---:|---:|---:|---:|---:|
| p_break_v7 | ask_markup_median | 59 / 14 | 46 / 13 | 7 / 7 | $+13.37 | 6.17% [-2.77%, 13.32%] |
| p_break_v7 | midpoint | 62 / 14 | 48 / 14 | 7 / 7 | $+16.93 | 7.59% [-0.76%, 14.48%] |
| p_r16_final_descriptive | ask_markup_median | 62 / 14 | 51 / 11 | 7 / 7 | $+13.51 | 5.60% [-1.81%, 12.09%] |
| p_r16_final_descriptive | midpoint | 64 / 14 | 52 / 12 | 8 / 6 | $+16.91 | 6.96% [-0.27%, 13.26%] |

## 边界

这批数据可以扩 market baseline、概率 residual 和 repricing 研究；不能恢复 spread、深度、滑点、maker queue 或真实可成交 ROI。archive timestamp 也不是 collector-exact first-seen，不能用于秒级 source latency alpha。未增加 price/path/time threshold，且本结果不参与 frozen forward 调参。
