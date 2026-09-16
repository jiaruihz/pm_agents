# Korea CrossNO dual-head v5

## 结论

v4 的零单 fallback 已撤回作为策略表达。v5 保留 CrossNO event head：AMOS persistence cross 一旦成立就写
mechanism candidate，概率模型只负责重估，不得删除原始信号。

**覆盖纠正：这不是 Korea 全历史。** 输入 artifact 的窗口被旧脚本固定为
`2026-07-09..2026-07-22`；7月23日以后尚未物化进本训练表。
下文“2个交易日期”只指旧窗冻结 holdout 的 active trade days。

历史 replay 确实恢复了有效信号与交易表达，但 **尚不能进入 shadow forward**：开发窗 holdout 点估为正，
未参与训练的 Busan 8/1 terminal false cross 会让同一 settlement head 买入错误的 39 NO。

## Signal funnel

- qualified CrossNO episodes：`115` / `14` dates。
- quote-state covered：`66`；direct NO ask covered：`52`。
- 10-share executable：`17`。
- first executable per city/date/previous bracket：`11`。
- first-executable active dates：`7`；
  其中 settlement label 已覆盖 `6` 天。

这里盘口缺失只属于 evidence gap；不是零信号。

## 双概率 head

`P(next routine cross)`：path challenger logloss `0.4259`，
expanding base `0.4061`；本轮 path increment
`FAIL`，选择 `expanding_base_rate`。

`P(final WU leaves previous bracket)`：train `22` rows / `5` dates，
`22/22` wins，Beta-smoothed `p=0.9583`。
holdout `24` rows / `3` dates：

| probability | logloss | Brier |
|---|---:|---:|
| CrossNO settlement prior | 0.3039 | 0.0781 |
| same-row market | 0.4266 | 0.1468 |

model−market logloss `-0.1227`，date-block CI
`[-0.4689,+0.3173]`。

## 交易表达

`edge = P(final leaves previous bracket) - previous NO ask - fee`。

holdout：`3` orders / `2` dates，
`3` wins，PnL `$+3.3556`，
ROI `+28.82%`，CI `[0.2357960689327047, 0.4074595355383535]`。

这证明表达不是零单，但只有2个独立日期，不能算有效性确认。

## 未参与训练的负面对照

Busan 2026-08-01：38 NO 胜、39 NO 负，2笔成本 `$18.895`，
合计 PnL `$-3.895`、ROI `-20.61%`。
当前 settlement head 会放行错误的39 NO，因此 forward=`FAIL`。

## 动作

`significance=FAIL baseline=FAIL forward=FAIL conclusion=inconclusive`。

保留非零 CrossNO candidate/previous-NO expression；暂停把它送入 WCIR forward shadow。
下一版必须直接建模 AMOS→routine/WU basis、forecast peak/remaining heat 和 terminal-false probability；
不能再用 persistence 或 market fallback 代替 settlement head。
