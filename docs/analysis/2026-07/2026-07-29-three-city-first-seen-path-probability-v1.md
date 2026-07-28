# Helsinki / Amsterdam / Tokyo First-Seen Path Probability v1

Status: `research_probability_head_v1`; no plan/order/fill/exit/live change

## 结论

第一版已实现为温度主导、PIT METAR context 辅助的 expanding-date OOF `P(new strict high within 30/60/120m)`。所有 fast rows 必须有显式 `source_first_seen_at_utc`；脚本不使用 fetched/issue time 冒充 first-seen。

本轮仍是 probability-layer research，不是 residual trading policy：当前 DB 中三城 fast-source 事件尚未进入 canonical `weather_information_events → weather_state_checkpoints`，因此没有同 checkpoint 的 PIT market baseline，不能产生可交易 residual。本次代码已经补上 high-frequency producer event header、历史 exact/late-backfill rebuild 分流和 zero-notional forward input；新采集/重建后才能形成 checkpoint。

## Frozen target

- window: `2026-07-08..2026-07-27`
- cities/sources: `Helsinki/FMI`, `Amsterdam/KNMI`, `Tokyo/JMA AMeDAS`
- labels: future strict source high at 30/60/120m; canonical settlement is used only for source-basis audit, never backfilled as a feature
- validation: expanding OOF by whole target_date; every training date receives equal weight

## Source coverage

| city | source | exact events | exact dates | missing exact first-seen | canonical fast events | settlement basis aligned/evaluable days | terminal false days |
|---|---:|---:|---:|---:|---:|---:|---:|
| Helsinki | `fmi` | 591 | 7 | 32869 | 0 | 5/6 | 1 |
| Amsterdam | `knmi` | 0 | 0 | 0 | 0 | 0/0 | 0 |
| Tokyo | `jma_amedas` | 1 | 1 | 41958 | 0 | 0/0 | 0 |

## Same-denominator probability score

括号内为 Brier 相对 expanding historical prior 的差值，负数才是改善。

| city | horizon min | OOF dates | events | prior Brier | fast path Brier (Δ) | +METAR Brier (Δ) |
|---|---:|---:|---:|---:|---:|---:|
| Helsinki | 30 | 2 | 187 | 0.2263 | 0.1479 (-0.0784) | 0.1666 (-0.0597) |
| Helsinki | 60 | 2 | 182 | 0.2565 | 0.1413 (-0.1152) | 0.1824 (-0.0741) |
| Helsinki | 120 | 2 | 170 | 0.2530 | 0.1229 (-0.1301) | 0.1681 (-0.0849) |

## Signal funnel

- raw matching rows: `75425`
- distinct collector-exact temperature events: `592`
- PIT path states: `592`
- OOF predictions (model × horizon): `1617`
- policy-selected expressions: `0`

## Evidence funnel

- canonical settlement basis city-days: `8`
- canonical fast information events/checkpoints: `0` / `0`
- same-checkpoint PIT market ladder: `0`
- executable expressions / fills: `0 / 0`

## Interpretation boundary

- fast-source future-high label measures path information; it is not settlement truth.
- a lower OOF Brier is evidence that the first-seen print changes path probability, not evidence of fee-adjusted alpha.
- Amsterdam remains in the frozen universe even if KNMI key/collector coverage is zero; that is a coverage gap, not a strategy filter.
- canonical ingest/rebuild support is implemented, but the current canonical DB/zero-notional process has not been rebuilt or restarted in this research run.
- next evidence step is to accumulate/rebuild these fast events and attach pre/post event full-ladder market probabilities; only then is `model probability - market probability` a residual that can be tested.

## Gate

`significance=NA market_baseline=FAIL forward=RESEARCH_ONLY conclusion=inconclusive`
