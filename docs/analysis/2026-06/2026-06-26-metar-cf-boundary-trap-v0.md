# METAR C->F Boundary Trap V0

Status: snapshot
Updated: 2026-06-26
Evidence: N100 raw timing logs, run from `/home/jiarui/projects/pm_agent` with local script `scripts/analysis/market_structure_edge/research_metar_cf_boundary_trap_v0.py`

## Conclusion

这个坑是真实存在的，但目前样本还很小。

在当前 N100 raw timing logs 里，F 市场 crossing 事件有 `81` 个，其中 `59` 个能匹配到 raw METAR。里面有 `22` 个存在 main integer-C 与 RMK tenth-C 口径差异，进一步收窄到“会影响 whole-F bracket 是否被打穿”的 boundary mismatch 事件有 `5` 个。

这 5 个里，只有 SanFrancisco 2026-06-25 `68-69°F` 出现了我们关心的完整形态：

1. METAR 正文 `21C` 会被简单换算成 `70F`；
2. 同一条 METAR 的 RMK `T0206` 表示 `20.6C`，换算后是 `69.1F`，整华氏仍是 `69F`；
3. 盘口一开始没有完全相信 YES，也没有完全相信 NO；
4. 之后盘口明显向 `68-69 YES` 回摆。

结论等级：

```text
significance=NA
baseline=NA
forward=NA
conclusion=research_only
```

这不能直接 live。它说明的是：F/WU 市场里，METAR 整数 C 可能制造短时间 source-basis 误导；未来应该监控“市场是否过度相信 main C crossing”，而不是继续用 main C crossing 去买 crossed NO。

## Data Snapshot

| Metric | Count |
|---|---:|
| F crossing events | 81 |
| Events with raw METAR | 59 |
| Source-basis risk events | 22 |
| Boundary mismatch events | 5 |
| Boundary mismatch with YES reversion | 1 |
| Boundary mismatch live submitted | 2 |

## City Summary

| city | boundary events | reverted | live submitted | median source lag sec | median correction sec |
|---|---:|---:|---:|---:|---:|
| Austin | 2 | 0 | 0 | 163.572 | NA |
| Dallas | 1 | 0 | 0 | 193.478 | NA |
| Houston | 1 | 0 | 1 | 162.566 | NA |
| SanFrancisco | 1 | 1 | 1 | 220.775 | 3575.555 |

## Boundary Events

| city | date | market | report | detect | main C/F | RMK C/F | detect YES/NO | 60m YES mid | correction | live |
|---|---|---|---|---|---:|---:|---:|---:|---|---|
| Austin | 2026-06-25 | 87°F or below | 2026-06-25T16:53:00Z | 2026-06-25T16:55:42Z | 31.0C/88F | 30.6C/87F | Y 0.005 / N ask 0.998 | 0.001 | NA |  |
| Houston | 2026-06-25 | 88-89°F | 2026-06-25T16:53:00Z | 2026-06-25T16:55:42Z | 32.0C/90F | 31.7C/89F | Y 0.166 / N ask 0.973 | 0.002 | NA | submitted |
| Dallas | 2026-06-25 | 88-89°F | 2026-06-25T16:53:00Z | 2026-06-25T16:56:13Z | 32.0C/90F | 31.7C/89F | Y 0.007 / N ask 0.996 | 0.009 | NA |  |
| Austin | 2026-06-25 | 88-89°F | 2026-06-25T17:53:00Z | 2026-06-25T17:55:44Z | 32.0C/90F | 31.7C/89F | Y 0.018 / N ask 0.997 | 0.001 | NA |  |
| SanFrancisco | 2026-06-25 | 68-69°F | 2026-06-25T19:56:00Z | 2026-06-25T19:59:40Z | 21.0C/70F | 20.6C/69F | Y 0.395 / N ask 0.700 | 0.900 | 2026-06-25T20:59:16Z | submitted |

## What Happened In SFO

SFO 这笔不是简单的“市场一直知道 YES 会赢”。盘口时间线更像先被 METAR main-C 影响，再被更精细/结算口径纠正：

| Time UTC | 68-69 YES | 68-69 NO | Reading |
|---|---:|---:|---|
| 19:55:41 | ask 0.39 / bid 0.25 | ask 0.74 / bid 0.61 | report 前市场偏 NO，但不是确定。 |
| 19:56:00 | - | - | KSFO METAR report，正文 `21/13`，RMK `T02060128`。 |
| 19:59:40 | - | - | bot 看到 report，把 21C 算成 70F。 |
| 19:59:47 | mid 约 0.395 | ask 0.69 | bot 买 NO；市场仍给 YES 很多概率。 |
| 20:13:40 | bid 0.59 / ask 0.62 | ask 0.41 | 明显向 YES 回摆。 |
| 20:59:16 | bid 0.82 / ask 0.87 | ask 0.18 | 基本确认 YES。 |
| 21:39:00 | bid 0.95 / ask 0.96 | ask 0.05 | 接近收敛。 |

关键是同一条 METAR 本身已经带了更细的温度：

```text
KSFO 251956Z ... 21/13 ... RMK ... T02060128
main temp: 21C -> 69.8F -> round 70F
RMK temp: 20.6C -> 69.1F -> round 69F
```

如果一个 bot 只解析 `21/13`，会以为 `68-69°F` 已经被打穿。
如果另一个 bot 解析 RMK `T0206`，它会知道 `68-69°F` 还没死。

## SFO Follow-Up: Settlement Basis Evidence

2026-06-26 follow-up 把 SFO 这笔进一步拆开后，结论更明确：市场不是只在同一条 hourly METAR 里慢慢发现 RMK，而是先被更早的 5-minute ASOS/MADIS HFMETAR 粗口径影响，随后被 WU/routine-hourly 结算口径拉回。

