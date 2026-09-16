# KNMI cross-NO threshold replay v1

## 决策摘要

- 当前只保留 `ta +0.5°C / 1 confirmation` 为 zero-notional entry shadow；`ta +0.5°C / 2 confirmations` 只作 persistence 标签，不等待它才入场。
- `tx` 不作入场 trigger：四个阈值的一次确认 fee-adjusted ROI 分别为 `-2.70% / -28.56% / -20.95% / NA`，短峰 terminal false cross 会造成真实亏损。
- `ta +0.5 / 1`：train `2/2`、ROI `+8.83%`；holdout `2/2`、ROI `+4.48%`；合计 `4/4`、ROI `+6.61%`。但只有 4 个独立交易日，不能升 live。
- 上述 `4/4` 是固定要求 top ask depth `>=10 shares` 的容量切片。若真实执行改为“目标 10 shares，价格合格时买 `min(10, top ask size)`”，则为 `6` 笔、`5` 胜、总成本 `$45.7696`、fee-adjusted PnL `+$2.2304`、组合 ROI `+4.87%`。
- `ta +0.5 / 2`：train `3/3`、ROI `+24.90%`；holdout `0` 笔可执行。它把 signal win rate 从 `92.59%` 提至 `100%`，但 11 个同事件配对中有 10 个 NO ask 变贵，中位 `+1.0c`，并使 3 个原可执行机会失去资格。
- 因而策略动作是“第一次 `ta>=METAR running max+0.5°C` 且已离开实际 market bracket 时立刻记录/报价；第二次确认只更新置信标签”，不是固定等待 20 分钟。

## 扩展信号校准（无 PIT 盘口）

另加入 `2026-07-22..26` 五个完整 KNMI/WU weather days；该段无同频 archived book，只用于 source→WU outcome 校准，不进入 ROI。与上面 10 天合并后：

| field | margin | confirms | signals | signal wins | signal win rate |
|---|---:|---:|---:|---:|---:|
| ta | 0.5 | 1 | 50 | 48 | 96.00% |
| ta | 0.5 | 2 | 30 | 30 | 100.00% |
| ta | 0.6 | 1 | 42 | 41 | 97.62% |
| ta | 0.6 | 2 | 21 | 21 | 100.00% |
| ta | 0.7 | 1 | 34 | 34 | 100.00% |
| ta | 0.7 | 2 | 14 | 14 | 100.00% |
| ta | 0.8 | 1 | 31 | 31 | 100.00% |
| ta | 0.8 | 2 | 9 | 9 | 100.00% |
| tx | 0.5 | 1 | 52 | 48 | 92.31% |
| tx | 0.5 | 2 | 38 | 36 | 94.74% |
| tx | 0.6 | 1 | 50 | 47 | 94.00% |
| tx | 0.6 | 2 | 31 | 29 | 93.55% |
| tx | 0.7 | 1 | 44 | 42 | 95.45% |
| tx | 0.7 | 2 | 25 | 25 | 100.00% |
| tx | 0.8 | 1 | 37 | 35 | 94.59% |
| tx | 0.8 | 2 | 20 | 20 | 100.00% |

注意：这是 15 个 weather days、其中 13 个 signal-active dates；不能把 50 个同日多 bracket signals 当成 50 个独立日。

## 数据快照

- window：`2026-06-18..2026-06-27`；KNMI rows `947`；IEM archived EHAM METAR `434`；book rows `29134`；settled days `10`。
- grain：每个 `target_date × previous market bracket × source field × threshold × confirmation policy` 的首个 signal；交易表达为 previous-bracket NO。
- PIT：decision clock 使用 KNMI file `created`，盘口使用其后 `300s` 内首个 archived NO ask；`ask<=0.97` 且 depth `>=10 shares`；Weather taker fee `0.05*p*(1-p)`。
- 重要限制：KNMI 文件为事后下载的最终 revision，`created` 只重建可用时钟，无法证明 initial payload 与 final revision 完全相同；因此是 archive-reconstructed research replay，不是 clean forward PIT。
- missing_bracket：`0`；unsettled：同值。

### 时间覆盖勘误

这里的 executable replay 不是 6 月 18 日至 7 月 28 日连续一个月，而只是 KNMI 与 dense PIT book 真正重叠的 `2026-06-18..27` 十个完整 target dates：

