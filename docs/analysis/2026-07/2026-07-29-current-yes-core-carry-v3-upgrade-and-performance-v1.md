# Current-YES Core Carry v3 升级与收益复核 v1

## 数据快照

- 概率层：9 份 feature-factory 历史分片，排除 2026-07-02 至 2026-07-05 的已知 fallback 污染；current v3 expanding OOF 共 1,350 rows、31 target dates、773 city-days。物理 challenger 的共同 forward 分母为 909 rows、23 target dates，frozen window 为最后 8 个 target dates。
- 历史交易层：current v3 的 frozen 5-share full-ladder replay 为 104 entries、95.19% win rate、fee-adjusted ROI +4.38%，target-date block bootstrap 95% CI `[+0.83%, +7.78%]`。
- 实盘层：截至 2026-07-29 21:31 CST，CLOB fill coverage gate `gate_pass=true`，但后续 authenticated order-id 审计发现 Core Carry maker 有 5 个 cache fill rows / 17.38 shares 属于账户级 public activity 错配。剔除后 Core Carry 为 34 fills，其中 33 fills 已结算、1 fill 未结算；unsettled=2.94%，missing bracket=0。
- canonical `weather.db` 当时被并发 coverage refresh 长时间占用，Core Carry 实盘切片无法再次从 `fact_trades` 返回。下列实盘金额已按 authenticated maker order identity 修正；fill-lineage 证据见同日 maker 审计 v2。

目标指标：判断“加入暖平流、露点上升、强风/垂直混合语义后，Core Carry 的同分母 PIT 概率质量是否优于当前 v3”，以及报告 Core Carry 当前历史 replay 与 tiny-live fee-adjusted 收益。概率粒度为 checkpoint row，交易粒度为 physical fill；两层不混用。

## 结论

**当前不升级概率模型，也不回退 v2。** 生产已是移除错误 forecast peak-clock 的 v3；把暖平流和垂直混合整体塞进概率头，在共同 forward 分母上显著恶化 Brier。暖平流单项在 frozen 8 天只有极小点估改善，置信区间跨 0，保留为 shadow telemetry，不足以替换 v3。

收益方面，历史 frozen replay 为正且区间为正；tiny-live 合并 v2+v3 暂为负，亏损集中在旧 v2 的 Singapore/Chengdu overshoot。maker fill-lineage 修正后，v3 tiny-live 目前 9 个已结算 market 全胜、fee-adjusted PnL `+$5.04`、ROI `+8.40%`，但样本极小，不能据此扩 size。

## 概率层 A/B

共同的 909-row expanding OOF：

| 模型 | Brier | Log loss |
|---|---:|---:|
| current v3 | 0.075066 | 0.265807 |
| raw market | 0.076888 | 0.275981 |
| calibrated market | 0.076831 | 0.276059 |
| 暖平流 + 垂直混合 challenger | 0.077280 | 0.272094 |
| 暖平流单项 challenger | 0.075258 | 0.266153 |

相对 current v3：

- 完整物理 challenger：Brier Δ `+0.002244`，95% CI `[+0.000072, +0.005214]`；明确更差。
- 暖平流单项：Brier Δ `+0.000174`，95% CI `[-0.000535, +0.001091]`；没有可辨别提升。
- frozen 后 8 个 target dates，暖平流单项 Brier Δ `-0.000174`，95% CI `[-0.000979, +0.000790]`；点估略好但区间跨 0。

current v3 本身相对 calibrated market 的全历史 proper-score 点估更好（Brier Δ `-0.000946`、log-loss Δ `-0.004728`），但 target-date CI 仍跨 0，因此仍是 evidence probe，不是 confirmed alpha。

## 收益层

### 历史 frozen replay

- 104 entries，95.19% win rate。
- fee-adjusted ROI `+4.38%`，target-date block bootstrap 95% CI `[+0.83%, +7.78%]`。
- 这是历史 5-share taker ladder replay，不代表 maker 或当前 tiny-live 的真实收益。

### tiny-live 截至 2026-07-29

| 版本 | 已结算 fills | 已结算 markets | 成本（含 fee） | fee-adjusted PnL | ROI | market 胜负 |
|---|---:|---:|---:|---:|---:|---:|
| v2 | 19 | 13 | $92.28 | -$17.28 | -18.72% | 11 胜 / 2 负 |
| v3 | 14 | 9 | $59.96 | +$5.04 | +8.40% | 9 胜 / 0 负 |
| 合计 | 33 | 22 | $152.24 | -$12.24 | -8.04% | 20 胜 / 2 负 |

另有 Guangzhou 2026-07-29 的 v3 taker 5 shares、成本约 `$4.45` 尚未纳入 realized PnL。

两个旧 v2 失败 market：

- Singapore 31 YES：2 个有效 fills，fee-adjusted `-$8.28`；另一个 maker cache fill 已由 authenticated order state 证明为误配。
- Chengdu 29 YES：2 fills，fee-adjusted `-$13.88`。

按 execution policy 合并 v2/v3 后，maker 为 `-$5.39 / -10.71%`，taker 为 `-$6.85 / -6.72%`；该切片主要反映上述两个 v2 overshoot，不能用来判断 v3 maker 是否已有负 alpha。authenticated lineage 显示 maker 只成交 11/20 个已挂出 intent，不能用 selected fills 代替全机会分母。

## 三门判定与动作

| 检查 | 当前 v3 | 物理 challenger |
|---|---|---|
| 统计显著性 | 历史交易 ROI 通过；proper-score 增量未通过 | 完整版显著更差；暖平流单项未通过 |
| 同分母 market baseline | 点估优于 market，但 CI 跨 0 | 未优于 current v3 |
| frozen forward | v3 暂保留 | 暖平流单项仅微弱点估改善，CI 跨 0 |

动作：

1. 保持 current v3，不改生产 probability head、不扩 size。
2. 暖平流、露点趋势、强风混合作为连续 shadow telemetry 继续积累，不做 Wellington 式 hard guard。
3. 等更多 v3 已结算 city-days 后，按 target_date block 重跑 v3 vs market 与 advection-only challenger；只有 proper-score 和 frozen forward 同时通过，才进入模型升级。
4. 先修复 public-activity fallback 的 order-id 误配，再重建 canonical facts；之后对 34 个有效 fills 做 strategy/instance 精确切片并更新 dashboard。
