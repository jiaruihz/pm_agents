# Weather Strategy Search Reset v1

> Target：找 fee-adjusted、PIT、可执行、能在 frozen forward 重复的 alpha；不是继续从历史切片挑最高 ROI。数据同步与 fact rebuild 于 2026-07-14 完成，CLOB fill coverage gate=true。

## 结论

现在仍没有一条达到“confirmed、可以扩 live”的策略。更重要的是，本轮不能再把快源 4–5 个可结算交易日包装成主方向；那正是从全量研究重新滑回稀疏切片：

1. **全量证据优先于事件故事。** 近 50 天同 bracket 连续状态上的简单价格反转，BUY NO 3,043 条 ROI -16.1%，BUY YES 649 条 ROI -9.6%；不能靠“市场反应慢/反应过头”的直觉直接交易。
2. **物理条件更适合作为条件概率，不适合直接映射买卖方向。** “forecast peak 尚未来、仍在升温”不支持买 cheap current NO；纠正分母后该表达 post-hypothesis ROI -68.6%。同状态 current YES 的 +10.0% 只有 16 条，不能抵消宽分母反转失败。
3. **快源只保留 collector-only。** 两个月盘口历史不等于两个月 profile-matched 快源 PIT 历史；后者持续保存始于 7/08 UTC。canonical 机会绩效只有 4 个可结算交易日，不能回答近 60 天 alpha，更不能推荐 5-share。
4. **纯盘口组合没有散户 taker 空间。** all-NO / leave-one-out / any-k 的 forward 总利润只有 1–2 美分，最小 friction 即归零；all-YES 旧 +3.16% 是漏 fee，纠正后 -0.42%。

## 同口径结果

| family | evidence | fee-adjusted result | verdict |
| --- | --- | --- | --- |
| low-price YES P0 actual live | 67 fills；settled through 2026-07-13 | cash+fee $32.975249；PnL -$1.175249；ROI -3.564%；open cash $0.74 | forward 尚未确认，不能把早期正收益当 alpha |
| fast-source previous NO actual live | 11 settled fills / 5 dates / 3 cities | actual ROI +3.789%；share-capped size-only counterfactual +6.900% | 方向最有希望，但日期太少、source/basis 混杂，未过 gate |
| fast-source event-time replay | 旧口径 58 rows / 5 dates 混入 7/13 condition fallback；canonical-only 只有 4 dates | canonical first-row 56 rows ROI +5.85%，但仅 4 dates | rejected-as-main-strategy；collector-only |
| physical peak-runway BUY current NO | 16 post-hypothesis rows / 9 dates | ROI -68.6%；CI [-100%,-8.7%] | reject |
| same-state BUY current YES reversal | 16 rows / 9 dates | ROI +10.0%；CI [-4.4%,+19.7%]；baseline excess CI positive | new shadow hypothesis；绝对 gate FAIL |
| broad price direction | momentum：上涨买 YES 3,133 rows / 49 dates、下跌买 NO 632 rows / 48 dates；reversal 亦为宽分母 | momentum fee ROI -2.9% / -8.5%；reversal -16.1% / -9.6% | 无裸方向 alpha；weather interaction 也未在 proper score 上胜 market |
| all-YES underround old denominator | 270 settled baskets / 25 dates | gross +3.16% → fee-adjusted -0.42%；CI [-0.68%,-0.19%] | old confirmed verdict withdrawn |
| broader full ladder | 3,419 PIT snapshots | NO-side forward only 1–2 opportunities；ROI +0.038%..+0.059%，friction kills | reject live |

## 快源：保留采集实验，不作为主策略

### 如果以后存在 alpha，它只能来自哪里

快源的价值不是“天气模型比市场聪明”，而是 **source event 到 settlement-facing observation 之间的时间差**。当快源打印温度 `T`，旧 running-high exact bracket `(T-1)` 可能已经输，但市场还没完全重定价。交易只在下面这个 residual 为正时成立：

```text
p_win = P(final settlement bracket != previous bracket
          | source, city, source_basis, boundary, observation_age, path_state)
cost  = executable previous-NO ask + official fee + execution friction
edge  = p_win - cost
```

