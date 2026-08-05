# Helsinki market-offset residual v1

## 数据快照

- 数据源：frozen v7 replay + Helsinki active-bracket telemetry；research replay，actual fill=0。
- 固定天气 checkpoints=2115；full-ladder market rows=534；active matched=231；combined unique=598。
- unsettled/missing settlement 不进入概率分数；2026-07-31+ 不读标签、不调参。

## 两个漏斗

- signal funnel：2,115 weather checkpoints → expanding OOF 362 rows/9 dates → state-offset raw positive-edge 28 date-X signals；其中 research-actionable 19。
- evidence funnel：534 full-ladder rows + 231 active matched rows → 去重后598 settled market rows/14 dates；前5日只训练，故评分使用362 rows/9 dates。
- active book 是 source detect 后0–120秒首份盘口；full-ladder 是 source 前最近一份盘口，二者不混写时钟。

## 结论

- checkpoint 上 state offset Brier/logloss=0.06773/0.21575，market=0.07641/0.24874；Brier delta=-0.00868。
- date-X entry 上 state offset Brier=0.07316，market=0.08180；负 delta 才表示 residual 有增量。
- raw positive-edge：28笔/3错，ROI=14.33%，date CI=[4.87%,21.82%]；research-actionable cost<0.98：19笔/3错，ROI=24.04%，date CI=[6.56%,40.54%]。
- cost<0.98 只用于研究摘要去除统计尘埃，不进入训练、不改最终策略。

## 结构性错误切片

- fade：70 rows，state/market accuracy=81.4%/92.9%，Brier delta=+0.10439；仍明显退化。
- forecast peak 未来0–1h：89 rows，state/market accuracy=89.9%/86.5%，Brier delta=+0.04867；临峰判断仍是主弱点。
- active first-after-source：231 rows/4 dates，Brier delta=+0.00403；新增高频证据本身尚未证明 residual 优于市场。
- 完整 path/peak/local-hour/evidence/price-band 正确率、校准与 proper score 见 `generated/helsinki_market_offset_residual_v1/scenario_scores.csv`。

## 口径与资格

- market logit 是固定 offset；模型只学 prior-date 的 weather/path correction。
- checkpoint Brier delta 的 date-block 95% CI 为 [-0.04832,+0.02865]，仍跨0；date-X logloss点估基本持平。
- 当前点改善主要来自非 active 子集，fade/临峰切片仍差；不能选为生产模型。
- significance/baseline/forward 未全过，保持 inconclusive/collector，不改 live；下一版只能作为预注册 nonlinear state interaction challenger，等待新日期验证。
