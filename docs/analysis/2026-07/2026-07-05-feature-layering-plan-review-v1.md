# Weather Feature Layering Plan Review v1

Status: current-reference
Updated: 2026-07-05
Source of truth: no; review evidence for `WEATHER_FEATURE_LAYERING_PLAN.md`
Superseded by / Used by: WEATHER_ARCHITECTURE_SPINE.md; WEATHER_FEATURE_LAYERING_PLAN.md; WEATHER_DOCS_INDEX.md

## Verdict

`docs/WEATHER_FEATURE_LAYERING_PLAN.md` is directionally useful, but it should
not be executed as written. The key correction is: shared assets should be
centralized by **contract and versioned semantics**, not by forcing similar code
into one undifferentiated implementation.

This pass found one concrete stale/misread item and landed one safe subtask:
`CITY_FAMILY` is now centralized in `weather_data_feed.city_family`, but as two
named maps because current code had a real taxonomy fork (`Beijing` differs).

## P1-P12 Review

| Item | Verdict | Evidence / correction |
|---|---|---|
| P1 shared fact layer in research dir + docs as data store | Correct | `scripts/analysis/reheat_risk/research_reheat_feature_factory_v1.py` still writes `OUT_DIR = docs/analysis/2026-06/generated/reheat_feature_factory_v1` and reads shared DB/orderbook inputs. `rg -l` found 586 files mentioning `docs/analysis/**/generated` and 129 mentioning `reheat_feature_rows.csv`, so the dependency surface is real. |
| P2 dated patch inputs | Correct, but scope grew | The factory still points at `m3_observed_max_v6_h10_21_iem_patch_20260617`, `theta_no_wu_obs_patch_v1`, and `theta_no_iem_ext_patch_v7_20260617`. There are newer WU patch dirs (`v10`-`v15`), so canonicalization should first define the current source policy, not just rename the old three paths. |
| P3 two feature factories | Correct | `docs/analysis/2026-06/generated/current_bracket_no_pass_through_v1_feature_factory/` still exists beside `reheat_feature_factory_v1`. Convergence is plausible but must replay pass-through rows before switching. |
| P4 research scripts imported as libraries | Correct, with priority narrowing | Many `research_*` imports exist. This is tolerable inside research chains, but live/shadow runners importing them is the structural risk to fix first. |
| P5 live/shadow runner imports research scripts | Correct, with nuance | `tmax_distribution_edge_live_candidate_v1.py` imports atlas + P0-P4 research modules directly. `regime_routed_no_shadow_v1.py` imports `research_regime_routed_no_expression_v1` directly. `regime_routed_no_tiny_live.py` imports `regime_routed_no_stable`, but that stable adapter still imports research modules internally, so it is a boundary shim, not a fully independent stable implementation. It also imports `weather_metar_cross_prev_no_shadow` for METAR helpers. |
| P6 `CITY_FAMILY` hardcoded in 6 scripts | Partly correct; plan needed adjustment | Current code had 5 hardcoded definitions, not 6. Four were identical; atlas differed only on `Beijing` (`east_asia_continental` vs `continental_dry_hot`). Forced merge would change behavior. Landed adjusted fix: `CITY_FAMILY_CURRENT_BRACKET_NO_V1` and `CITY_FAMILY_ATLAS_V1` in `weather_data_feed.city_family`; scripts import the matching map. |
| P7 METAR / `SKY_CODE` duplication | Partly correct | `SKY_CODE` had 5 identical hardcoded definitions and was safely centralized in this pass. METAR parsing/fresh observation helpers are duplicated more broadly, but some live code also handles cache/source-events and latency-specific behavior. Parser centralization still needs API-level parity tests per caller. |
| P8 directory identity mismatch | Correct | Atlas and temperature context scripts still live under `reheat_risk/` despite being shared mechanism features. Moving code is lower priority than making live runners consume stable shared APIs. |
| P9 docs intent vs code reality | Correct | `WEATHER_TEMPERATURE_CONTEXT_FEATURE_LAYER.md` describes the factory as a shared temperature-state layer, while code path/name still says reheat. `WEATHER_ARCHITECTURE_SPINE.md` remained `design-draft` before this pass. |
| P10 known data debt | Partly stale | The old "forecast peak backfill is PIT-polluted" warning is no longer fully accurate after Single Runs PIT backfill rejoin. Current factory text says it consumes `forecast_peak_clock_backfill_v1.csv`, closer to PIT but still a backfilled research layer when native `fact_signal_candidates` peak fields are missing. Treat as not production-native, not as the old unqualified pollution claim. |
| P11 execution styles duplicated | Correct high-level; overstates parameterization | The five styles exist. But lottery maker lifecycle and TP20 exit are not interchangeable by signature: lottery is BUY entry with reprice/downshift/taker fallback edge checks; TP20 is SELL exit over wallet positions, cancel index, and disabled/draft behavior. Extract pure components, not one monolithic state machine. |
| P12 direct CLOB channels | Partly wrong | `weather_metar_cross_prev_no_shadow.py` and `all_yes_underround_fok_executor_v0.py` build their own `ClobClient`. `weather_station_basis_exec.py` does **not**: live placement is intentionally unimplemented and hard-gated. Do not count station-basis as an active parallel live CLOB client. |

