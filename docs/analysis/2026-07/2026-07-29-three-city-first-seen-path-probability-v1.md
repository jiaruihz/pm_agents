# Helsinki / Amsterdam / Tokyo First-Seen Path Probability v1

Status: `research_probability_head_v1`; no plan/order/fill/exit/live change

## 结论

第一版已实现为温度主导、PIT METAR context 辅助的 expanding-date OOF `P(new strict high within 30/60/120m)`。所有 fast rows 必须有 collector 显式 exact timestamp（通用/JMA 为 `source_first_seen_at_utc`，KNMI Open Data 为 `knmi_first_seen_at_utc`）；脚本不使用 fetched/local-detect/issue time 冒充 first-seen。

本轮仍是 probability-layer research，不是 residual trading policy：当前 DB 中三城 fast-source 事件尚未进入 canonical `weather_information_events → weather_state_checkpoints`，因此没有同 checkpoint 的 PIT market baseline，不能产生可交易 residual。本次代码已经补上 high-frequency producer event header、历史 exact/late-backfill rebuild 分流和 zero-notional forward input；新采集/重建后才能形成 checkpoint。

## Frozen target

- window: `2026-07-08..2026-07-27`
- cities/sources: `Helsinki/FMI`, `Amsterdam/KNMI`, `Tokyo/JMA AMeDAS`
- labels: future strict source high at 30/60/120m; canonical settlement is used only for source-basis audit, never backfilled as a feature
- validation: expanding OOF by whole target_date; every training date receives equal weight

## Current raw capture snapshot

这一层只回答 authoritative raw journal 中是否已有 exact capture；不等于 frozen window 的研究分母，也不等于 canonical ingest 已完成。

| city | journal | exact rows | target-date range | latest exact first-seen UTC | captured fields |
|---|---|---:|---|---|---|
| Helsinki | `live_cross_observations` | 630 | 2026-07-21..2026-07-28 | 2026-07-28T18:51:29.566192+00:00 | `pressure_hpa,temp_c,wind_speed_kt` |
| Amsterdam | `knmi_open_data` | 499 | 2026-07-27..2026-07-29 | 2026-07-29T02:14:24.899942+00:00 | `max_temp_c_past_10m,temp_c` |
| Tokyo | `live_cross_observations` | 621 | 2026-07-22..2026-07-29 | 2026-07-29T02:06:37.846956+00:00 | `temp_c` |

## Frozen-window research coverage

| city | source | exact events | exact dates | legacy/non-exact raw rows (not unique) | canonical fast events | settlement basis aligned/evaluable days | terminal false days |
|---|---:|---:|---:|---:|---:|---:|---:|
| Helsinki | `fmi` | 591 | 7 | 32869 | 0 | 5/6 | 1 |
| Amsterdam | `knmi` | 37 | 1 | 0 | 0 | 0/0 | 0 |
| Tokyo | `jma_amedas` | 579 | 7 | 41960 | 0 | 3/6 | 3 |

## Same-denominator probability score

括号内为 Brier 相对 expanding historical prior 的差值，负数才是改善。

| city | horizon min | OOF dates | events | prior Brier | fast path Brier (Δ) | +METAR Brier (Δ) |
|---|---:|---:|---:|---:|---:|---:|
| Helsinki | 30 | 2 | 187 | 0.2293 | 0.1431 (-0.0862) | 0.1714 (-0.0579) |
| Helsinki | 60 | 2 | 182 | 0.2568 | 0.1417 (-0.1151) | 0.1851 (-0.0717) |
| Helsinki | 120 | 2 | 169 | 0.2544 | 0.1282 (-0.1262) | 0.1781 (-0.0763) |
| Tokyo | 30 | 2 | 186 | 0.2242 | 0.1151 (-0.1090) | 0.1144 (-0.1097) |
| Tokyo | 60 | 1 | 91 | 0.2485 | 0.1300 (-0.1185) | 0.1223 (-0.1262) |
| Tokyo | 120 | 1 | 84 | 0.2478 | 0.0963 (-0.1516) | 0.0795 (-0.1683) |

## Signal funnel

- raw matching rows: `77103`
- distinct collector-exact temperature events: `1207`
- PIT path states: `1207`
- OOF predictions (model × horizon): `2697`
- policy-selected expressions: `0`

## Evidence funnel

- canonical settlement basis city-days: `15`
- canonical fast information events/checkpoints: `0` / `0`
- same-checkpoint PIT market ladder: `0`
- executable expressions / fills: `0 / 0`

## Interpretation boundary

- fast-source future-high label measures path information; it is not settlement truth.
- a lower OOF Brier is evidence that the first-seen print changes path probability, not evidence of fee-adjusted alpha.
- source coverage means exact rows admitted from all authoritative collector journals; it must not be described as whether a city is currently configured or running.
- legacy/non-exact raw rows are repeated historical poll outputs, not a count of unique observations or failed current captures; they stay outside the PIT denominator.
- canonical ingest/rebuild support is implemented, but the current canonical DB/zero-notional process has not been rebuilt or restarted in this research run.
- next evidence step is to accumulate/rebuild these fast events and attach pre/post event full-ladder market probabilities; only then is `model probability - market probability` a residual that can be tested.

## Gate

`significance=NA market_baseline=FAIL forward=RESEARCH_ONLY conclusion=inconclusive`
