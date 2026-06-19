"""Versioned copy of the legacy weather-predict collection runners.

These modules are intentionally kept close to the production weather-predict
scripts for the first migration phase. The service wrapper supplies independent
runtime output/cache roots so the new timers can run in parallel.
"""

