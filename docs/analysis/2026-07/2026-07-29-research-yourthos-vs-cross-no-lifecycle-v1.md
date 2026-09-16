# yourthos vs Seoul cross-NO：四次完整生命周期复盘

Status: `research / zero-notional shadow / no live change`

## 目标与 grain

目标不是从四个案例直接拟合新 gate，而是回答：

> 在同一批 Seoul/RKSI PIT observations 与盘口上，yourthos 相对现有
> `2×(running max+0.5°C) 且 latest>=+0.7°C` persistent cross-NO benchmark
> 提前或延后多久、交易了什么、如何退出、最终结果如何。

grain 固定为 `Seoul target_date × complete mutually-exclusive ladder`。钱包同日
多档 BUY/SELL/NegRisk conversion 合并解释，不把单 token 当独立策略。

## 数据与边界

- wallet：`0x4f1164d1531b8fb77919df285c576628318553b0`
- public fills / settlement：JRS immutable wallet snapshot
  `20260729T145856Z`
- AMOS：JRS `high_frequency_observations` all-runway first-seen
- routine：JRS `source_events` AviationWeather METAR first-seen；使用 detect time，
  不用 report time 倒填
- benchmark trigger 盘口：`fast_source_stale_book/quote_snapshots.jsonl` 的 fresh
  bid/ask/depth
- wallet fill 时完整盘口：full-ladder archive；部分相距成交 15–34 分钟，只作
  market-state 背景，实际成交价以 public fills 为准
- 无法观察：钱包原始 order-post/cancel、未成交单、maker/taker flag、私有模型与
  人工审批

## 总览

| date | wallet 相对 persistent trigger | benchmark fresh NO ask | routine cross | winner | wallet public cashflow |
|---|---:|---:|---|---:|---:|
| 7/15 | trigger 后 8.6m | 26 NO `0.955 × 82.42` | wallet 首买后 42.0m 到 27 | 28 | +$629.31 |
| 7/16 | trigger 前 101.4m | 28 NO `0.64 × 163.3` | 没有 | 28 | -$1,150.73 |
| 7/21 | 无 cross expression | NA | 没有 | 27 | +$11.98 |
| 7/24 | trigger 前 82.8m | 30 NO `0.89 × 35.3` | wallet 首买后 123.8m 到 31 | 31 | +$306.61 |

## Case 1：7/15，接近我们的 cross-NO，但会连续滚动 ladder

### 监控与 trigger

- 12:46:22：routine running max 26°C；AMOS 已有两个 distinct observations
  `>=26.5°C`，latest 26.8°C，persistent benchmark 成立。
- 12:47:17 fresh 26 NO 为 `bid 0.929 / ask 0.955`，ask depth 82.42。
- 13:36:57：routine first-seen 正式把 running max 更新到 27°C。

### 钱包操作

1. 12:54–13:09：trigger 后 8.6 分钟开始买 26 NO，共
   `2,331.50 shares / $2,185.03`，VWAP `0.937`。
2. 13:26–13:40：在我们看到 routine 27 前，继续买 339.08 shares 26 NO，
   随后把总计约 2,670.57 shares 以 `0.999` 卖出；同时转买
   `27 NO @0.69`，并配置少量 28 NO、29 YES。
3. 14:02 后 routine 已到 27：继续买 27 NO，VWAP 约 `0.67–0.75`；
   同时买少量 28/29 YES。
4. 当日后续 running max 到 28；17:05 左右通过 NegRisk-equivalent inventory
   卖出 `1,614.91 shares 28 YES @0.999`。

### 结果与 benchmark

- BUY cost `$3,651.92`，SELL `$4,281.22`，winner 28°C；
  public cashflow `+$629.31`，turnover ROI `+17.23%`。
- 若 benchmark 只在首次 trigger 以 fresh ask `0.955` 买 10 shares 26 NO，
  gross pre-fee profit 约 `$0.45`。

钱包的优势不只是早几分钟，而是每次新高后把 `26 NO → 27 NO → converted
28 YES` 连续滚动，把同一天多个 source events 都利用起来。

## Case 2：7/16，提前预测与 hard cross 同时失败

### 钱包提前建仓

- 12:03：routine/running max 28°C，最高点已约 93 分钟没更新；最新 all-runway
  AMOS 只有 27.0°C，没有 half-cross，更没有 persistence。
- 12:03–12:16：先买 399.99 shares 28 NO，VWAP `0.570`。
- 12:30：routine 仍为 28、AMOS 仍 27.0；再买 1,170.62 shares，
  VWAP `0.671`。
- 13:38 起：routine 仍为 28；继续买 28 NO，并配置 29 NO/YES、30 YES。

### 我们的 trigger 后到，但也是 false cross

- 13:44:29：AMOS 到 28.9°C，persistent benchmark 才成立；钱包已经提前
  101.4 分钟入场。
