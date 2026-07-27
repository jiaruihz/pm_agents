# KNMI EHAM → METAR → WU alignment v1

## 数据快照

- 数据源：KNMI Open Data 10-minute station 240 historical files；Mac JRS `source_events` EHAM METAR；Weather.com/WU EHAM history；`runtime/weather.db settlement_outcomes`
- 窗口：`2026-07-22..2026-07-26`；KNMI `720` rows；next-METAR `714` rows；WU `5/5` days；canonical settlement `2/5` days
- grain：observation→next routine METAR；city-day→WU/settlement。历史 KNMI retrieval 不是 PIT first-seen，不能用于延迟/盘口研究。
- unsettled：`3/5`；missing_bracket：`0`（无 winner 的日期按 coverage gap，不伪装为策略筛除）

## 结论

- `ta` arithmetic-round → 下一份 EHAM METAR exact：`523/714 (73.2%)`；±1°C：`708/714 (99.2%)`。
- `tx` arithmetic-round → 下一份 EHAM METAR exact：`482/714 (67.5%)`；±1°C：`703/714 (98.5%)`。
- 下一份 METAR bias/MAE：`ta -0.021/0.276°C`；`tx +0.158/0.340°C`。因此逐报文映射优先 `ta`。
- 日最高对 WU：`ta-WU` median `0°C`；`tx-WU` median `0°C`。
- 日最高 exact WU：`ta 3/5`；`tx 4/5`。因此日最高候选优先 `tx`，但仍不能当 settlement latch。
- `tx > WU` terminal-false-cross days：`1/5`。
- 结论等级：`inconclusive`。该窗口只校准 source basis；forward collector 从 2026-07-28 起才具备真实 first-seen clock，不授权 live。

## 每日对照

| Date | KNMI ta max raw→round | KNMI tx max raw→round | EHAM METAR max | WU max | canonical winner | tx false cross |
|---|---:|---:|---:|---:|---|---:|
| `2026-07-22` | 20.8→21 | 21.2→21 | 20 | 20 | `20` | 1 |
| `2026-07-23` | 20.3→20 | 20.7→21 | 21 | 21 | `21` | 0 |
| `2026-07-24` | 24.1→24 | 24.3→24 | 24 | 24 | `coverage_gap` | 0 |
| `2026-07-25` | 24.8→25 | 25.1→25 | 25 | 25 | `coverage_gap` | 0 |
| `2026-07-26` | 21.8→22 | 21.9→22 | 22 | 22 | `coverage_gap` | 0 |

## 双漏斗

- signal funnel（observation grain）：KNMI files `720` → next EHAM METAR `714`。
- evidence funnel（city-day grain）：KNMI `5` → WU `5` → canonical winner `2` → PIT book `0` → fill `0`。

## 8 环

覆盖 source/reference/settlement basis；缺 PIT book、执行、容量、PnL、显著性 forward。`significance=NA baseline=NA forward=FAIL conclusion=inconclusive`。
