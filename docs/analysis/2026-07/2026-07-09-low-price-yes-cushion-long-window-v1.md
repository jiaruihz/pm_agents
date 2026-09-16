# Low-Price YES Cushion Long-Window Sensitivity

Status: snapshot / consolidated
Date: 2026-07-09
Strategy family: `forecast_tail_low_price_yes` / HeadA

This report consolidates the original zero-delay replay and the separate
30-minute-delay rerun. The latter no longer has a parallel dated report.

## 结论

两种 execution-lag 口径给出同一个耐久结论：`0.05` 的优势主要来自
2026-07-01..07 已知 winner 窗口，不能当作稳健最优阈值。阈值选择前的
2026-05-29..06-30 中，零延迟口径偏向 1c，30 分钟延迟口径偏向 3c；5c
在两种口径都不是最优。

因此 3c 只能作为较少后验污染的执行折中，不是 alpha gate，也不支持
size-up。两次 replay 都是历史 orderbook 的近似，不是 live runner 的精确
fresh-book 时钟。

## 固定口径

- target dates: `2026-05-29..2026-07-07`
- orderbook: historical `orderbook_snapshots`
- candidate denominator: 383 HeadA base rows；`ask 5-20c`、`edge>=20c`、每
  city-date 第一行，再应用 `dist>0` hot-tail 边界
- fee: official Weather taker fee `0.05 * price * (1-price)`
- zero-delay: decision 后第一张 YES book，最多等待 60 分钟；matched 355，
  hot-tail matched 244
- delay-30m: decision+30m 后第一张 YES book，最多等待 45 分钟；matched 350，
  hot-tail matched 240

## Pre-Choice Window：2026-05-29..06-30

| delay | cushion | rows | wins | win rate | avg fresh ask | ROI | losing days |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 0m | 0.01 | 120 | 15 | 12.5% | 0.099 | +21.5% | 18 |
| 0m | 0.02 | 180 | 19 | 10.6% | 0.103 | -2.2% | 17 |
| 0m | 0.03 | 198 | 22 | 11.1% | 0.106 | +0.4% | 16 |
| 0m | 0.05 | 208 | 23 | 11.1% | 0.109 | -3.0% | 15 |
| 0m | 0.08 | 213 | 24 | 11.3% | 0.110 | -1.8% | 15 |
| 30m | 0.01 | 115 | 13 | 11.3% | 0.093 | +16.4% | 19 |
| 30m | 0.02 | 154 | 17 | 11.0% | 0.099 | +6.9% | 18 |
| 30m | 0.03 | 173 | 23 | 13.3% | 0.102 | +25.3% | 16 |
| 30m | 0.05 | 195 | 24 | 12.3% | 0.106 | +11.2% | 16 |
| 30m | 0.08 | 203 | 24 | 11.8% | 0.108 | +5.2% | 16 |

## Full Window：2026-05-29..07-07

| delay | cushion | rows | wins | win rate | avg fresh ask | ROI | losing days |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 0m | 0.01 | 128 | 17 | 13.3% | 0.098 | +29.4% | 20 |
| 0m | 0.02 | 196 | 24 | 12.2% | 0.104 | +12.7% | 18 |
| 0m | 0.03 | 216 | 27 | 12.5% | 0.106 | +12.6% | 17 |
| 0m | 0.05 | 226 | 28 | 12.4% | 0.109 | +8.7% | 16 |
| 0m | 0.08 | 231 | 29 | 12.6% | 0.110 | +9.4% | 16 |
| 30m | 0.01 | 122 | 16 | 13.1% | 0.094 | +33.3% | 21 |
| 30m | 0.02 | 169 | 22 | 13.0% | 0.100 | +24.5% | 19 |
| 30m | 0.03 | 189 | 28 | 14.8% | 0.102 | +38.5% | 17 |
| 30m | 0.05 | 211 | 29 | 13.7% | 0.106 | +23.7% | 17 |
| 30m | 0.08 | 219 | 29 | 13.2% | 0.108 | +17.6% | 17 |

## Known-Winner Window 与动作

7/01..07 中，3c/5c 在零延迟口径都是 18 rows、ROI +143.4%；30 分钟口径
都是 16 rows、ROI +169.3%。这正是 full-window 数字被后验窗口抬高的证据，
不是 5c 的独立确认。

```text
0.05: execution_probe_not_confirmed
0.03: cleaner_pre_choice_execution_compromise
live_action: no selector change; no size-up; require fresh forward evidence
```
