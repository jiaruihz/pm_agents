# Current-YES Codex Prompt Version Comparison v1

Date: 2026-06-21

## Verdict

Do not enable Codex preflight as a live blocking gate yet.

The prompt versioning and replay harness are now in place, and `shadow_only` is no longer treated as a strategy filter. On the 2026-06-18 to 2026-06-21 current-YES rollout window, `current_yes_codex_v3_price_aware_veto` is the best exploratory prompt, but support is still too thin for live blocking:

```text
significance=NA
baseline=NA
forward=FAIL
conclusion=inconclusive / shadow_candidate
```

## Data Snapshot

- Local sync/rebuild was refreshed on 2026-06-21 before this replay. `weather_clob_fill_coverage_gate.py` passed.
- Evidence layer: remote forward telemetry mirrored under `runtime/weather_edge_v1/remote_pm_agent/`, matched order JSONL, and `runtime/weather.db:settlement_outcomes`.
- Row grain: one matched order row joined to the nearest planned telemetry row for the same `strategy_instance/city/target_date/bracket`.
- Important caveat: 2026-06-21 rows, including Wuhan, are not settled in `settlement_outcomes` at report time, so the official settled accuracy denominator excludes them.

Baseline matched orders:

| window | matched rows | settled | wins | losses | accuracy | est. PnL | ROI |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2026-06-18..2026-06-21 | 34 | 24 | 18 | 6 | 75.0% | -$3.713195 | -3.6404% |

## Prompt Versions

Backtest policy here ignores `shadow_only`; only `decision=veto` is treated as filtered.

| prompt version | decisions | kept rows | kept settled | kept W-L | kept accuracy | kept est. PnL | kept ROI | caught losses | loss recall | false-veto wins | veto loss precision |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `current_yes_codex_v1_reheat_guard` | veto 19 / shadow 15 | 15 | 10 | 9-1 | 90.0% | +$7.460806 | +16.2191% | 5 | 83.3333% | 9 | 35.7143% |
| `current_yes_codex_v2_veto_loss_detector` | allow 12 / veto 10 / shadow 12 | 24 | 16 | 12-4 | 75.0% | -$4.863321 | -6.9476% | 2 | 33.3333% | 6 | 25.0% |
| `current_yes_codex_v3_price_aware_veto` | allow 22 / veto 9 / shadow 3 | 25 | 17 | 15-2 | 88.2353% | +$10.870125 | +15.31% | 4 | 66.6667% | 3 | 57.1429% |

## Wuhan 2026-06-21 Check

The prompt comparison does not solve the Wuhan failure by itself.

- v1 vetoed both Wuhan `28` orders.
- v2 vetoed the stale `peak_forming_micro` order, but allowed the fresh `fade_confirmed` order.
- v3 also vetoed the stale `peak_forming_micro` order, but allowed the fresh `fade_confirmed` order.

This matches the root issue: the two orders were not the same signal, but both still relied on the same fragile proposition that `28C` would survive. A human reviewer should not treat the 11:00 local pullback alone as enough evidence that the day is finished heating.

## Deployment Decision

Deploy the code that makes prompt versions explicit and keeps `shadow_only` out of the blocking policy.

Do not enable `--enable-llm-preflight --llm-preflight-mode block_veto` on N100 yet. N100 does not have Codex installed, and moving a personal Codex login/auth state onto the headless live server is not the right production boundary. The safer next live architecture is a Mac-side Codex preflight advisor/queue that writes auditable decisions back to N100; until that exists, N100 should keep LLM preflight disabled or advisory-only.

## Artifacts

- Script: `scripts/analysis/reheat_risk/replay_current_yes_codex_preflight_v1.py`
- Prompt registry: `src/strategies/weather_edge_v1/tools/current_yes_codex_prompts.py`
- Summary JSON: `docs/analysis/2026-06/generated/current_yes_codex_preflight_replay_v1/summary.json`
- Replay caches:
  - `docs/analysis/2026-06/generated/current_yes_codex_preflight_replay_v1/codex_preflight_replay.jsonl`
  - `docs/analysis/2026-06/generated/current_yes_codex_preflight_replay_v1/codex_preflight_replay__current_yes_codex_v2_veto_loss_detector.jsonl`
  - `docs/analysis/2026-06/generated/current_yes_codex_preflight_replay_v1/codex_preflight_replay__current_yes_codex_v3_price_aware_veto.jsonl`