## Execution Shape Review

The plan's `execution_style + style_params` contract is the right direction,
but Phase E should be split into:

1. Contract-only docs/schema for style metadata.
2. Pure helpers that are obviously behavior-preserving: maker price, book
   levels, top-ask clamp, fresh-book cushion checks.
3. Per-runner state adapters with parity replay.
4. Direct CLOB fast-path consolidation only after explicit user confirmation and
   latency measurement.

`maker_then_taker` cannot be declared a universal state machine yet. It needs
separate role semantics for BUY entry, SELL exit, and FOK latency lines.

## Work Landed In This Pass

Phase B-4 and the `SKY_CODE` subpart of Phase B-5 were executed with adjusted
contracts:

- Added `weather_data_feed/city_family.py`.
- Updated the 5 hardcoded research scripts to import the proper named map.
- Preserved the atlas-specific `Beijing = continental_dry_hot` fork and the
  current-bracket/no-reheat `Beijing = east_asia_continental` fork.
- Added `tests/pmm_tests/test_weather_city_family.py`.
- Added `weather_data_feed/sky_cover.py`.
- Updated the 5 identical `SKY_CODE` dictionary definitions to import
  `SKY_COVER_CODE` while preserving each script's public `SKY_CODE` alias.
- Added `tests/pmm_tests/test_weather_sky_cover.py`.

Validation:

- Static AST scan: no remaining top-level `CITY_FAMILY = {...}` definitions
  under `scripts/**/*.py`.
- Import parity: 4 current-bracket/no-reheat scripts report 36 cities and
  `Beijing=east_asia_continental`; atlas reports 36 cities and
  `Beijing=continental_dry_hot`.
- `pytest tests/pmm_tests/test_weather_city_family.py` passed.
- Static AST scan: no remaining top-level dict-valued `SKY_CODE = {...}`
  definitions under `scripts/**/*.py` or `src/**/*.py`.
- Import parity: live theta runner, factory, M3, validation, and theta-NO
  selector all report 10 sky codes with `FEW=1`, `OVC=4`.
- `pytest tests/pmm_tests/test_weather_sky_cover.py` passed.
- `pytest tests/pmm_tests/test_theta_current_yes_live_guards.py -q` passed.

## Do Not Execute As Written

- Do not collapse `CITY_FAMILY` into one unlabeled map.
- Do not mark station-basis as an active direct live CLOB client.
- Do not treat `SKY_CODE` centralization as full METAR parser unification;
  parser/fetch path changes still need caller-level parity and latency checks.
- Do not switch metar-cross fast path into a generic executor path without
  measuring latency and preserving its live confirmation/notional controls.
- Do not move the factory output path until a real parity rebuild is defined;
  the current `docs/generated` dependency surface is large enough that a stub
  cutover would break research/ops consumers.