关键变化是：`p_win` 不是 1。Tokyo JMA→next METAR 的 raw confirm rate 约 90.8%，FMI 77.1%，Busan AMOS 83.1%，Singapore MSS 63.3%；这些是 source-specific prior，不可混成一个 deterministic cross。Busan 7/11、7/13 两次实际亏损都是 source/basis false cross，说明半度边界、runway/station channel 和 settlement source 必须进入概率校准。

### 冻结 collector 规则

- grain：每个 `(city, local_date, source, source_obs_ts, previous_official_running_bracket)` 只取 first-seen event。
- label：优先 canonical `settlement_outcomes`；没有 source-grain label 时不进入机会绩效。7/13 目前只能给实际 fill 找 condition settlement，不能混进“所有机会”分母。
- calibration：按 target date expanding，先 source-level shrinkage，再在样本足够时加入 city、source/settlement basis、整数/半度边界、source-to-official delta 和 age/path features。
- expression：primary 只评 previous NO；current YES reversal 独立记账，不能用同一 ROI 合并。
- execution：同 event fresh quote、official fee、5-share depth；shadow 记录全部 score，不为了改善 ROI 临时 blacklist Busan。source/basis 未校准的城市状态为 unsupported-for-this-head，不影响 lottery、forecast、regime 等其他策略。
- promotion：至少 10 个独立 labeled target dates，绝对 ROI CI 与同价 baseline excess CI 均 >0，且真实 5-share fill/share-cap audit 通过，才重新讨论 tiny-live；当前不推荐运行。

## 为什么之前反复“有物理逻辑却不赚钱”

- 预测的是天气事件本身，而不是 `P(outcome)-market price` 的 residual；物理特征有判别力，不等于盘口没定价。
- exact bracket 的方向经常反直觉：继续升温会让 current YES 输、current NO 赢；但当 current NO 已经很便宜时，市场往往已经包含大部分升温概率。
- 多个旧正结果漏了 Weather taker fee，或由 launch-stage 宽盘口 / 小日期簇驱动。
- 旧 full-ladder denominator 要求 current 上方同时存在多档，错误丢掉大量本来可交易的 current 表达；纠正后有 26,383 states，但 edge 仍未出现，说明不是“多补数据就会自然变正”。
- 快源 source print 与结算站不是同一个物理量；不做 source-basis calibration 会把领先信息变成错误确定性。
- 执行层还把 FOK BUY 的 USDC spend 误当 hard share cap，14 fills 中 12 笔 over-cap；该 bug 让已结算策略 PnL 比同 VWAP capped counterfactual 少 $1.570414。

## 当前动作

- 停止扩展 generic hard-filter / synthetic basket 搜索；这些方向已有足够反证。
- 主研究回到全量、连续 residual：模型必须先在同分母 proper score 上 forward 打败 market，再谈交易；不能从 selected-trade ROI 反向挑规则。
- 快源只继续收集 first-seen source→official→book→settlement 链；`current-YES reversal` 也不再因 16 条正收益升格。
- taker 方向策略若继续不能覆盖 spread/fee，下一层只研究可验证的 maker fill/queue/spread capture，不用假设成交的回放冒充收益。
- 不把任何新结果升级 live。fast-source share-cap 修复已在本地完成并有持久 pause，但当前旧 FOK live process 尚未重启，仍是修复前代码；生产停机/重启需单独确认。

## 证据入口

- [peak-runway replay](2026-07-14-peak-runway-current-no-v1.md)
- [all-YES fee correction](2026-07-14-all-yes-underround-fee-correction-v1.md)
- [current-NO fee correction](2026-07-14-current-bracket-no-prevday-pit-fee-correction-v1.md)
- [full-ladder distribution audit](2026-07-14-full-ladder-distribution-arb-v1.md)
- [fast-source share-cap incident](2026-07-14-fast-source-share-cap-incident-v1.md)
- [broad price direction](2026-07-14-broad-price-reversal-v1.md)
- [weather/climate feature review](2026-07-14-weather-climate-feature-review-v1.md)
