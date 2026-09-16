# GPT Pro Review Packet — WCIR Stage 3

## Requested review boundary

Review the frozen next-report dataset, lead clocks, reaction coverage, simple baselines, oracle definition, negative controls, and city disposition. Do not authorize Stage 4 or any production/live change.

## Headline evidence

- 841 distinct five-city fast observations, all linked to the next routine official print.
- City coverage is 14–16 target dates, below the pre-registered 20-date minimum.
- Rounded source exact-print rates: Amsterdam 79.3%, Tokyo 66.7%, Helsinki 76.3%, Seoul 84.3%, Busan 79.0%; within-one rates are 97.7–100%.
- These mechanism results do not survive the executable funnel: only one 5-share/30-second one-sided oracle row exists, for Busan, with net markout `-$0.3233`.
- Full two-sided information oracle, market-only same-row baseline, and date-block CI are unavailable and are not imputed.

## Decision

All five cities are `CONTINUE_COLLECTION_ONLY`; the Stage 3 modeling gate fails. No Stage 4, model, selector, threshold, position, execution, or live authorization is requested.

## Reproduce

Run `reviews/wcir_next_print/stage_03/REPRODUCE_STAGE_03.sh`. It verifies frozen hashes, uses a nonexistent runtime root, writes only to a temporary replay directory, byte-compares 11 derived artifacts, and reruns the book contract tests.

## Requested disposition

Choose exactly one:

```text
ACCEPT_STAGE_03_ORACLE_AND_AUTHORIZE_SELECTED_CITIES
ACCEPT_WITH_BLOCKING_FIXES
REWORK_ORACLE_OR_MARKOUT_DEFINITION
CONTINUE_COLLECTION_WITHOUT_MODELING
REDIRECT_AWAY_FROM_PRE_REPORT_ALPHA
```
