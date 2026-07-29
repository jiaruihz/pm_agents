# Three-City Pre-Cross Path Pretrain v2

Status: `research_path_head_v2`; zero-notional; no live behavior change

## 结论

用户直觉对应的可研究对象是对的：不等 source cross 后才产生信号，而是在每份报文 first-seen 时更新 `P(未来 30/60/120m 跨入下一 whole-degree lattice)`。历史 archive 只训练温度路径动力学；collector-exact 日期只负责 first-seen calibration 和 forward OOF。

当前只完成 weather probability head。production manifest 的 canonical DB route 健康，但 refresh LaunchAgent strict check 失败；且同 checkpoint settled/full-depth market evidence 不足，所以本报告不声称 market residual 或可交易 alpha。

在当前 5–6 个 OOF 日期上，历史 path pretrain 是主结果；再用仅 3 个起始 exact 日期拟合 calibration layer 反而一致变差，因此 exact timing 暂时只作为 forward calibration telemetry，不覆盖基础 path probability。

## Data separation

| city | archive path observations/dates | archive range | exact events/dates |
|---|---:|---|---:|
| Helsinki | 1194 / 13 | 2026-07-08..2026-07-20 | 684 / 9 |
| Amsterdam | 1667 / 15 | 2026-06-18..2026-07-26 | 230 / 3 |
| Tokyo | 1424 / 13 | 2026-07-08..2026-07-20 | 695 / 9 |

Archive clock is observation time, never first-seen. It can teach path transitions but cannot measure source latency, book reaction, or execution.

## Exact-forward probability score

主 label 是跨下一档，不是任意 +0.1°C strict high。括号为相对历史 base-rate Brier delta；负数更好。

| city | horizon | OOF dates | events | history prior | exact-only | history path | + exact calibration |
|---|---:|---:|---:|---:|---:|---:|---:|
| Helsinki | 30 | 6 | 437 | 0.1422 | 0.0946 (-0.0476) | 0.0776 (-0.0647) | 0.1015 (-0.0408) |
| Helsinki | 60 | 6 | 422 | 0.2207 | 0.1210 (-0.0997) | 0.0993 (-0.1214) | 0.1419 (-0.0788) |
| Helsinki | 120 | 6 | 383 | 0.2530 | 0.1212 (-0.1318) | 0.1165 (-0.1365) | 0.1544 (-0.0986) |
| Tokyo | 30 | 6 | 460 | 0.1455 | 0.1217 (-0.0237) | 0.1029 (-0.0425) | 0.1165 (-0.0290) |
| Tokyo | 60 | 5 | 350 | 0.2627 | 0.1413 (-0.1214) | 0.1372 (-0.1255) | 0.1644 (-0.0983) |
| Tokyo | 120 | 5 | 312 | 0.3078 | 0.0593 (-0.2485) | 0.0885 (-0.2193) | 0.1168 (-0.1910) |

历史 path head 六个 Helsinki/Tokyo horizon 的 date-block bootstrap Brier-delta CI 上界最大为 `-0.0203`，均低于 0；但独立日期仍只有 5–6 个，结论只限 weather-path probability。

## Report-update interpretation

只比较 next-lattice target 未改变的连续报文，避免把 cross 后的新目标概率误算成一次更新。

- `m2_history_pretrain`: updates `2145`; positive updates `1061`; cross rate positive/nonpositive `0.379/0.243`
- `m3_history_plus_exact_calibration`: updates `2145`; positive updates `1131`; cross rate positive/nonpositive `0.376/0.237`
- Helsinki 60m positive updates 且随后确实 cross：`71`；first-seen 到 cross 中位 lead `28.8m`
- Tokyo 60m positive updates 且随后确实 cross：`70`；first-seen 到 cross 中位 lead `25.1m`
- `probability_update > 0` 只是连续天气信号，不是 entry gate；必须与同一 checkpoint 的 normalized ladder market probability 比较后才能形成 residual。
- cross 前可以表达的含义是：某份报文让下一档概率上升，而盘口尚未等幅更新；不是提前固定买某个 bracket，也不是把 touch 当 final-exact。

## Signal funnel

- archive path states: `4285` observation-grain / `41` city-days
- exact path states: `1609` event-grain / `21` city-days
- exact OOF predictions: `9456` model×event×horizon
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
