# Manual Airport Research Archive

Last updated: 2026-05-11

This directory is historical manual research. It is not the production city,
airport, or model-selection source for `weather_edge_v1`.

Production source of truth:

- `/home/rui/projects/weather-predict/pm_edge_compare.py::CITIES`
- `/home/rui/projects/weather-predict/docs/airport-selection-current.md`
- `/home/rui/projects/weather-predict/REPORT.md`
- `/home/rui/projects/weather-predict/CLAUDE.md`

Why this was archived:

- The active paper workflow runs in `weather-predict`.
- Current city selection is based on forecast accuracy, calibration, and replay
  research in `weather-predict`.
- Several archived station choices conflict with the production mapping:
  - Chicago: archived `KORD`, production `KMDW`
  - London: archived `EGLC`, production `EGLL`
  - Production cities missing from archived station profiles include Phoenix,
    Austin, Boston, LA, Warsaw, and Beijing.

What remains useful:

- airport microclimate notes
- wrong-anchor warnings
- source probing examples
- older manual case logs

How to use it:

- Use it only as qualitative research material.
- Do not use it to decide the production city universe, ICAO station, unit,
  model selector, or live-trading eligibility.
- If a note here is still useful, migrate it into `weather-predict` docs and
  reconcile it against the current production `CITIES` mapping first.
