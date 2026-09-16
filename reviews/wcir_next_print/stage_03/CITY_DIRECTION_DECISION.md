# City Direction Decision

## Decision

All five cities are `CONTINUE_COLLECTION_ONLY`. None is authorized for model construction, strategy selection, threshold tuning, position policy, shadow promotion, or live use.

| City | linked dates | linked events | executable 5-share/30s dates | decision |
|---|---:|---:|---:|---|
| Amsterdam | 16 | 87 | 0 | CONTINUE_COLLECTION_ONLY |
| Tokyo | 14 | 42 | 0 | CONTINUE_COLLECTION_ONLY |
| Helsinki | 15 | 59 | 0 | CONTINUE_COLLECTION_ONLY |
| Seoul | 14 | 268 | 0 | CONTINUE_COLLECTION_ONLY |
| Busan | 16 | 385 | 1 | CONTINUE_COLLECTION_ONLY |

The pre-registered modeling gate requires at least 20 target dates, 100 eligible executable events, a positive 5-share feasible oracle with one-sided 90% date-block CI lower bound above zero, limited date concentration, and reaction that is not mostly pre-source. No city reaches even the date requirement. Busan's sole executable 5-share/30-second row is negative.

Disposition requested from the reviewer: `CONTINUE_COLLECTION_WITHOUT_MODELING`.

