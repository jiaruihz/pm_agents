# Reheat Risk Delegation Prompts

Status: superseded-for-now
Created: 2026-06-16

This is a historical delegation template, not a current system prompt or
research contract. New research must use the applicable weather skill plus
`docs/analysis/templates/research.md`; do not copy the task packages below as
current instructions. The mechanisms remain useful context, so the document is
retained rather than deleted.

Use these prompts to spawn focused research threads. Each thread must keep one
main line, but should not overfit itself into a single brittle threshold.

## Shared Instructions For All Reheat Threads

Work in `/Users/deepsleep/projects/pm_agents`.

Read first:

- `AGENTS.md`
- `docs/WEATHER_ANALYSIS_CONTRACT.md`
- `docs/analysis/weather_strategy_research_whitepaper.md`
- `docs/analysis/reheat_risk.md`
- `scripts/analysis/reheat_risk/README.md`

Use the weather strategy performance skill when doing backtests/performance
analysis. Before conclusions, run the mandatory 5-line SQL self-check from
`AGENTS.md`. Use canonical sources: `runtime/weather.db`, `fact_signal_candidates`,
`fact_trades`, and time-aligned raw orderbook only when needed. Do not use legacy
DBs. Do not change N100 live behavior. Do not publish live_real PnL unless the
CLOB fill coverage gate passes.

Output should be human-readable: first a short trading conclusion, then the data
funnel and evidence. Always state row grain and target metric before tables.

## Prompt 1: Reheat Feature Factory

Goal: build the shared intraday feature layer for all reheat-risk strategy heads.

Main line:

```text
Create a reusable reheat-risk feature factory with row grain:
city + target_date + decision_snapshot_ts_utc + decision_hour_local + bracket/outcome.
It should support current_yes_peak_forming, current_yes_fade_confirmed,
higher_no_carry, and low_price_yes_reheat_reversal without each strategy
re-materializing observed max/orderbook/settlement facts.
```

Minimum useful output:

- A script under `scripts/analysis/reheat_risk/`.
- A generated coverage report under `docs/analysis/2026-06/`.
- A schema/field list covering:
  - current temp / running max / decline from max / minutes since max.
  - forecast peak hour / forecast peak delta / forecast values hash.
  - dewpoint/RH/wind/sky/temp trend where available.
  - current YES, d1 NO, d2 NO, and target YES sibling quote fields.
  - final winning bracket, current bracket held, d1 hit, skip-over, target hit.
- A data funnel by date/city/hour explaining what is available and what is still missing.

Do not optimize thresholds yet. This task is about making later experiments share
one clean fact layer.

## Prompt 2: Peak-Forming vs Fade-Confirmed

Goal: compare two current-YES timing heads using the same reheat-risk feature base.

Main line:

```text
Compare current_yes_peak_forming vs current_yes_fade_confirmed.
Peak-forming buys current running-max YES while temperature is still at/near
the high. Fade-confirmed buys after visible decline. Answer whether earlier
entry price advantage pays for the extra reheat risk.
```

Minimum useful output:

- Target metrics:
  - `current_yes_peak_forming_ev`
  - `current_yes_fade_confirmed_ev`
- Same-city/date/hour comparison where possible.
- Train/holdout or prefix walk-forward, not a pure hindsight grid.
- Price/ROI/win-rate/slippage-sensitive summary.
- A simple explanation of whether the strategy should prefer early, late, or a
hybrid rule.

Keep the search flexible: explore forecast peak clock, minutes since max,
decline amount, local/solar time, dewpoint/RH/wind/sky, and market ask, but do
not produce a giant threshold dump without a clear conclusion.

## Prompt 3: Higher NO Carry Expression Selector

Goal: decide when higher NO carry is a better expression than current YES, not
whether reheat-risk exists in the abstract.

Main line:

```text
Build a paired expression selector for the same city-hour state:
current YES vs d1 NO vs d2 NO or a small NO ladder.
Decompose settlement into current-hit, d1-hit, d2-hit, and skip-over states.
Find whether NO carry has real payoff advantage or is usually just a worse
expression of the same no-reheat thesis.
```

Minimum useful output:

- Paired rows with current YES, d1 NO, and d2 NO quotes when available.
- ROI/excess ROI against sibling current YES, not just standalone NO ROI.
- Skip-over contribution separated from no-reheat contribution.
- Price buckets and execution feasibility.
- A decision table: choose current YES, choose d1/d2 NO, choose ladder, or skip.

Do not assume NO carry is independent alpha. Treat it as an expression-layer
candidate until the paired evidence proves otherwise.

## Prompt 4: Low-Price YES Reheat Reversal

This is already being handled in thread `019ec725-3613-7132-88c8-19657d5972b6`.
Use this prompt only to steer that thread, not to open a duplicate.

Main line:

```text
Upgrade low-price YES from naked model_p_yes edge into a reheat-conditioned
convexity sleeve: p_reversal_yes = forecast prior * p_reheat_to_target_context.
The label is target_yes_wins, not current_yes_wins.
```

## Prompt 5: Execution Freshness Gate

Priority: useful, but below prompts 1/2/3.

Main line:

```text
Research when a current-YES intent should cross the spread using a fresh CLOB
ask, remain passive, or skip. The bottleneck is snapshot ask staleness and
reprice risk, not only the weather model.
```

Minimum useful output:

- Fresh-book guarded taker rule.
- `p_win - fresh_ask`, snapshot age, spread/depth, and slippage sensitivity.
- maker/passive/taker/skip decision table.

## Cleanup Prompt: Research Structure Hygiene

Goal: clean half-updated docs/scripts without changing strategy conclusions.

Main line:

```text
Audit the weather research structure for half-migrated names, stale current
references, duplicated reports, old paths, and scripts living in the wrong
analysis module. Fix obvious docs/index/path issues directly, but do not rewrite
historical evidence or change live behavior. Produce a small cleanup report with
what was moved, what was only indexed, and what remains intentionally archival.
```

Scope:

- `docs/WEATHER_DOCS_INDEX.md`
- `docs/analysis/SCRIPT_MIGRATION_MANIFEST.md`
- `docs/analysis/MD_CONSOLIDATION_PLAN.md`
- `docs/analysis/weather_strategy_research_whitepaper.md`
- `docs/analysis/reheat_risk.md`
- `docs/analysis/pre_predict.md`
- `scripts/analysis/reheat_risk/`
- `scripts/analysis/pre_predict/`

This cleanup thread should not run strategy backtests unless needed to verify a
script path. Its job is hygiene, not alpha discovery.
