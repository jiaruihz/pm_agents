# Repeat-weekend box office v3：行业预测与可执行盘口

## 结论

方向性模型不能交易。Boxoffice Pro 的周三预测已实现自动化采集，但在同分母 Polymarket 样本上明显弱于市场；chronological market-anchor 最终给它的权重接近 0。该分支被冻结为 shadow evidence，不生成方向性订单。

可实际运行的是 model-free market-structure shadow：实时批量读取全部 YES/NO 深度和 Gamma feeSchedule，在同一电影/周次的一个或多个 exhaustive partition 上，用 binary minimum-cost cover 搜索全状态最低保底组合。它覆盖单市场 YES+NO、完整 bracket basket 与跨 partition strike 不一致；当前只记录 zero-notional candidate，因为多腿 taker 不是 atomic execution。

## 行业预测证据

- 31 篇 Wednesday Weekend Preview，91 条 Top-3 forecast；88 条匹配最终票房。
- repeat-weekend：51 条、28 个 target weekend。
- midpoint MAPE 20.84%；原发布区间覆盖率仅 35.29%。
- Polymarket 同分母：34 个 partition、23 个 target weekend。
- log loss：行业 2.1552，市场 1.2928；Brier：行业 0.8739，市场 0.6275。
- walk-forward industry weight：0.00000596；融合后 log loss 1.292762，与市场等同。

## 交易效用

行业模型单独按 10pp edge、2c execution stress：25 笔、19 个 target weekend、3 胜，胜率 12%，入场中位数 13.5c；投入 4.9857、PnL -1.9857、ROI -39.83%。逐笔 PnL 的 0/25/50/75/100% 分位为 -0.3254/-0.2845/-0.1349/-0.0378/+0.6491。0–10c 的 7 笔和 10–25c 的 8 笔全部失败。按 target-weekend bootstrap 的 ROI 中位数 -41.55%，95% 区间 [-100%, +10.17%]。

market-anchored final 在 10pp/2c 下 0 笔；5pp 下只有 1 笔且失败。所有 edge/spread utility grid 均保存在 summary.json，未从切片挑选新 gate。

## 当前实测

2026-08-08 19:04 UTC 一次完整 shadow：3 个 open repeat partition、2 个 movie/week、18 个 binary contract、36/36 token books；5/10/25 shares 共 6 个 minimum-cover candidate，正 edge 为 0。

- Spider-Man Week 2 最接近：5 shares 成本 5.00575、保底 5，差 -0.00575（-0.115%）。
- The Odyssey Week 4：5 shares 成本 5.13437、保底 5，差 -0.13437（-2.617%）。
- orders submitted = 0，venue write calls = 0。

当前 run：`runtime/box_office_repeat_weekend_shadow/runs/20260808T190418Z/`。冻结合同见 `frozen_shadow_manifest.json`。

## 数据时钟

Boxoffice Pro 周三发布预测；盘口由 Gamma discovery + CLOB batch books 实时读取；最终 label 来自 The Numbers weekend chart。当前周末尚未出 final chart，明确记录为 coverage gap，没有拿 Friday daily 或盘口反推 final label。
