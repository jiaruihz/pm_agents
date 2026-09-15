# WEATHER-CORE-CARRY-HOLDOUT-01 handoff
Status: READY_FOR_REVIEW; owner acceptance not granted.
Repo: /Users/deepsleep/projects/pm_agents
Worktree: /Users/deepsleep/projects/pm_agents-worktrees/core-carry-frozen-holdout-20260915
Branch: research/core-carry-frozen-holdout-20260915
Base: c53f60a8
Artifact: /Volumes/jrs-archive/pm_agents/research/artifact_store/core_carry/run=20260915_frozen_holdout
Report: HOLDOUT_REVIEW.md

## Claim / evidence
- Implemented train/validation/test time separation with one frozen test fit: existing family CLI --frozen-holdout; PLAN, selection lock, fits and membership parquet. Five candidates beforeAug25; validationAug25-Sep07 selects using six complete knownUTC days; one O model refit preSep08 (maxdateSep06), scoredSep08-14 without updates.
- O selected and exact tested research weights sealed: FROZEN_CANDIDATE.json, modelSHA5a54e8aaff31ba475431d7bc9fe5e47bd1c790198ea0bcc1e0df356f0eb9f75c. Final6fits plus superseded6fits retained; total12 actualfits this task.
- Main simulated35tickets34win1loss +7.5139326 versus B13tickets -1.4005; same50USD UTC budget, different actual invested capital. Evidence test/ledger.parquet and summary. No actualfills/production writes.
- Statistical inference inconclusive: seven dates, pairedCI crosses0, strict-clock/friction pending bounds can turn negative; full-domain probability worse than market despite carry improvement. Evidence paired_intervals, probability_metrics, sensitivity_summary and terminal_pnl_bounds. No replacement search after test.
- Historical researcher exposure preserved: original history already reviewed; no clean OOS claim. FORWARD_PROTOCOL prepared_not_running, no new O actualforward collected, no deployment or accepted-state change.
- One independent readonly review: twoP2 fixed (incomplete firstUTC validation day, configbudget consistency). checks/CODE_REVIEW.md and CORRECTION_IMPACT.json quantify32 excluded cross-model selection records; winner unchanged; finalmodelSHA/1043testpredictions/ledger/memberships/summary exactparity. Wrong/missed liveorders0, labelchanges0. Reviewer actualmodel/effort/usage unavailable.
- Validation:20tests pass, recordvalidator pass, gitdiffcheck pass. Weatherdocs7 preexisting errors exactly same before/after. Projectstructure2 existing reviewdebt errors+3skillwarnings, reviews/skills untouched.

## Original request reconciliation
1. Continue model research: implemented and ran five existing candidates under stricter nested temporal isolation.
2. Data unseen by training: all train-only preprocessing, date+receipt barriers, selection lock, single final test fit and perturbation/orchestration regressions delivered; researcher-exposed history explicitly distinguished.
3. Find next freeze: O tested weights sealed as next research validation object; efficacy/fullrobustness not yet proven. FREEZE_ASSESSMENT separates artifact freeze from scientific acceptance.
4. Durable evidence: report, rawattempt preservation, allrankings/changes, futureprotocol, seal; livingcontext/registry/index/governancerecord updated only in owned branch.

## Remaining decision boundary
Production upgrade not authorized or proposed on these seven days. Future independent O collection requires actual integration and recorded prediction start; current protocol alone is not forward evidence. Current offline task delivered; do not silently deploy or self-accept.

## Owner follow-up r03 detailed review
0fits; audit-only runner and24tests; expected/observed probabilities/PnL, all48O/B test tickets, both daily clocks, duplicate checks, trainresubstitution/validation/test, selection-label-delay and single-date sensitivity delivered. Full14day validationW>O; O-B excludingSep14 negative. CannotreplaceB/overfitunresolved; fixedO remains unchanged. Artifact run=20260915_holdout_detailed_review; reviewr03 prepared_not_sent, AppleNotes fulltext/prompt export andarchive tracked in artifact receipts. Single review2P2fixed,11audit tables parity.

Notes delivery verified:78,813characters exactAX match and finalmarker; sourceSHAab081cbb3f7613e584af160b3dc1e4eff0b1048dd040b177c59150edc75bb882. R03reviewZIP61checkedmembers6,106,979bytes. Archive5objects match; wholearchive45stimeout recorded. NoGPTProsend. See NOTES_VERIFICATION and ARCHIVE_VERIFICATION inartifact.