- 13:44:38 fresh 28 NO 为 `bid 0.57 / ask 0.64`，ask depth 163.3。
- 之后 routine 从未打印 29°C，final/winner 仍为 28°C。
- 14:29–14:41：钱包仍加买 408.18 shares 28 NO，VWAP仅 `0.206`，没有有效
  thesis invalidation。

### 结果与 benchmark

- BUY `$1,223.49`，SELL仅 `$72.76`，public cashflow `-$1,150.73`，
  ROI `-94.05%`。
- benchmark 若在 `0.64` 买 10 shares，同样会 gross pre-fee 亏 `$6.40`。

这个案例说明两件事：提前概率模型并不会自动修复 terminal false cross；而错误
cross 往往给出更便宜、更深的盘口，正是 adverse execution selection 最强的时刻。
钱包真正的问题是失败状态下仍连续加仓。

## Case 3：7/24，提前 83 分钟预测成功

### 预测阶段

- 12:32：routine/running max 30°C，running max age 122 分钟；AMOS 30.1°C，
  没有 half-cross/persistence。
- 12:32–13:00：买 575.01 shares 30 NO，VWAP `0.814`；同时小仓买
  30 YES 与 `31 YES @0.60`，不是裸单腿。
- 13:12：AMOS 30.3，仍未 cross；再买 383.62 shares 30 NO，
  VWAP `0.839`。
- 13:28–13:39：AMOS 回到 29.7；仍补 238.74 shares 30 NO，
  VWAP `0.624`，并配置少量 32+ YES。

### confirmation 与结算

- 13:55:17：AMOS 31.0，persistent benchmark 成立；钱包首买比它早82.8分钟。
- 13:55:24 fresh 30 NO 为 `bid 0.87 / ask 0.89`，ask depth 35.3。
- 13:55–13:59：钱包再买63 shares 30 NO，VWAP `0.761`，同时清掉
  30 YES 与32+ YES。
- 14:36:14：routine first-seen 正式到31°C，比钱包首买晚123.8分钟。
- winner 31°C；主要持有 winner inventory 到 redemption。

### 结果与 benchmark

- BUY `$1,065.44`，SELL `$11.69`，redeem `$1,360.37`；
  public cashflow `+$306.61`，ROI `+28.78%`。
- benchmark 在确认时 `0.89` 买10 shares，gross pre-fee profit仅约 `$1.10`。

这一天能明确看到 prediction value：钱包第一批30 NO VWAP `0.814`，比 benchmark
确认时ask便宜7.6¢；但它靠的是连续重估与组合仓位，不是简单把x.7阈值提前。

## Case 4：7/21，不属于 cross-NO 的 terminal exact

- 12:43：routine 已回落到25°C，running max仍为27°C；最高点已约764分钟，
  AMOS 24.7°C，没有 cross。
- 买38.2 shares `27 YES @0.85`；13:50再买41.67 shares同价。
- 没有主动卖出，winner 27°C，redeem `$79.87`；
  BUY `$67.89`，public cashflow `+$11.98`，ROI `+17.65%`。

我们的 cross-NO 在这一天应保持 no-trade。钱包使用的是另一套 posterior：
`P(final exact=27 | old max + 2°C pullback + remaining heat exhausted)`。

## 对现有 cross-NO 的结论

不能把改进简化成“cross前 N 分钟提前买”。四个案例支持的是状态机扩展：

```text
每条新 observation
  -> 更新 p_cross_30 / p_cross_60 / p_final_leave / p_final_exact
  -> 与同刻完整 ladder ask+depth+fee 比较
  -> probe / add / reduce / convert / terminal-hold / no-trade
```

具体动作：

1. 保留现有 persistent cross-NO 作为 benchmark，不把 threshold 再往前挪。
2. 新增 probability head：在每条 AMOS snapshot 都输出连续概率；wallet只作
   confirmation/veto feature。
3. 必须有 `state-aware invalidation`：source回落、running max变老、
   `p_final_leave < executable cost` 时停止加仓。7/16 是必测 negative control。
4. entry 与 exit 联合回放：正确 cross 后允许 NegRisk conversion/滚到下一档，
   而不是所有 NO 无条件持结算。
5. 继续 zero-notional shadow；至少15个新Seoul target dates后，在相同PIT
   observations和quotes上比较 probability head、wallet overlay 与hard trigger。

## 产物

- lifecycle JSON：
  `generated/yourthos_rksi_replay_v1/yourthos_vs_cross_no_lifecycles_v1.json`
- wallet phase + full-ladder book：
  `generated/yourthos_rksi_replay_v1/case_timelines_with_book_v2.json`
- 可复跑脚本：
  `scripts/analysis/wallet_weather/research_yourthos_vs_cross_no_lifecycle_v1.py`
- JRS immutable analysis：
  `/Volumes/jrs/pm_agents/research/external_wallet_weather/raw/wallet=0x4f1164d1531b8fb77919df285c576628318553b0/snapshot=20260729T145856Z/analysis/yourthos_vs_cross_no_lifecycle_v1/`
