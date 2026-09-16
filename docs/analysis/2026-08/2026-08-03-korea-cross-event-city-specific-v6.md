# Korea CrossNO city-specific v6 — 已撤回当前绩效口径

> **2026-08-03 勘误：** 本报告输入被旧上游硬编码截断在 `2026-07-22`，不能称为截至8月3日的
> current holdout。下列数字仅保留为 stale partial diagnostic；在补齐 `2026-07-23..2026-08-03`
> 的 Busan/Seoul AMOS、routine/WU、盘口与 settlement 前，不发布当前模型胜率或 ROI。
> 这不是韩国没有后续数据：既有 expanded replay 已使用 `2026-07-21..28` 的174个 holdout states，
> 8月2日 WCIR census 也确认 Busan/Seoul AMOS raw 分别已有7,184/19,778行。污染点是本报告引用的
> 旧 timing CSV 没有继续物化这些 raw。

## 结论

Busan 与 Seoul 已拆成两个模型。两城只共用 event/WCIR schema、特征定义和 previous-NO 表达；
`P(next routine)`、`P(final WU leaves bracket)`、market baseline、交易 replay 与 verdict 均按城市独立。

输入仍是旧的 `2026-07-09..2026-07-22` partial archive；
7月23日以后必须补齐后重训，当前不进 shadow。

| city | qualified events / dates | train P(final NO) | holdout rows / dates | logloss Δ vs market | orders / active days | ROI | verdict |
|---|---:|---:|---:|---:|---:|---:|---|
| Busan | 71 / 14 | 90.91% | 19 / 3 | -0.1929 | 3 / 3 | -11.64% | FAIL_EXTERNAL_NEGATIVE_CONTROL |
| Seoul | 44 / 11 | 93.33% | 5 / 2 | +0.0378 | 1 / 1 | +7.82% | INCONCLUSIVE_NO_CITY_FORWARD_NEGATIVE_CONTROL |

## 城市差异

- Busan：train `9/9`，
  `P(final NO)=90.91%`；holdout 已出现2个 terminal-false rows，
  且8月1日外部负面对照失败，`forward=FAIL`。
- Seoul：train `13/13`，
  `P(final NO)=93.33%`；当前 partial holdout 无失败，
  但只有 `2` 个评分日，`forward=NA`，不能借用Busan结论。

## 表达口径纠正

旧 artifact 的 `selected_first_executable` 属于10-share规则，但回放实际只买5 shares；同时它在模型 edge
之前去重，会让昂贵的早期 quote 阻断同档稍后的正 edge。v6 已改为：每个模型 checkpoint 都先计算
`edge=P(final NO)-ask-fee`，要求5-share深度，再按 city/date/bracket 选择第一笔正 edge。

Busan 因此是：`71` 个机制事件 / 14天 →
`33` 个 direct ask / 9天 →
`6` 个模型正-edge表达 /
`6` 天。这里全窗数字只诊断频率，不能计算绩效；
冻结 holdout 的订单数见上表；
不能再把它描述成“完整14天只有2单”。

## 动作

`Busan significance=FAIL baseline=FAIL forward=FAIL`；
`Seoul significance=FAIL baseline=FAIL forward=NA`。

保留两个独立 artifact identity；补全7月23日以后各自的AMOS、routine/WU、盘口和settlement后分别重训，
不再发布 Korea pooled probability 或 pooled ROI。
