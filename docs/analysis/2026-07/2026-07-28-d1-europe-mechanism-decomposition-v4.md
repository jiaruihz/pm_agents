# D1 Europe distance-2 NO 成因拆解 v4

## 数据快照

- 数据源：v2 immutable full-ladder opportunity replay artifacts；不是 fill，不读取或发布 live_real PnL。
- target_date：2026-06-17..2026-07-07；固定 1,190 baskets / 20 dates / 43 cities，settlement 与 executable quote 完整。
- unsettled=0；missing_bracket=0；本次未同步/重建 canonical DB。

## 结论

**主因不是已证明的“欧洲有另一批 market maker”，而是市场没有充分把欧洲候选档位较低的真实 tail risk 反映进价格；模型只负责把这个价格偏差挑出来。**

- Europe-all model ROI +6.35%，US-F -2.00%。Shapley 描述性拆解显示，两地 ROI 差的约 57% 来自欧洲档实际更少命中（NO 更安全），约 43% 来自欧洲 NO 买入成本更低。
- family 相对 US 的拆解接近一半一半：risk 48% / price 52%。
  family-US ROI delta +10.33%，CI [+4.77%, +15.44%]；risk component +4.94%，CI [+0.25%, +9.55%]；price component +5.39%，CI [+1.70%, +9.43%]。
- 因而它既不是纯模型 alpha，也不是纯盘口身份故事；是`underlying tail risk × insufficient price adjustment` 的交互。

## 1. 最直接的证据：市场给了更便宜的价，但欧洲实际更安全

| group | actual P(NO) | model P(NO) | market P(NO) | mean cost | ROI |
|---|---:|---:|---:|---:|---:|
| Europe family | 0.957 | 0.909 | 0.877 | 0.883 | +8.32% |
| Europe other | 0.955 | 0.939 | 0.906 | 0.913 | +4.64% |
| non-Europe C | 0.906 | 0.919 | 0.894 | 0.901 | +0.60% |
| US F | 0.912 | 0.944 | 0.923 | 0.931 | -2.00% |

Europe-all actual P(NO) 约 95.7%（family）/95.5%（其余欧洲），但市场/成本只给到约 88%–91%；US actual P(NO) 91.2%，成本却为 93.1%。市场在欧洲低估 NO，在美国则略高估 NO。

同样在 NO ask<0.75 的便宜带，family 实际胜率 84.8%，US 仅 51.6%。所以结果不只是欧洲平均买价更低；同价带的真实 tail risk 也不同。

## 2. lattice 有影响，但不是“2°C 比 2°F 更安全”这么简单

- `distance=2` 是从挂牌 ladder 两端向内数两个 native ticks；C 市场一个 tick 通常是 1°C，F 市场通常是 1°F，两个 universe 不是同一物理距离。
- 但向内数更大的摄氏 tick 理论上反而会更接近中心、增加命中风险，不能机械地用 2°C=3.6°F 解释欧洲更赚钱。
- 真正可比的连续量显示：selected bracket 距五源 forecast consensus family 平均 5.4F、US 5.6F；除以训练 MAE 后分别约 2.3× 和 1.7×。欧洲候选相对自身 forecast 误差分布确实更深在尾部。
- discriminating basket（两个 NO 中恰有一个输）比例 family 11.5%、Europe-other 4.5%、US 13.0%、non-Europe C 15.1%。这说明欧洲效应不只是 C/F 单位标签，ladder 相对本地天气分布的位置也不同。

## 3. 模型层做了什么

- Europe 训练期平均多模型 MAE 约 2.33F，US 为 3.94F；欧洲 forecast 在这个样本里更可预测。
- Europe-all mechanical-half ROI +3.44%：完全不用模型已经为正，说明底层 carry/定价结构先存在。
- all-5 model 把 Europe-all ROI 提到 +6.35%，主要通过把平均 ask 从 0.926 降到 0.895，而平均 payout 只从 0.961 降到 0.956。
- family 更强：model 相对 mechanical 不但把 ask 从 0.913 降到 0.879，mean payout 还从 0.942 升到 0.957。
- US 同样把 ask 从 0.952 降到 0.928，但 payout 从 0.935 降到 0.912，省下的价格不够补损失，ROI 仍为负。

模型层的边界：family 的 Brier/logloss point estimate 略优于 market，但 CI 跨 0；Europe-all proper score 反而略差。此前 15 个 source ablation / city-best source 没有一个稳定胜过 equal-all5。因此当前只能说模型在 family 内有潜在 ranking value，不能说“更好的欧洲模型已经被证实”。

## 4. 是否是不同定价者

- book spread：family 0.011、Europe-other 0.012、US 0.012；ask depth 中位数分别 85/90/76 shares。
- 距当地中午 lead time 也接近：family 21.4h、US 21.7h。
- 这些快照没有 wallet/order-flow/participant identity，无法识别“主要定价者是谁”。相似的 spread/depth/timing 也没有支持“欧洲因为流动性差所以错价”的强证据。
- 能说的是定价函数有地域差：family 的 market P(NO) 与 forecast distance 相关系数为 +0.66，说明市场会方向性地参考 tail geometry，并非完全忽略 forecast；但 selected rows 的 market P(NO) 仅 87.7%、实际为 95.7%，调整幅度仍不够。可能来源包括参与者注意力、高温 YES 偏好、模板化报价或公开 forecast 使用粗糙；现有证据不能在它们之间定责。

## 成因排序与下一步

1. **最高可信：ladder 相对当地 forecast/error distribution 的位置不同，欧洲 tail 实际更少命中。**
2. **较高可信：市场没有充分为这个较低风险提价，尤其 family 的便宜 NO。**
3. **中等可信：equal-all5 能在 family 内挑到更便宜且仍安全的一侧。**
4. **低可信：由不同 market maker/主要定价者身份直接造成。**当前没有身份或 order-flow 证据。

下一轮 frozen shadow 应把策略从 native `distance=2` 改成并行记录连续`forecast_distance_f / city rolling MAE`、market residual、market unit、tail side 与 local lead time；先验证连续 safety score 是否在新日期单调解释 P(NO)-cost。不要把 Europe 或五城直接设成 hard gate。

## Signal / evidence funnel 与三门

- signal：1,190 fixed paired baskets → 1,190 all5 selections；本报告只做既有分母成因拆解。
- evidence：PIT forecast/book/settlement=完整；actual fill=0，queue/capacity/participant identity=缺失。
- 数据完整性自检：candidate rows=2,380=1,190×2；paired basket violations=0；unsettled=0；missing bracket=0；所有五源概率均 finite。
- 8 环：覆盖描述绩效、统计推断、信号判别、概率评估、target-date 相关性与同分母基准；缺真实执行微结构与容量。
- significance=family historical trade PASS；baseline=trade expression PASS but probability proper-score FAIL；forward=NA；conclusion=`shadow_candidate`，不改 live。
