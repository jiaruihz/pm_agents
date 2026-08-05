# Helsinki freeze 前逐笔直觉复盘 v1

## 结论与动作

- weather probability / market-offset artifact 保持冻结，不再用 7/15–29 的已看日期调特征。
- 暂不冻结成可执行策略：17/28 个回放入场使用 source 前最近 full-ladder，
  只有 11 个入场/4 日使用 source 后 0–120 秒 active book。
- active-only 5-share 为 11 笔、8 胜3负、ROI `+12.16%`，但 target-date
  bootstrap CI `[-12.57%,+35.09%]`；仍是正点估、证据不足。
- 已修正 active 10-share 时钟：现在与 5-share 从同一份 source 后 orderbook
  扫深度。全回放 10-share 为28笔、25胜3负、ROI `+14.22%`；active-only
  为11笔、8胜3负、ROI `+11.94%`，CI `[-12.70%,+34.75%]`。
- upper-YES strip 在28个入场点的同刻可执行覆盖为 `0/28`，不能声称
  E1 current-X NO 与 E2 upper strip 已完成择优。
- 不改 live；继续 zero-notional collector。

## 数据快照

| 项目 | 值 |
|---|---|
| 数据源 | frozen OOF checkpoint、FMI first-seen、active-bracket raw orderbook、EFHK/WU settlement |
| replay 生成时间 | 2026-07-31 17:13 +08:00 |
| probability rows | 362 checkpoints / 9 OOF target dates |
| 5-share signals | 28 date-X / 8 active signal dates |
| post-source active signals | 11 / 4 dates |
| settlement | 28/28 settled；unsettled=0；missing_bracket=0 |
| actual orders/fills | 0；全部为 research replay |
| production manifest | DB route healthy；整体 warning 为未登记 tmux sessions |

## 固定分母与语义

- 每个 `(target_date, current X)`，每10分钟重新估计最终最高温是否超过 X。
- 第一次 `p_offset_fade > 5-share current-X NO effective cost` 产生一次
  research intent；之后同一 date-X HOLD，不重复买。
- `X NO` 只有最终最高温不等于 X 才赢。5-share PnL 已扣 Weather taker fee。
- `full_ladder_before_source` 只是 source 前最近价，不是严格可成交证据；
  `active_first_after_source` 才是 source detect 后首份盘口。

## 代表性案例

### Case 1：2026-07-25，19 NO，获胜但模型修正偏激进

- Helsinki 14:33，FMI first-seen 后11秒取得 active book。
- 当时 official running max=19，FMI=18.1，path=pullback；预报峰值仍在
  1.5小时后，future reheat strength=1.4°C。
- market midpoint=35.5%，5-share effective cost=40.19%；模型=68.42%，
  edge=28.23pp，因此模拟买5份，成本 `$2.0095`。
- 最终最高温=20，19 NO 获胜，PnL `+$2.9905`。
- 直觉：继续升到20是合理判断；但模型从 weather-only 35.9% / market 35.5%
  一次抬到68.4%，幅度过大。正确结果不能证明这种修正幅度可靠。

### Case 2：2026-07-27，20 NO，获胜但只是边缘信号

- Helsinki 16:02，FMI first-seen 后27秒取得 active book。
- FMI=19.9，30分钟升温速度=+0.8°C/h，forecast peak 正好在当前，
  future boundary margin=0。
- market midpoint=30.0%，effective cost=37.15%；模型=38.02%，
  只有0.87pp edge。模拟成本 `$1.8576`。
- 最终最高温=21，PnL `+$3.1424`。
- 直觉：天气还在升，买20 NO不离谱；但这不是强信号，任何轻微滑点或校准误差
  都会吃掉edge。按用户约定不增加最终阈值，forward 中需原样记录这种边缘单。

### Case 3：2026-07-25，20 NO，错误且天气证据互相冲突

- Helsinki 16:33，source 后50秒 active book。
- FMI=20.2、30分钟斜率=+1.2°C/h、path=fresh runway；但预报峰值只剩
  0.5小时，future boundary margin=-1.4°C，预报天花板明显不支持再升档。
