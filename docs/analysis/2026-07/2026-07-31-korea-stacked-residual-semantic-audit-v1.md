# Korea stacked residual semantic audit v1

## 结论

用户质疑成立。`83.5%` 不是“删掉32档后，在剩余档位中正规化”的结果；
最终用于交易的 `market_offset_routine_short` 根本不是 joint full-ladder distribution，而是以当前
market favorite 为 target 的单独 binary residual：

`logit(P(favorite exact YES)) = market_logit + Xβ`。

长期 remaining-heat prior 本身输出五档且和为1，但进入 residual 后只把当前
favorite bucket、expected delta、entropy 当普通特征；最终没有同时输出33/34/35
等档位，也没有约束全 ladder 概率和为1。市场 favorite 一变，binary target 也跟着
变，因此不能持续重估原持仓。

## 12:46：33 YES 为什么是83.5%

- source/routine rounded base 都是33，尚未发生 source cross。
- market 33 YES：`73.00%`。
- routine prior：`negative=1.44% / zero=70.69% / plus1=16.43% / plus2=7.25% / plus3=4.19%`；33 为 zero bucket `70.69%`，
  更高档合计 `27.87%`。
- physical prior：33 `78.06%`，更高档合计
  `20.83%`。
- binary residual 在 market logit 上再加 `+0.6252`，
  得到 `83.48%`。

所以 prior 确实给34及以上留了概率；但最终83.5%没有从同一个 simplex 中扣除这些
upper mass。它只是一条 favorite binary probability，无法证明全 ladder 自洽。
negative bucket仍有约1%，是历史 source→settlement basis，不是机械把所有已低于
running max 的档位硬置0。

## 14:23：为什么 source max 34.1 仍给33 YES 86.6%

- market 33 YES：`48.00%`。
- routine max仍为33，因此 routine prior仍给33 `81.92%`：
  `negative=1.18% / zero=81.92% / plus1=10.99% / plus2=4.00% / plus3=1.91%`。
- AMOS source rounded max已经34；source-state physical prior为：
  `negative=0.88% / zero=88.93% / plus1=7.28% / plus2=2.26% / plus3=0.65%`。其中 negative `0.88%` 是“最终低于source base 34”的
  **总概率上界**，33 exact 只能小于等于它。
- primary使用 routine prior，不使用 `favorite_offset_from_source=-1` 的
  physical bucket约束；raw source变量只作为线性特征。
- feature residual总计 `+1.9472` logit，把市场48%
  推到 `86.61%`。

最大正向贡献：

| feature | raw | logit contribution |
|---|---:|---:|
| source_running_max | 34.1000 | +0.4453 |
| dewpoint_depression | 9.9000 | +0.2869 |
| relative_humidity | 56.3350 | +0.2656 |
| source_margin | 0.8000 | +0.2298 |
| favorite_offset | 0.0000 | +0.2134 |
| wind_speed | 13.1000 | +0.1838 |
| routine_entropy | 0.6628 | +0.1708 |
| routine_expected_delta | 0.2354 | +0.1329 |

这里有明确的结构性错误：`source_running_max` 和 `source_margin` 越高，线性系数反而
把“当前favorite正好获胜”的概率向上推；模型缺少
`favorite - rounded(source max)` 的约束/交互，也没有显式估计
`P(AMOS cross is terminal false | routine confirmation, persistence, basis)`。

## 影响半径

holdout 中 source rounded max 已超过当前 favorite 的 state 只有
`6` 行 / `3` dates /
`3` city-days：

| probability | date-equal Brier | date-equal logloss | mean P(favorite YES) |
|---|---:|---:|---:|
| p_raw_market | 0.250689 | 0.664786 | 64.40% |
| p_physical_bucket | 0.328335 | 1.640939 | 1.09% |
| p_routine_bucket | 0.239216 | 0.677074 | 61.69% |
| p_market_offset_routine_short | 0.472237 | 1.244831 | 87.99% |

这个小切片包含 Busan 07-22、07-26 的 true overshoot，也包含07-27的 AMOS
terminal false-cross，因此不能把 AMOS 34.1 直接当 settlement 33=0。正确问题是
校准 false-cross mixture；当前 binary residual 在该切片 date-equal logloss
`1.244831`
，明显差于 market
`0.664786`，
但只有3个 city-days，仅作语义 negative control。

12:46 的 favorite-binary probability 确实触发了回放买入；14:23 没有产生新单，因为 first-signal policy
已进入 HOLD，但它暴露出无法重估 held 33 YES 的问题。actual fills=`0`，不涉及真实
资金影响。

## 正确重构方向

1. 直接输出固定 exact-bracket full ladder（含上下tail），同一时点概率和严格为1。
2. 用 full-ladder market-implied distribution 做 centered multinomial/log-ratio
   offset，而不是每次对favorite单独做binary residual。
3. settlement-facing routine/WU确认后的下档可hard mask；AMOS快源cross只能通过
   学出的 terminal-false-cross mixture降权，不能直接置0。
4. 每时点保留原持仓 bracket 的概率、bid/depth与markout，favorite切换不能改变
   target identity。

结论：`model_semantics=FAIL`，现有 v2 binary residual 不应继续用于
entry/position优化；长期 prior 资产保留，重构 residual head。
