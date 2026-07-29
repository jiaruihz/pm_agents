# Three-City Pre-Cross Path Pretrain v2

Status: `research_path_head_v2`; zero-notional; no live behavior change

## 结论

用户直觉对应的可研究对象是对的：不等 source cross 后才产生信号，而是在每份报文 first-seen 时更新 `P(未来 30/60/120m 跨入下一 whole-degree lattice)`。历史 archive 只训练温度路径动力学；collector-exact 日期只负责 first-seen calibration 和 forward OOF。

当前只完成 weather probability head。production manifest 的 canonical DB route 健康，但 refresh LaunchAgent strict check 失败；且同 checkpoint settled/full-depth market evidence 不足，所以本报告不声称 market residual 或可交易 alpha。

冻结的历史 path pretrain 在 exact collector 的全部日期上直接 forward 评分；exact-only 与 calibration layer 只有积累满 3 个先前日期后才开始评分。后者当前没有稳定优于历史 path，因此 exact timing 暂时只作为 calibration telemetry，不覆盖基础 path probability。

## Data separation

| city | archive path observations/dates | cadence | archive range | exact events/dates |
|---|---:|---:|---|---:|
| Helsinki | 9054 / 63 | 10m | 2026-05-19..2026-07-20 | 684 / 9 |
| Amsterdam | 2960 / 69 | 10/60m | 2026-05-19..2026-07-26 | 230 / 3 |
| Tokyo | 9071 / 63 | 10m | 2026-05-19..2026-07-20 | 695 / 9 |

这里的“历史全量”固定为 canonical strategy-era 可比窗口：从 `2026-05-19` 到各城 exact collector 首日之前，期间不抽样、不挑天气日。Tokyo/Helsinki 使用官方原生 10m；Amsterdam 使用完整 KNMI hourly archive，有原生 10m archive 的日期整日替换为 10m，避免同日混频。

Archive clock is observation time, never first-seen. It can teach path transitions but cannot measure source latency, book reaction, or execution.

## Exact-forward probability score

主 label 是跨下一档，不是任意 +0.1°C strict high。括号为相对历史 base-rate Brier delta；负数更好。

`Brier = mean((p-y)^2)`，其中结果发生 `y=1`，未发生 `y=0`；它衡量概率预测离真实结果有多远，`0` 最好。例如报 70% 后事件发生，该次误差为 `(0.7-1)^2=0.09`。

| city | horizon | OOF dates | events | history prior | exact-only | history path | + exact calibration |
|---|---:|---:|---:|---:|---:|---:|---:|
| Helsinki | 30 | 9 | 648 | 0.1389 | 0.0946 (-0.0443) | 0.0752 (-0.0637) | 0.1035 (-0.0354) |
| Helsinki | 60 | 9 | 625 | 0.2175 | 0.1210 (-0.0966) | 0.0898 (-0.1277) | 0.1450 (-0.0725) |
| Helsinki | 120 | 9 | 566 | 0.2575 | 0.1212 (-0.1363) | 0.0857 (-0.1718) | 0.1514 (-0.1061) |
| Amsterdam | 30 | 3 | 216 | 0.1438 | NA | 0.0687 (-0.0750) | NA |
| Amsterdam | 60 | 3 | 203 | 0.1908 | NA | 0.0614 (-0.1294) | NA |
| Amsterdam | 120 | 3 | 180 | 0.2251 | NA | 0.0694 (-0.1556) | NA |
| Tokyo | 30 | 9 | 657 | 0.1743 | 0.1217 (-0.0526) | 0.1315 (-0.0428) | 0.1174 (-0.0570) |
| Tokyo | 60 | 8 | 626 | 0.2647 | 0.1413 (-0.1234) | 0.1265 (-0.1382) | 0.1663 (-0.0984) |
| Tokyo | 120 | 8 | 566 | 0.3286 | 0.0593 (-0.2693) | 0.0812 (-0.2474) | 0.1190 (-0.2095) |

历史 path head 三城九个 horizon 的 date-block bootstrap Brier-delta CI 上界最大为 `-0.0193`，均低于 0；Amsterdam 独立 exact 日期仍只有 3 个，其显著性远弱于另外两城；结论只限 weather-path probability。

## Report-update interpretation

只比较 next-lattice target 未改变的连续报文，避免把 cross 后的新目标概率误算成一次更新。

- `m2_history_pretrain`: updates `3883`; positive updates `1831`; cross rate positive/nonpositive `0.400/0.239`
- `m3_history_plus_exact_calibration`: updates `2145`; positive updates `1144`; cross rate positive/nonpositive `0.377/0.234`
- Helsinki 60m positive updates 且随后确实 cross：`116`；first-seen 到 cross 中位 lead `25.0m`
- Amsterdam 60m positive updates 且随后确实 cross：`28`；first-seen 到 cross 中位 lead `20.0m`
- Tokyo 60m positive updates 且随后确实 cross：`113`；first-seen 到 cross 中位 lead `29.8m`
- `probability_update > 0` 只是连续天气信号，不是 entry gate；必须与同一 checkpoint 的 normalized ladder market probability 比较后才能形成 residual。
- cross 前可以表达的含义是：某份报文让下一档概率上升，而盘口尚未等幅更新；不是提前固定买某个 bracket，也不是把 touch 当 final-exact。

## Signal funnel

- archive path states: `21085` observation-grain / `195` city-days
- exact path states: `1609` event-grain / `21` city-days
- exact OOF predictions: `13302` model×event×horizon
- policy-selected expressions: `0`

## Evidence funnel

- exact PIT weather event: available
- same-checkpoint normalized market ladder: coverage insufficient / not scored here
- settled full-distribution rows: insufficient
- executable expression / fills: `0 / 0`

## Action

保持 research + collector。下一步把本概率头接到 zero-notional checkpoint：保存`p_before/p_after`、market ladder before/after 与全量 bracket×side telemetry。达到 settled forward 日期后再检验 residual；不改任何 live runner。

## Gate

`significance=NA market_baseline=FAIL forward=RESEARCH_ONLY conclusion=inconclusive`
