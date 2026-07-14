<!-- M5：新机制/概率/快源/PIT 研究；口径见 weather-strategy-research。 -->

# Weather 研究：{topic}

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
