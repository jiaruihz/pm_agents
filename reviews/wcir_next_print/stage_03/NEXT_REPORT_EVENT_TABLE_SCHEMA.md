# Next Report Event Table Schema

Grain is one distinct fast-source observation (`event_id`) linked to the next routine official print for the same city and target date.

| Group | Required fields |
|---|---|
| identity | `event_id`, `event_key`, city, target_date, source, station |
| fast-source clocks/value | source observation, detect, temperature and native rounded temperature |
| prior official state | latest official report/detect/value and running maximum |
| next official label | report timestamp, first-seen timestamp, value, rounded value, linkage status |
| market identity | market_id, condition_id, token_id, exact prior bracket |
| book evidence | event checkpoint alignment IDs, `book_valid`, `gap_reason`, fee-aware sweeps |
| labels | exact/within-one source accuracy, persistence accuracy, next-print crossing label |
| governance | frozen source identity, exclusion reason, production-health boundary |

Feature clocks stop at the event checkpoint. Next official values, later books, and settlement are labels only. The frozen dataset contains 841 rows from 2026-08-09 through 2026-08-26.

