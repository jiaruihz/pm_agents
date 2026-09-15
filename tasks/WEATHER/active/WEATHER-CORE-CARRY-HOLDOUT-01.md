# WEATHER-CORE-CARRY-HOLDOUT-01
Status: READY_FOR_REVIEW
Repo: /Users/deepsleep/projects/pm_agents
Worktree: /Users/deepsleep/projects/pm_agents-worktrees/core-carry-frozen-holdout-20260915
Base commit: c53f60a8
Goal: 继续模型研究，执行训练不可见的时间留出，确定下一可冻结研究候选或给出不成立证据。
Owner authorization: 2026-09-15 用户当前请求，授权离线训练、验证、研究产物冻结；不授权生产替换。
Write scope: scripts/analysis/reheat_risk/core_carry_candidate_backtest.py; scripts/analysis/reheat_risk/core_carry_model_development.py; tests/test_core_carry_model_development.py; this task/handoff; docs/analysis/CORE_CARRY_RESEARCH_CONTEXT.md; docs/WEATHER_STRATEGY_REGISTRY.md; docs/WEATHER_DOCS_INDEX.md; artifact run=20260915_frozen_holdout.
Non-goals: live/deploy/collector changes, canonical mutations, claims of untouched historical OOS, changes to old artifacts.
Decision: fixed existing candidate shortlist; train before Aug25, validation Aug25-Sep07, lock winner using complete known days at Sep08; refit winner at Sep08 only; Sep08-14 test once. Selection and fit cannot use test features/labels. Preserve pending, fixed common quotes, 50USD/UTC day/10share. Future data unavailable now; create protocol and exact research model identity, not accepted production state.
Acceptance: fit/membership and split evidence; all validation rankings, locked winner test, market/B/buyall baselines, paired target-date intervals, clock/friction/budget diagnostics, changed tickets, perturbation tests, independent code review and handoff. Report negative result without replacement search.
Handoff: tasks/WEATHER/handoffs/WEATHER-CORE-CARRY-HOLDOUT-01.md

Scope clarification: docs/WEATHER_LIVE_RUN_HISTORY_AND_DATA_GOVERNANCE.md adds only this task review correction and quantified impact, as required by global data-governance rule.
Result: O tested weights sealed, statistical conclusion inconclusive, future protocol prepared_not_running. 20 tests pass, whole test artifact parity after review correction.

## 2026-09-15 owner follow-up: detailed review and Notes
Status for follow-up: READY_FOR_REVIEW
User requests full textual report and GPT Pro prompt in Apple Notes, plus detailed entry times/prices/probabilities/expected success/daily tickets/dedup/training-validation/parameter and overfitting/replacement analysis. Authorized read-only inference and post-selection diagnostics, no new fits, no production changes. Extend artifact scope to run=20260915_holdout_detailed_review; extend existing runner/associated tests for reusable audit of frozen results. Preserve prior sealed run and model. New external-review round r03, archive before delivery; sending to GPT Pro is left to user.

Follow-up completed: AppleNotes single note title Core Carry 完整报告与 GPT Pro 审阅提示词｜2026-09-15 r03;78,813characters exactAX bodymatch, archivedprompt/report sameSHA. Packet61hashcheckedmembers,6.1MB. Fivebatcharchiveobjects verified; wholehistoricalarchiveverify45s timeout recorded. No message sent toGPTPro.
