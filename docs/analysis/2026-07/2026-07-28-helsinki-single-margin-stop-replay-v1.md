# Helsinki 单次 +0.7 CrossNO：source invalidation stop replay v1

Status: `research / low-sample / no live change`

## 结论

今天 20 NO 的下一份 FMI 回落发生时，退出不是割肉，而是明显的盈利止盈：
18:12:08 北京时间附近，公开成交显示 20 NO ask/trade 已到 `0.90`；18:11:38–40 的
YES `0.14` 成交在二元互补口径下对应 NO 可卖侧约 `0.86`。若 15 shares 能在
`0.85–0.86` 退出，按 Weather 双边 taker fee 计算，round-trip PnL 约
`+$5.72..+$5.87`，而持有至当前错误结果约为 `-$6.94`。

但不能据此增加“下一份 FMI 低于 +0.7 就全平”的 live stop。固定历史分母中，这种
单次回落经常只是 path noise：`2026-07-15..24` 的 23 个首个单次 +0.7 信号里，
6 个下一份 FMI 低于 +0.7；这 6 个最终全部离开 old exact bracket，即 NO 全部获胜。
同时 6 个都没有满足原始 `ask<=0.97 + top depth>=10` 的直接可执行 entry，因此历史上
没有可执行 stop PnL 可以支持自动全平。

推荐研究机制不是普通 stop-loss，而是：

```text
source invalidation (< old bracket +0.5, strict)
+ routine/WU 尚未确认 cross
+ fresh executable SELL book
+ fee-adjusted round-trip PnL >= 0
=> profit-protecting exit candidate
```

它应先进入 zero-notional shadow；当前不改 live。

## 今日 20 NO 逐秒证据

- 18:01:54：FMI `21.1°C` first-seen，20 NO book `0.40 / 0.45`。
- 18:01:55：15 shares taker 成交于 `0.45`，principal `$6.75`。
- 18:10:53：YES `0.11` 有公开成交，说明市场已开始回补 exact-20。
- 18:11:38–40：YES `0.14` 合计成交 `12.97` shares；二元互补口径对应 NO
  可卖侧约 `0.86`，但成交已消耗过该档，不能冒充 18:12:19 仍有完整 15-share depth。
- 18:12:08：NO `0.90 × 30` 有公开 BUY 成交，证明当时 NO ask/trade 已到 `0.90`。
- 18:12:19：FMI `20.1°C` first-seen，已严格跌破 `20.5°C` hysteresis。
- 18:27:09：定时全梯度快照显示 NO `0.28 / 0.32`；此时 routine METAR 已再次打印
  20°C，止损窗口基本消失。

18:12 的定时 snapshot 文件没有 Helsinki 行，continuous active-bracket archive 又只写到
7 月 27 日。因此 `0.85–0.86` 是由相邻真实成交恢复的 SELL proxy，不是该秒完整 direct
bid ladder；15 shares 全退仍缺精确 depth 证据。

## Fee-adjusted 反事实

入场 fee：

```text
15 × 0.05 × 0.45 × (1 - 0.45) = $0.185625
```

| 15-share exit | exit fee | round-trip PnL |
|---:|---:|---:|
| 0.85 | $0.095625 | +$5.718750 |
| 0.86 | $0.090300 | +$5.874075 |
| 0.90 | $0.067500 | +$6.496875 |
| 不退出且 NO 输 | — | -$6.935625 |

`0.90` 是当时真实 NO 成交/ask 证据，不是已证明能卖出 15 shares 的 bid，因此主结论使用
`0.85–0.86` proxy，不使用最乐观的 `0.90`。

## 同分母历史 replay

输入沿用既有单次 margin ledger 与 all-observation PIT atlas。entry grain 固定为
`target_date × previous exact bracket` 的首个单次 `>=+0.7°C` 信号；下一份 FMI
只作之后可见的 invalidation event，不回填 entry。

| rule | first +0.7 entries | next-FMI invalidations | invalidated rows 最终 NO win | direct executable entries |
|---|---:|---:|---:|---:|
| next FMI `< old+0.7` | 23 / 7 dates | 6 | 6/6 | 0/6 |
| next FMI `< old+0.5` strict | 23 / 7 dates | 1 | 1/1 | 0/1 |

更宽的既有 `2026-07-09..25` 报告中，单次 +0.7 最终标签为 `35/35`，但 next-METAR
只有 `30/35`；这同样说明“下一份官方报文没立即确认”不等于 final NO thesis 失败。

今天的两个 live fills 刚好展示 hysteresis 的用途：

- 19 NO：FMI `19.8 -> 19.5`。低于 entry +0.7，但没有严格跌破 `19.5`；
  naive stop 会误伤最终正确的 NO。
- 20 NO：FMI `21.1 -> 20.1`。严格跌破 `20.5`，且市场给出远高于 entry 的退出价；
  profit-protecting invalidation exit 能有效回收。

这只是 1 个 forward 日、2 个 live fills，不能把 `<+0.5` 直接定成 live alpha gate。

## 漏斗与完整性

Signal funnel：

- existing single +0.7 PIT entries：23 / 7 dates；
- next FMI `<+0.7`：6；
- next FMI `<+0.5` strict：1。

Evidence funnel：

- settlement label：23；
- historical invalidated rows with executable entry：0；
- historical same-second executable stop ladder：0；
- 2026-07-28 live invalidation with public trade proxy：1；
- 2026-07-28 exact 15-share direct SELL depth：0。

覆盖了 path state、entry signal、settlement 与今天的 public trade microstructure；缺少历史
stop bid/depth、实际 SELL fill、独立 forward dates 和同分母可执行 PnL。

```text
significance=FAIL（可执行 stop 样本不足）
baseline=FAIL（历史 stop ladder/depth 缺失）
forward=FAIL（仅 1 个 forward 日）
conclusion=inconclusive
action=zero-notional shadow；不新增 naive live stop
```

## 产物

- `scripts/analysis/market_structure_edge/research_helsinki_single_margin_stop_replay_v1.py`
- `generated/helsinki_single_margin_stop_replay_v1/stop_signal_replay.csv`
- `generated/helsinki_single_margin_stop_replay_v1/summary.json`