公开 WU/Weather.com historical API 对 `KSFO:9:US`、`2026-06-25` 的 hourly observations 显示：

| valid UTC | WU temp F |
|---|---:|
| 2026-06-25T18:56:00Z | 67 |
| 2026-06-25T19:56:00Z | 69 |
| 2026-06-25T20:56:00Z | 69 |
| 2026-06-25T21:56:00Z | 67 |

同一时间段的 IEM ASOS/METAR history 显示两条不同口径：

| valid UTC | feed | raw temp group | interpreted |
|---|---|---|---|
| 19:30-19:55 | `MADISHF` 5-minute | `T02100130` | 21.0C = 69.8F，naive whole-F 会看成 70F |
| 19:56 | routine METAR | `T02060128` | 20.6C = 69.1F，WU hourly reports 69F |
| 20:00-20:25 | `MADISHF` 5-minute | `T02100130` | 仍像 70F under naive rounding |

盘口也符合这个分歧：

| Time UTC | Market reading |
|---|---|
| 19:46 | `68-69 YES` 从约 0.38/0.43 掉到 0.24/0.30，像是有人提前看到了 5-minute `21.0C` / 70F 口径。 |
| 19:56 | routine METAR 发布，RMK 是 `20.6C`，WU historical hourly 后验为 69F。 |
| 20:00-20:12 | 市场仍在 0.3-0.5 区间摇摆，说明两种口径没有立刻统一。 |
| 20:13 | `68-69 YES` 拉到 0.59/0.62，第一次明确向 WU/routine-hourly 69F 纠正。 |
| 20:59-21:02 | YES 进入 0.82-0.96 区间，基本确认。 |

所以这笔真正的交易形态不是“RMK 发布慢”，而是：

```text
5-minute MADIS/HFMETAR coarse reading says crossed
routine/WU hourly settlement reading says not crossed
market 先按 crossed 砸 YES
随后按 settlement basis 纠正
```

这比旧的 crossed-NO 更有研究价值。正确方向应是 shadow 监控：

1. 发现 `MADISHF/5-min/main-C` 显示打穿，但 `routine METAR RMK` 或 WU hourly 仍没打穿。
2. 若对应 bracket YES 被市场错杀到足够低。
3. 反向 taker BUY YES，而不是继续 BUY NO。

但这仍不是 live 结论。必须先补实时 WU/native/routine first-seen 日志，否则只能事后证明口径，无法确认当时可交易速度。

## When This Happens

这种情况通常要同时满足：

1. 市场是 whole-F 结算，常见于 WU/native-F 口径城市。
2. 触发源是 METAR，且 runner 使用正文整数摄氏温度。
3. 正文整数 C 换算后刚好跨到下一华氏 bracket。
4. RMK `Txxxx` 十分之一摄氏温度仍落在低一档。
5. 市场盘口没有立刻打成确定，说明参与者对 source basis 也存在分歧。

典型危险温度包括：

| main C | main round F | RMK example | RMK round F | Risk |
|---:|---:|---:|---:|---|
| 21C | 70F | 20.6C | 69F | 会误判 `68-69°F` 已死。 |
| 31C | 88F | 30.6C | 87F | 会误判 `87°F or below` 已死。 |
| 32C | 90F | 31.7C | 89F | 会误判 `88-89°F` 已死。 |

## Implications

1. `metar_cross_prev_no_shadow` 不能在 F/WU 城市用 main integer-C crossing 做真钱 crossed-NO。
2. 更有研究价值的是反向监控：当市场因为 main C 把 YES 打低，但 RMK/native-F 没确认 crossing，是否可以买被错杀的 YES。
3. 这个方向必须先 shadow，因为它已经不是无风险 crossing，而是 source-basis / microstructure trade。
4. 后续监控要把 raw METAR、main temp、RMK T-group、WU/native-F latest high 和 orderbook 一起落盘。

## Next Work

1. 给 timing monitor 增加 METAR main-vs-RMK、MADISHF-vs-routine、WU historical/current 解析字段。2026-06-26 已接入：
   `iem_asos_madishf_latest`、`iem_asos_routine_latest`、`iem_asos_latest_raw`、
   `weather_com_current`、`weather_com_history_hourly`。
2. 对 F/WU 城市新增 boundary ambiguity flag：5-minute says crossed, routine/WU says not crossed。
3. 对 ambiguity 事件记录盘口从 report 到 +180m 的 YES/NO 路径。
4. 找 WU/native-F 可用源，记录它的 first-seen 时间，确认市场纠正是否跟它同步。
5. 只在 shadow 里研究“买被错杀 YES”，不要恢复旧 crossed-NO live。

实时观测命令：

```bash
TIMING_MONITOR_HTTP_TIMEOUT_SEC=12 \
TIMING_MONITOR_WEATHER_PROXY_MODE=all \
.venv/bin/python scripts/ops/weather_source_orderbook_timing_monitor.py loop \
  --include-research-cities \
  --cities SanFrancisco \
  --sources weather_com_current weather_com_history_hourly iem_asos_madishf_latest iem_asos_routine_latest iem_asos_latest_raw \
  --base-interval-sec 20 \
  --burst-interval-sec 2 \
  --burst-window-min 10 \
  --max-workers 8
```

注意：`weather_com_*` 在本机 direct 会 403，带 WU/Weather.com header 且走本机代理可用；
N100 若没有同等代理，先只能跑 IEM/MADISHF-vs-routine 线，不能把 Weather.com 当已验证的 N100 实时源。