- market midpoint=10.5%，effective cost=13.57%；模型=21.28%，模拟成本
  `$0.6783`。
- 最终最高温仍为20，PnL `-$0.6783`。
- 直觉：这是模型过度相信“仍在升温”，没有充分尊重负的 forecast ceiling。
  它属于需要在 frozen forward 监控的 peak-transition 冲突，不应事后加规则删除。

### Case 4：2026-07-26，19 NO，最大错误但入场逻辑当时合理

- Helsinki 14:04，source 后33秒 active book。
- FMI/official 都在19，30分钟斜率=+1.4°C/h，预报峰值还有4小时，
  future margin=+0.6°C、reheat strength=2.0°C。
- market midpoint=44.0%，effective cost=48.25%；模型给到97.00%，
  模拟成本 `$2.4123`。
- 最终最高温停在19，PnL `-$2.4123`。
- 直觉：从当时气象状态看买19 NO有道理，错误来自 forecast/path 大幅失准；
  真正不合理的是97%的过度自信。需要的是概率收缩/可靠性验证，不是价格阈值。

### Case 5：2026-07-29，24 NO，最不符合直觉的错误

- Helsinki 15:32，source 后34秒 active book。
- FMI=23.5、30分钟斜率=+0.8°C/h；但峰值只剩0.5小时，future margin=-1.5°C，
  weather-only 概率仅12.73%，market midpoint=16.5%。
- current 24 NO effective cost=19.77%，market raw ask=19c；offset模型却给
  36.30%，模拟成本 `$0.9885`。
- 最终最高温=24，PnL `-$0.9885`。
- 直觉：这笔不合理。residual 同时逆着 weather-only 和 market，把弱信号抬成
  强正edge。因为该日期已经被看过，不能再针对它调模型；必须把
  “residual correction magnitude / consensus sign flip”作为 frozen-forward
  诊断字段。

## 组合层复盘

- 28笔只来自8个有信号日期；每天会随着 running max 上升连续持有多个 X NO。
- 非当日最高 X 的20笔全部获胜，但投入 `$93.52` 只赚 `$6.48`，ROI `6.93%`。
- 每日最后一档只有8笔，5胜3负，投入 `$15.82`、赚 `$9.18`；全部3个错误都在
  每日最后一档。策略真正的盈亏来源是最后一档，而不是 headline 的25/28胜率。
- 按日汇总为6个盈利日、2个亏损日；最差为7/26 `-$1.50`，其次7/29
  `-$0.65`。target-date block bootstrap 已处理同日相关性，但 forward 还需记录
  最大同时投入和跨档叠仓。

## Freeze 前剩余研究

1. **不再调天气模型。** 继续使用当前 artifact，7/31+只评分。
2. **补 post-source executable 分母。** 至少达到原计划30 settled dates、
   80 date-X opportunities、50 fresh executable first-positive signals。
3. **补 E1/E2 同刻 expression parity。** current-X NO 与 upper-YES strip
   必须同时有5/10-share fresh ladder；当前为0/28，不合格。
4. **验证 portfolio state。** 报 date-X 与 city-day 两层，记录逐档叠仓、
   最大现金占用、最后一档和全日净收益；不把28笔当28个独立样本。
5. **记录 residual correction 诊断。** 保存 market、weather-only、offset三者及
   correction magnitude、sign flip；这是监控字段，不是新的 eligibility gate。

## 资格

```text
significance=PASS for exploratory replay ROI
baseline=FAIL_CI for probability vs same-row market
forward=NA
execution=FAIL_COVERAGE
conclusion=inconclusive / probability artifact frozen / expression strategy not frozen
```

Signal funnel：2,115 weather checkpoints → 362 OOF market checkpoints/9 dates
→ 28 first-positive date-X intents/8 dates。

Evidence funnel：28 intents → 11 post-source active quotes/4 dates → 28 settlements
→ 0 upper-strip parity rows → 0 actual fills。
