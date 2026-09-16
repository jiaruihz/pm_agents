# Pre-Predict Analysis Module

Owner doc: `docs/analysis/pre_predict.md`

This module is for forecast-prior research before the intraday observed path is
the main source of edge.

## Contract

Use this module when the target metric is based on forecast distribution before
or without observed running-max information:

- raw bracket probability
- blended probability
- forecast quality / reliability
- forecast-bounded range
- adjacent/range basket
- low-price YES prior

Do not use this module for current YES no-reheat or higher NO carry research;
those belong to `scripts/analysis/reheat_risk/`.

## Suggested Script Names

```text
research_pre_predict_calibration_v*.py
research_forecast_bounded_range_v*.py
research_pre_predict_low_price_yes_prior_v*.py
research_source_aware_forecast_quality_v*.py
```

## Current State

No maintained scripts were migrated into this module during the 2026-06-16
structure cleanup. Existing forecast-quality, Range RV, and low-price YES prior
reports stay indexed through their living docs until a script is intentionally
promoted here.

## Shared Row Grain

Preferred grains:

- city-date-model forecast distribution
- bracket quote within city-date-model
- range/basket candidate within city-date-model

Reports should not mix these grains without an explicit bridge table.
