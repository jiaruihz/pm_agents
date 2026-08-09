<!--
M5：新机制/概率/快源/PIT 研究 brief；固定方法见 weather-strategy-research，永久边界见 AGENTS.md。
每次只填一个 hypothesis 和一个唯一动作；不把本模板复制成新的 prompt 章程。
-->

# Weather 研究：{topic}

## 单轮 brief

- hypothesis（可证伪，一句话）：
- data scope（city/source/target-date，含或不含已查看样本）：
- market capture scope/policy（使用选择性 WS 时；否则 N/A）：
- collector budget（扩采时填 traffic/day、storage/day、retention、stop condition；否则 N/A）：
- acceptance gates（probability / market baseline / forward / execution）：
- 唯一动作（coverage audit / fixed A-B / frozen-forward collector / zero-notional shadow）：
- 不在范围内（尤其是 live、其他城市、额外变体）：

## Readiness（先填；`BLOCKED` 时停止于 coverage/机制诊断）

| 项目 | READY / BLOCKED | 证据 / 缺口 |
|---|---|---|
| PIT state + four clocks | | |
| canonical DB / build identity | | |
| fresh market quote + depth | | |
| settlement / label coverage | | |
| independent target dates | | |
| clean frozen-forward status | | |
| selective capture policy/version + subscription coverage（若使用 WS） | | |
| incremental-book reconstruction parity（若使用 WS） | | |
| sampling grain / message weighting（若使用 WS） | | |

## 结论与动作

{继续 collector / zero-notional shadow / 保持 research / reject expression / 不改 live}

## Target

```text
估计 P({exact outcome} | {PIT state})，并检验相对同一时点 market 的 residual。
```

- physical target / exact-bracket semantics：
- grain / universe：
- decision timestamp：
- label / settlement source：
- executable expression / fee：
- primary metric / market baseline：

## Data integrity / PIT

| 项目 | 值 |
|---|---|
| raw source and coverage | |
| forecast issue/run/hash/age | |
| source first-seen/cadence | |
| source-to-settlement basis | |
| book freshness / archive bias | |
| capture policy / rollout-valid window / subscription set | |
| WS baseline snapshot + delta reconstruction / gap status | |
| eligible checkpoints vs observed frames/messages | |
| label availability | |

## Signal funnel

| 层 | grain | rows | dates |
|---|---|---:|---:|
| raw universe | | | |
| mechanism candidate | | | |
| first event/city-day signal | | | |
| selected | | | |

## Evidence funnel

| 层 | grain | rows | dates | gap |
|---|---|---:|---:|---|
| PIT feature/source | | | | |
| PIT quote | | | | |
| settlement | | | | |
| executable expression | | | | |
| fill | | | | |

## Wide-denominator sanity

{先证明宽分母/base rate，不从少量事件直接下结论。}

## Model / residual

| candidate | rows | logloss | Brier | calibration | delta vs market | date-block CI |
|---|---:|---:|---:|---:|---:|---|

### Microstructure ablation（使用 WS 时必填）

| arm | fixed rows/dates | feature clock | primary score | delta vs level-only | date-block CI |
|---|---:|---|---:|---:|---|
| weather-only | | | | | |
| market level-only | | | | | |
| weather + level | | | | | |
| weather + level + WS dynamics | | | | | |

## Expression / execution

| expression | executable rows | dates | fee-adjusted ROI | excess | 95% CI | fill assumption |
|---|---:|---:|---:|---:|---|---|

## Frozen forward

- train choices frozen：
- forward dates/results：
- multiple testing：
- unresolved blockers：

## Bloodline placement

- shared data logic：
- feature layer：
- `fact_signal_candidates` fields：
- shadow/collector runtime：
- docs/index/registry update：
