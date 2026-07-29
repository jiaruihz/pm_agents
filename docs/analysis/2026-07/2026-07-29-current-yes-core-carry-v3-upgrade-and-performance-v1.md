# Current-YES Core Carry v3 升级与收益复核 v1

## 数据快照

- 概率层：9 份 feature-factory 历史分片，排除 2026-07-02 至 2026-07-05 的已知 fallback 污染；current v3 expanding OOF 共 1,350 rows、31 target dates、773 city-days。物理 challenger 的共同 forward 分母为 909 rows、23 target dates，frozen window 为最后 8 个 target dates。
- 历史交易层：current v3 的 frozen 5-share full-ladder replay 为 104 entries、95.19% win rate、fee-adjusted ROI +4.38%，target-date block bootstrap 95% CI `[+0.83%, +7.78%]`。
- 实盘层：截至 2026-07-29 21:31 CST，CLOB fill coverage gate `gate_pass=true`，全账户 1,321 个 distinct fill ids 与 `fact_trades` 对齐、unknown fee lineage=0。Core Carry 独立逐 fill 复核得到 39 fills / 23 markets，其中 38 fills / 22 markets 已结算，1 fill / 1 market 未结算；unsettled=2.56%，missing bracket=0。
- canonical `weather.db` 当时被并发 coverage refresh 长时间占用，Core Carry 实盘切片无法再次从 `fact_trades` 返回。下列实盘金额以已通过 gate 的 canonical CLOB fill ids 回连 Core Carry order lineage，并用 Polymarket closed outcome 交叉核验；应在 DB 解除占用后再固化为 canonical dashboard 数字。

目标指标：判断“加入暖平流、露点上升、强风/垂直混合语义后，Core Carry 的同分母 PIT 概率质量是否优于当前 v3”，以及报告 Core Carry 当前历史 replay 与 tiny-live fee-adjusted 收益。概率粒度为 checkpoint row，交易粒度为 physical fill；两层不混用。

## 结论

**当前不升级概率模型，也不回退 v2。** 生产已是移除错误 forecast peak-clock 的 v3；把暖平流和垂直混合整体塞进概率头，在共同 forward 分母上显著恶化 Brier。暖平流单项在 frozen 8 天只有极小点估改善，置信区间跨 0，保留为 shadow telemetry，不足以替换 v3。

收益方面，历史 frozen replay 为正且区间为正；tiny-live 合并 v2+v3 暂为负，亏损集中在旧 v2 的 Singapore/Chengdu overshoot。v3 tiny-live 目前 9 个已结算 market 全胜、fee-adjusted PnL `+$6.64`、ROI `+9.38%`，但样本极小，不能据此扩 size。

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
| v2 | 20 | 13 | $96.28 | -$21.28 | -22.10% | 11 胜 / 2 负 |
| v3 | 18 | 9 | $70.74 | +$6.64 | +9.38% | 9 胜 / 0 负 |
| 合计 | 38 | 22 | $167.02 | -$14.64 | -8.76% | 20 胜 / 2 负 |

另有 Guangzhou 2026-07-29 的 v3 taker 5 shares、成本约 `$4.45` 尚未纳入 realized PnL。

两个旧 v2 失败 market：

- Singapore 31 YES：3 fills，fee-adjusted `-$12.28`。
- Chengdu 29 YES：2 fills，fee-adjusted `-$13.88`。

按 execution policy 合并 v2/v3 后，maker 为 `-$7.79 / -11.96%`，taker 为 `-$6.85 / -6.72%`；该切片主要反映上述两个 v2 overshoot，不能用来判断 v3 maker 是否已有负 alpha。

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
4. canonical DB 解除占用后，用 `fact_trades` 对上述 39 fills 再做一次 strategy/instance 精确切片并更新 dashboard。
