# Current-YES Codex Preflight Replay v1

Date: 2026-06-21
Window: target_date 2026-06-18..2026-06-21
Evidence layer: split current-YES forward telemetry + raw live orders + `runtime/weather.db:settlement_outcomes`

## Data Snapshot

- Synced N100 runtime and market data at 2026-06-21 21:06 Asia/Shanghai.
- Rebuilt `runtime/weather.db`; `run_stack.sh` completed fact rebuild and CLOB gate, then failed only on busy FE port 5174.
- CLOB fill coverage gate: `gate_pass=true`.
- `fact_trades`: 4,400 rows; `live_real=855`; max `order_ts_utc=2026-06-21T13:07:15Z`; max `fill_ts_utc=2026-06-11T09:59:21+00:00`.
- Current-YES split telemetry was copied from N100:
  - `runtime/weather_edge_v1/remote_pm_agent/theta_current_yes_fade_confirmed_tiny_live_v1/forward_telemetry.jsonl`
  - `runtime/weather_edge_v1/remote_pm_agent/theta_current_yes_peak_forming_micro_tiny_live_v1/forward_telemetry.jsonl`

## Funnel

Row grain: one planned current-YES candidate / live order attempt.

- planned telemetry rows: 36
- submitted live orders: 35
- matched live orders: 34
- matched rows with official settlement: 24
- matched rows still unsettled, all 2026-06-21: 10

Main correctness uses only official `settlement_outcomes` settled rows.

## Baseline

Previous live behavior on matched settled orders:

| slice | settled matched | wins | losses | accuracy | est. PnL | est. ROI |
|---|---:|---:|---:|---:|---:|---:|
| all matched | 24 | 18 | 6 | 75.0% | -$3.71 | -3.64% |
| fade_confirmed | 3 | 3 | 0 | 100.0% | +$1.97 | +17.93% |
| peak_forming_micro | 21 | 15 | 6 | 71.4% | -$5.69 | -6.25% |

The damage is concentrated in `peak_forming_micro`; `fade_confirmed` has only 3 settled fills, so the 100% figure is too thin to promote.

## Codex Replay

Codex preflight was replayed on the same 36 planned rows using local `codex exec`, not an LLM API key. Order JSON lacks weather context, so the replay joins matched orders back to the corresponding planned telemetry row.

Codex decisions on matched orders with replay:

- `veto`: 19
- `shadow_only`: 15
- `allow`: 0

If live blocked both `veto` and `shadow_only`, it would take zero trades. That policy is too conservative and has no accuracy denominator.

More useful policies:

| policy | settled kept | wins | losses | accuracy | est. PnL | est. ROI |
|---|---:|---:|---:|---:|---:|---:|
| block `veto`, keep `shadow_only` | 10 | 9 | 1 | 90.0% | +$7.46 | +16.22% |
| block `veto` with confidence >= 0.80 | 12 | 11 | 1 | 91.7% | +$9.15 | +17.59% |

Decision x outcome on settled matched rows:

| Codex decision | wins | losses |
|---|---:|---:|
| veto | 9 | 5 |
| shadow_only | 9 | 1 |

## Verdict

`block_veto_and_shadow_only` is not acceptable: it blocks everything.

The promising replay policy is `block_high_conf_veto_ge_0.80`: it keeps 12 settled rows, improves accuracy from 75.0% to 91.7%, and moves estimated ROI from -3.64% to +17.59%. This is still low-sample and replay-only, not a confirmed live upgrade.

Recommended next implementation is not “LLM decides live yes/no” broadly. It should be:

- Codex preflight runs locally/advisory first.
- Hard block only `decision=veto AND confidence>=0.80`.
- Treat `shadow_only` as reduced-size or paper/shadow, not automatic hard block.
- Keep deterministic safety gates for stale observation and forecast clock.

Contract labels:

- significance=NA, baseline=PASS exploratory, forward=FAIL/NA
- conclusion=inconclusive / shadow_candidate

Artifacts:

- Script: `scripts/analysis/reheat_risk/replay_current_yes_codex_preflight_v1.py`
- JSON summary: `docs/analysis/2026-06/generated/current_yes_codex_preflight_replay_v1/summary.json`
- Codex replay cache: `docs/analysis/2026-06/generated/current_yes_codex_preflight_replay_v1/codex_preflight_replay.jsonl`
