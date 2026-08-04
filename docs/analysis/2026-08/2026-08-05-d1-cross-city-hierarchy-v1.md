# D-1 跨城市 pooled / hierarchical / city-only 概率研究 v1

## 结论

主口径 `D-1_18_24_first` 有 **279 个 city-date checkpoint / 27 个 target dates / 34 城**。
分层模型 logloss=2.9343，相对 pooled 的差值为 0.2771（95% date-block CI 0.1197 到 0.4393），相对 city-only 为 -0.4919（-0.7424 到 -0.2837）。负值才是改善。
但分层模型仍明显输给同时间 market：logloss 差 1.3981（1.0927 到 1.7276）；market=1.5363，hierarchical=2.9343。
因此这版的架构结论不是直接采用 hierarchical：**pooled 是当前最稳的 weather-only baseline；city-only 明显过拟合；hierarchical 虽缓解 city-only，但 logloss 仍显著差于 pooled。** 分层结构只保留为 challenger，等有严格 D-1/D-2 城市样本后再验证。当前纯天气分布不能直接做交易概率源，更不能升 live。

## 同分母准确度

| arm | logloss | Brier | RPS | winner P | top-1 accuracy |
|---|---:|---:|---:|---:|---:|
| market | 1.5363 | 0.0661 | 0.0609 | 0.2693 | 40.0% |
| pooled | 2.6572 | 0.0824 | 0.1243 | 0.1591 | 17.1% |
| hierarchical | 2.9343 | 0.0826 | 0.1212 | 0.1699 | 20.0% |
| city_only | 3.4262 | 0.0847 | 0.1231 | 0.1758 | 19.7% |

评分先在每个 target_date 内平均，再对日期等权；Brier/RPS 采用每 ladder rung 的均值。market 为同 checkpoint bid/ask mid（缺一边时用 snapshot market price）后整条 ladder 归一化。

## 交易效果（只作诊断）

固定表达为：每个 checkpoint 只取 `P(model)-YES ask-official weather fee` 最大且大于 0、top ask size≥1 的一张 YES，立即 taker，持有到结算。没有阈值搜索，也没有 maker 成交假设。

| arm | tickets | dates | cost | PnL | ROI | win rate | median edge |
|---|---:|---:|---:|---:|---:|---:|---:|
| pooled | 279 | 27 | 20.81 | -7.81 | -37.5% | 4.7% | 0.154 |
| hierarchical | 279 | 27 | 26.47 | -10.47 | -39.5% | 5.7% | 0.145 |
| city_only | 279 | 27 | 33.07 | -15.07 | -45.6% | 6.5% | 0.152 |

这不是可部署回测：概率训练来自旧 daily cache（非严格 first-seen PIT），测试 forecast 是 conservative single-run reconstruction，且只有 27 个日期；交易数还由模型 edge 自选，不能用正 ROI 反推 alpha。

## 城市异质性（主 checkpoint）

分层相对 pooled 最好的城市：

| city | states | Δ logloss vs pooled | Δ logloss vs market |
|---|---:|---:|---:|
| Guangzhou | 8 | -0.8330 | 1.2036 |
| Shanghai | 7 | -0.8121 | 0.3106 |
| Houston | 3 | -0.4679 | 0.5913 |
| Manila | 9 | -0.3760 | 1.0333 |
| Karachi | 9 | -0.3686 | 0.5001 |
| Miami | 2 | -0.2902 | 0.2351 |
| TelAviv | 1 | -0.2882 | 0.7409 |
| Chongqing | 12 | -0.2745 | 0.7581 |

分层相对 pooled 最差的城市：

| city | states | Δ logloss vs pooled | Δ logloss vs market |
|---|---:|---:|---:|
| Atlanta | 13 | 2.4812 | 5.3355 |
| Seattle | 14 | 2.3365 | 5.5659 |
| Chicago | 11 | 1.8395 | 3.9806 |
| Austin | 8 | 1.7437 | 3.1171 |
| BuenosAires | 6 | 0.1687 | 0.0175 |
| Lucknow | 11 | 0.1529 | 0.4705 |
| Seoul | 6 | 0.1224 | -0.0164 |
| Dallas | 7 | 0.1152 | 0.7864 |

单城只有约二十几个 forward 日期，城市表只能用于找偏移方向，不能逐城挑赢家后上线。

## 数据与模型合同

- 训练：5561 rows，39 城，2024-05-01 至 2026-05-07，只用 5–8 月；截止早于测试。
- 测试：694 checkpoints；forecast lineage=`single_run_reconstructed_conservative_12h_lag`；日期 2026-06-17 至 2026-07-23。
- pooled：按固定 forecast source（GFS/ECMWF）共享 Normal error distribution；city-only：单城 source-specific error distribution；hierarchical：`w=n/(n+60)` 的分布矩收缩。
- 公共同分母只保留旧 registry 已有固定模型选择的 34 城；严格 D-1 原始 47 城中的其余 13 城记 coverage gap。
- D-2：没有同等级 single-run PIT + full-ladder checkpoint 数据，本报告不输出 D-2 数字。

## 双漏斗

- signal funnel：strict forecast snapshots 1774 → fixed-model assigned 1255。
- evidence funnel：assigned 1255 → settlement/l完整 ladder/market 同时可评分 694；missing settlement=0，invalid ladder=561，missing market=0。
- actual fills：0；本研究未更改 production / shadow / order。

## 下一步

以 pooled 作为冻结 baseline，hierarchical 作为 challenger。下一版不在这 27 天上继续调 shrinkage λ，而是先补干净的 D-1/D-2 forecast run archive，并在同一骨架上加入 lead/run-age、calendar season、multi-model mean/spread；随后训练 `weather-only` 和 `market + weather residual` 两个头，做新的 frozen forward。
