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

## 发布节奏与采集策略

- 历史文件 metadata `720` 个：`created - interval_end` min/p50/p95/p99/max = `217/221/225/230/251s`；超过 5 分钟 `0` 个。
- 这批样本的初次创建窗口为约 `+03:37..+04:11`。生产采集采用保守 hot window `+03:25..+04:20` 每 `10s` list；窗口外每 `300s`，并会提前唤醒到下一个 hot window。约 `48` 次 list/hour，低于 Open Data registered key 的 `1000/hour`。
- `lastModified - interval_end` p50/p95/max = `819/824/4424s`；`lastModified-created` p50/max = `598/4203s`。因此同一 filename 必须按 revision 重采，不能 filename-only dedupe。
- KNMI 官方只承诺 10 分钟文件在几分钟后可用，不把上述 5 日经验窗口当 SLA；cold polling 用来捕捉异常延迟，forward first-seen 会继续校准窗口。

## Cross-NO 事件检验

- 事件定义：在下一份 EHAM METAR 之前，KNMI arithmetic-round 首次高于当日已见 METAR running max；每个 `date × source_field × prior_max` 只保留首个事件。
- `ta`：events `24` / independent dates `5`；下一 KNMI 仍 cross `18/24 (75.0%)`；下一 METAR confirm `22/24 (91.7%)`；WU final confirm `24/24 (100.0%)`；terminal false `0/24 (0.0%)`。
- `tx`：events `25` / independent dates `5`；下一 KNMI 仍 cross `22/25 (88.0%)`；下一 METAR confirm `18/25 (72.0%)`；WU final confirm `24/25 (96.0%)`；terminal false `1/25 (4.0%)`。
- action：只进入 `collector + zero-notional shadow`。5 个 independent city-days、无 PIT book/成交分母，且已有 terminal false cross，不能升 live。

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
