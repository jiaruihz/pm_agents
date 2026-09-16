# Legacy City Notes

These files were moved from the old untracked `src/strategies/weather_theta_no_v1/`
directory on 2026-05-15.

They are historical manual research notes only. They are not production
configuration, and they must not be used as the source of truth for live
trading, settlement stations, units, or city eligibility.

Current production truth lives in:

- `/home/rui/projects/weather-predict/pm_edge_compare.py::CITIES`
- `/home/rui/projects/weather-predict/docs/airport-selection-current.md`
- `src/strategies/weather_edge_v1/config/weather_predict_integration.yml`

What changed in this cleanup:

- Early city notes were archived here.
- The duplicate March 13 plan was not migrated because
  `src/strategies/weather_edge_v1/plan/pm_weather_plan_2026-03-13.md` already
  has a richer tracked version with snapshot metadata.
- The old `weather_edge_v1_todo.md` from `weather_theta_no_v1` was not migrated
  because it described an obsolete pre-rename state and referenced paths that
  should no longer be treated as active.