- legacy `source_orderbook_timing/books.jsonl` 在这 10 天对 Amsterdam 约每 `42–52 秒`一轮；保存 best ask/bid、top size 和 levels count，因此能知道 KNMI 文件发布后几秒到几分钟的 top-of-book 价格与数量，但没有保存完整逐档 ladder。
- `2026-06-28` 只覆盖到 `03:28 UTC`，`2026-06-29` 只有一个 timestamp；之后没有这套 dense timing book。
- `2026-07-22..26` 有完整 KNMI/METAR/WU outcome，可用于 signal basis 校准，但没有同频 archived book。该 5 日实际产生 `ta .5=23`、`.6=23`、`.7=18`、`.8=17` 个 first-bracket signals；不能判断其当时 ask、depth 或 executable ROI。

因此“`.6` 的 3 笔都在 6 月 18–19 日”只描述十日 PIT-book 窗口中的可执行样本，不表示此后一个月没有天气信号。当前 `weather_full_ladder_capture` 会话虽存在，但 7 月 28 日巡检显示它持续因 symlink runtime 写入 `Operation not permitted` 失败；当前 forward ladder 证据链需先修复，不能用进程存在冒充健康采集。

## 可执行漏斗审计

`ta +0.5 / 1` 在 10 天中不是只触发 4 次：原始 KNMI 路径共有 `61` 条满足 cross 条件的 observation，按“同一日期、同一 previous bracket 只取第一次”去重后是 `27` 个机会、覆盖 `8` 个 signal dates。证据漏斗为：

`61 raw observations → 27 first-bracket signals → 27 book observed → 19 NO ask available → 4 executable`

- `8/27`：盘口已观测到，但 NO 侧没有 ask；不是采集缺失。
- `13/27`：NO ask 高于 `0.97`，其中 1 个同时深度不足；价格已基本反映信息。
- `2/27`：价格不高于 `0.97`，但 top ask depth 小于 `10 shares`。
- `4/27`：同时满足价格和深度条件。

此前脚本把 `best_ask=null` 的正常盘口行过滤掉，因而把 8 个“book observed、NO ask empty”事件误记成 quote coverage gap。本次已修正：受影响范围是 8 个事件的缺失原因分类；signals、4 笔 executable trades、wins 和 ROI 均未改变。

### 2026-06-21 bracket 26 false cross 回放

这是 partial-sizing 新增的唯一失败事件：

- `14:20 UTC`（Amsterdam `16:20`）：KNMI `ta=26.5°C`、`tx=26.6°C`；此前 EHAM METAR running max 为 `26°C`。按 half-up 映射，ta 从 26 档跨到 27 档，因此产生“买 26 NO”的 signal。
- `14:23:42 UTC`：KNMI 文件生成；`14:23:49` 盘口为 26 NO `ask=0.84`、top ask size `6.52`。按目标 10、余量全吃的执行法，会买 `6.52 shares`，含 fee 成本 `$5.5206`。
- `14:30 UTC`：下一档 KNMI `ta` 已回落到 `25.8°C`；虽然 `tx=26.8°C` 说明此前 10 分钟内确有很短的高温峰，但峰值没有形成 settlement-facing 的 27°C 报文。
- EHAM METAR 在 `12:25..16:25 UTC` 的可见报文最高一直是 `26°C`，`16:55 UTC` 降至 `25°C`；WU/market 最终 winning bracket 为 `26`。因此 26 NO 全亏，fee-adjusted PnL `-$5.5206`。

这不是“温度后来升到更高导致 previous-bracket NO 错”，而是 `KNMI decimal cross → METAR/WU integer bracket` 的 terminal false cross：短峰后回落，且不同 source/rounding basis 没有确认跨档。

### 单次确认阈值 × partial sizing

执行统一为 `shares=min(10, top ask size)`、`ask<=0.97`：

| ta threshold | signals / dates | ask available | ask<=.97 | partial trades / dates | wins | cost | fee-adjusted PnL | ROI | date-block 95% CI |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 0.5 | 27 / 8 | 19 | 6 | 6 / 5 | 5 | $45.7696 | +$2.2304 | +4.87% | [-43.53%, +35.15%] |
| 0.6 | 19 / 7 | 12 | 3 | 3 / 2 | 3 | $21.3934 | +$6.6066 | +30.88% | [+6.06%, +50.44%] |
| 0.7 | 16 / 6 | 10 | 1 | 1 / 1 | 1 | $9.4282 | +$0.5718 | +6.06% | NA |
| 0.8 | 14 / 6 | 8 | 0 | 0 / 0 | 0 | $0 | $0 | NA | NA |

