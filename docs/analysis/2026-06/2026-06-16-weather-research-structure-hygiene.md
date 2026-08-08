# Weather Research Structure Hygiene

Status: snapshot
Created: 2026-06-16

## Scope

This cleanup only aligned weather research docs, script ownership, and index
references. It did not run alpha searches, change N100/live behavior, rewrite
historical conclusions, or rename historical report files.

## Target Structure

Current branch owners:

- `docs/analysis/pre_predict.md` + `scripts/analysis/pre_predict/`: forecast-prior / pre-path research.
- `docs/analysis/reheat_risk.md` + `scripts/analysis/reheat_risk/`: intraday observed-path / no-reheat / reheating-risk research.
- `scripts/analysis/observed_max/`: historical observed-max and station-basis-adjacent scripts unless explicitly promoted.

## Moved

Moved maintained reheat-risk script entrypoints from `scripts/analysis/observed_max/`
to `scripts/analysis/reheat_risk/`:

- `research_m3_exhaustion_source_aware_restart_v1.py`
- `research_m3_jump_model_v1.py`
- `research_m3_jump_model_v2_quote_calibration.py`
- `research_m3_jump_model_v3_bad_case_attribution.py`
- `research_theta_*.py`

Updated current report script references from the old `observed_max` path to
the new `reheat_risk` path. Two migrated scripts still use old observed-max
helper functions, so they now add the legacy helper directory explicitly instead
of silently relying on the old working directory.

## Indexed-Only

- `pre_predict` has a README and owner doc, but no maintained script was moved
  there in this pass.
- `weather_strategy_research_whitepaper.md`, `reheat_risk.md`,
  `pre_predict.md`, and `reheat_risk_delegation_prompts.md` already pointed to
  the intended two-branch structure and were not rewritten.

## Archival-Kept

Kept historical filenames and older observed-max scripts in place when moving
them would only rename history:

- early `m3_observed_max`, paper-snapshot proxy, orderbook best-ask, settlement
  alignment, station-basis, maker, official-source, and lookahead-check scripts.
- `docs/archive/analysis/observed_max_reheat_risk_legacy.md` remains a legacy
  handoff snapshot, not the current owner.

## Still Dirty

- `scripts/analysis/observed_max/` still mixes several historical families:
  early M3 no-reheat, station-basis, settlement-source audit, and helper code.
  This is intentional for now; a later cleanup should split station-basis only
  if it becomes confusing enough to justify a new owner module.
- Some generated Markdown/JSON reports retain historical names like `m3` or
  `theta`. Those names are snapshot evidence and were left intact to avoid
  breaking links.
- Existing unrelated dirty files in the worktree were not touched by this
  cleanup.

## Validation

- Verified no current docs/scripts still reference the migrated theta,
  M3 jump-model, or M3 source-aware restart scripts through the old observed-max
  directory.
- Ran Python compile checks on the migrated reheat-risk scripts.
