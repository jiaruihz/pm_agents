# Leakage and Negative-Control Audit

- Feature time uses `source_detect_ts_utc`; observation time alone is never treated as availability.
- Official labels use the next routine report with a later report clock and later first-seen clock on the same target date.
- Books are strict receive-time as-of joins; future snapshots and REST gap fills are prohibited.
- Target-date is the dependency/bootstrap block. Rows are not treated as independent messages.
- Deterministic shuffled-time controls use seed `20260827`.
- Source exact-print accuracy materially exceeds the shuffled control in all five cities, but this is a mechanism diagnostic, not an after-cost alpha result.
- Forecast-only and market-only baselines are explicitly unavailable because this frozen boundary lacks PIT forecast identity and full-denominator pre-source two-sided books. They are not imputed.
- Atlanta 2026-07-17 is outside this five-city denominator and remains the mandatory terminal-false source-onboarding control; no claim is made that this run revalidated it.

Result: clock leakage checks pass; executable coverage remains the blocking limitation.

