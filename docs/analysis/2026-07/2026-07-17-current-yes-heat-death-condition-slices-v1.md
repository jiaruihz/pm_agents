# Current YES Heat-Death H1 Condition Slices v1

Status: research
Date: 2026-07-17

## 结论

本报告固定 H1 可执行价格带 0.95-0.99，只消融天气机制；每条规则只取首次 city-day 信号。
`late15_assigned_peak_base` 是唯一同时扩大分母并保持 train/holdout 正向的干净 challenger；support count 降为诊断特征。
它仍不能晋升 live：102 个零亏损 holdout 行低于冻结的 120 行尾部门，且完整 live weather support 字段无法历史 PIT 重建。

## Holdout comparison

| Rule | Train rows | Train ROI | Holdout rows/dates | Win | ROI | 95% CI | Losses |
|---|---:|---:|---:|---:|---:|---:|---:|
| published_dual_peak_support2 | 12 | +1.64% | 5/5 | +100.00% | +1.70% | [+1.10%, +2.42%] | 0 |
| assigned_peak_support2 | 14 | +1.83% | 7/6 | +100.00% | +1.49% | [+1.06%, +1.84%] | 0 |
| assigned_peak_support1 | 59 | +2.09% | 66/16 | +98.48% | +0.38% | [-4.09%, +2.16%] | 1 |
| assigned_peak_base | 80 | +2.13% | 119/16 | +99.16% | +1.06% | [-1.04%, +2.09%] | 1 |
| late15_price_only | 215 | +0.10% | 323/17 | +96.90% | -0.81% | [-2.78%, +0.83%] | 10 |
| late15_fade_mature_notwarming | 85 | +1.06% | 133/16 | +97.74% | -0.33% | [-2.47%, +1.90%] | 3 |
| late15_assigned_peak_base | 65 | +2.18% | 102/16 | +100.00% | +1.80% | [+1.50%, +2.14%] | 0 |
| assigned_peak_base_ask97_989 | 38 | +1.96% | 69/16 | +100.00% | +1.82% | [+1.67%, +1.98%] | 0 |
| dual_peak_base | 66 | +2.01% | 96/16 | +98.96% | +0.76% | [-1.88%, +1.98%] | 1 |
| assigned_peak_age30 | 82 | +2.15% | 138/16 | +98.55% | +0.43% | [-2.07%, +1.98%] | 2 |
| no_peak_base | 109 | +0.40% | 169/17 | +97.04% | -0.89% | [-2.99%, +1.14%] | 5 |
| no_decline_base | 81 | +2.12% | 120/16 | +99.17% | +1.08% | [-1.01%, +2.10%] | 1 |

## Coverage boundary

历史支持层只能代理 cloud/humidity/dual-forecast-gap；无法 PIT 重建 live 的降雨、风向、remaining-3h weather 和 solar geometry。
因此 support>=1/2 的比较是机制消融，不是完整 live rule 的伪精确回测。

## Tail risk

`late15_assigned_peak_base` holdout 102 行零亏损，平均 effective cost 0.9823，
保本亏损率仅 1.77%；rule-of-three 上界 2.94%，仍高于保本线。
因此 date bootstrap 正 CI 不能替代未观察尾部风险，本次只注册为 shadow challenger，不修改现有 live。

## Daily opportunity capacity

| Rule | Gross opportunities | Avg / UTC day | Avg / active day | Max | After cap=3 | Capped avg / UTC day |
|---|---:|---:|---:|---:|---:|---:|
| current 13-17 + support>=2 proxy | 7 | 0.39 | 1.17 | 2 | 7 | 0.39 |
| late15 assigned-peak, support diagnostic | 102 | 5.67 | 6.38 | 14 | 47 | 2.61 |

这里的数量单位是 strategy opportunity，不是 exchange child order。H1 每个 opportunity 初始会产生 1 个 taker + 1 个 maker，maker chase 重挂会继续增加订单消息。

## Forecast robustness

这个 challenger 使用 forecast peak clock，不使用 forecast maximum 数值作 gate。固定模型最高温误差统一换算为摄氏度后：

| Absolute forecast-max error | Rows | Win | ROI |
|---|---:|---:|---:|
| <=1C | 55 | +100.00% | +1.85% |
| 1-2C | 23 | +100.00% | +1.69% |
| >2C | 24 | +100.00% | +1.77% |

平均绝对误差 1.27°C，P90 2.67°C，最大 4.11°C；三个误差桶都零亏损。
但 peak clock 本身仍有 model risk：GFS/both-model gate 在本窗零亏损，ECMWF 单独 gate 有 2 次亏损；因此不能解释为 forecast model 无关。