`.7` 确实剔除了 6 月 21 日的 `.5` terminal false cross，但它不是当前更好的入场门槛：10 个有 ask 的事件中只有 1 个仍低于 `0.97`，median ask 已到 `0.997`，可执行空间基本消失。`.6` 是更合理的 frozen challenger，但其 3 笔可执行交易全部集中在 train 的 6 月 18–19 日，holdout 为 0 笔；点估和 nominal bootstrap CI 不能越过两天样本、四阈值事后比较和 forward 缺失。当前仍是 `inconclusive`，只允许把 `.5` 与 `.6` 并行 zero-notional shadow，`.7` 仅作高置信标签。

## 结果

| field | margin | confirms | signals | signal win rate | book observed | ask available | settled trades | dates | wins | trade win rate | mean ask | fee-adjusted ROI | date-block 95% CI |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| ta | 0.5 | 1 | 27 | 0.925926 | 27 | 19 | 4 | 4 | 4 | 1 | 0.935 | 0.066084 | [0.042193, 0.088275] |
| ta | 0.5 | 2 | 16 | 1 | 16 | 11 | 3 | 2 | 3 | 1 | 0.795 | 0.248964 | [0.047389, 1.030539] |
| ta | 0.6 | 1 | 19 | 0.947368 | 19 | 12 | 2 | 2 | 2 | 1 | 0.93 | 0.071524 | [0.060648, 0.082626] |
| ta | 0.6 | 2 | 10 | 1 | 10 | 5 | 1 | 1 | 1 | 1 | 0.965 | 0.034458 | [, ] |
| ta | 0.7 | 1 | 16 | 1 | 16 | 10 | 1 | 1 | 1 | 1 | 0.94 | 0.060648 | [, ] |
| ta | 0.7 | 2 | 5 | 1 | 5 | 2 | 0 | 0 | 0 |  |  |  | [, ] |
| ta | 0.8 | 1 | 14 | 1 | 14 | 8 | 0 | 0 | 0 |  |  |  | [, ] |
| ta | 0.8 | 2 | 3 | 1 | 3 | 1 | 0 | 0 | 0 |  |  |  | [, ] |
| tx | 0.5 | 1 | 28 | 0.892857 | 28 | 20 | 7 | 5 | 5 | 0.714286 | 0.728571 | -0.026974 | [-0.562354, 0.363185] |
| tx | 0.5 | 2 | 20 | 0.9 | 20 | 15 | 5 | 3 | 4 | 0.8 | 0.8624 | -0.077825 | [-1.0, 0.294021] |
| tx | 0.6 | 1 | 26 | 0.923077 | 26 | 18 | 3 | 3 | 2 | 0.666667 | 0.93 | -0.285635 | [-1.0, 0.093984] |
| tx | 0.6 | 2 | 17 | 0.882353 | 17 | 12 | 5 | 3 | 4 | 0.8 | 0.8484 | -0.062263 | [-1.0, 0.384217] |
| tx | 0.7 | 1 | 22 | 0.954545 | 22 | 16 | 4 | 3 | 3 | 0.75 | 0.94625 | -0.209503 | [-1.0, 0.063389] |
| tx | 0.7 | 2 | 13 | 1 | 13 | 8 | 1 | 1 | 1 | 1 | 0.94 | 0.060648 | [, ] |
| tx | 0.8 | 1 | 18 | 0.944444 | 18 | 11 | 0 | 0 | 0 |  |  |  | [, ] |
| tx | 0.8 | 2 | 10 | 1 | 10 | 5 | 1 | 1 | 1 | 1 | 0.959 | 0.040615 | [, ] |

## 口径

- `ta`：区间末最后 1 分钟平均气温；更接近下一份 routine METAR 的瞬时状态。
- `tx`：过去 10 分钟内 1 分钟平均气温的最大值；能抓短峰，但更容易出现已回落的 terminal false cross。
- `confirmations=2` 要求两个连续 10 分钟 KNMI observation 都在同一 METAR running-max state 上超过阈值。
- signal funnel 与 evidence funnel 分开；缺盘口只算 coverage gap，不算策略筛除。
- train=`2026-06-18..22`，holdout=`2026-06-23..27`；holdout 前已冻结 primary=`ta/+0.5/2`、challenger=`ta/+0.5/1`，见 train report。

## 结论边界

本表同时测试 16 个版本，未做多重检验校正；历史 KNMI 是 final revision 重建，且可执行交易只有 4–7 笔。需要用 2026-07-28 起 clean first-seen collector 做至少 15 个独立 signal dates 的 frozen forward，并与同刻 market baseline 比 probability score。`significance=FAIL baseline=NA forward=FAIL conclusion=inconclusive_keep_zero_notional_shadow`。
