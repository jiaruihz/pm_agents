# HK / Shenzhen / Moscow Settlement Source Reroute v0

Status: snapshot
Updated: 2026-06-18
Source of truth: no
Used by: source profile follow-up; weather latency arb source eligibility

## Target metric

`settlement_source_reconciliation` = whether the public source named in current Polymarket rules can reproduce `pm_history` winners at city-day grain.

This is not a PnL analysis and does not change N100/live behavior.

## Current rule text checked

Pulled from Gamma event descriptions for June 17-19, 2026:

| City | Current rule source | Rule precision | Initial verdict |
|---|---|---|---|
| HongKong | HKO Daily Extract, field `Absolute Daily Max (deg. C)` | one decimal C | confirmed special source |
| Shenzhen | Wunderground history page for `ZGSZ` | whole-degree C | still problematic; WU backend maps to Lau Fau Shan |
| Moscow | NOAA/weather.gov WRH timeseries, `site=UUWW` | whole-degree C | fixable; use Synoptic/weather.gov backend |

## HongKong

Current rules continue to point to the Hong Kong Observatory, not VHHH METAR:

```text
https://www.weather.gov.hk/en/cis/climat.htm
```

The relevant API endpoints are:

```text
https://data.weather.gov.hk/weatherAPI/opendata/opendata.php?dataType=CLMMAXT&station=HKO&rformat=json&year=2026
https://www.hko.gov.hk/cis/dailyExtract/dailyExtract_YYYYMM.xml
https://data.weather.gov.hk/weatherAPI/opendata/weather.php?dataType=rhrread&lang=en
```

Evidence:

- Existing batch2 alignment: `hko_extract_floor` = 27/27, 100%.
- `CLMMAXT` confirms field name `Daily Maximum Temperature (°C) at the Hong Kong Observatory`.
- `dailyExtract_202606.xml` is JSON despite the suffix and contains `dayData` with max temperature.
- `rhrread` has current station temperatures, including `Hong Kong Observatory`, but does not directly expose the finalized daily absolute max.

Verdict:

- HK settlement source is solved: `HKO Daily Extract Absolute Daily Max`, floor-to-bracket mapping.
- HK is not a METAR city. Do not use VHHH/IEM/WU as payout feature source.
- For latency arb, HK needs a dedicated HKO live adapter that tracks HKO station current/running max and then proves it matches finalized Daily Extract. Until then, keep `live_eligible=false`.

## Moscow

Current rules point to:

```text
https://www.weather.gov/wrh/timeseries?site=UUWW
```

The WRH page loads:

```text
https://www.weather.gov/source/wrh/apiKey.js
https://www.weather.gov/source/wrh/timeseries/obs.js
```

`apiKey.js` exposes the weather.gov site token for Synoptic Data, and `obs.js` calls:

```text
https://api.synopticdata.com/v2/stations/timeseries?STID=UUWW&...
```

Important implementation detail: the Synoptic API rejects direct calls unless the request includes a weather.gov-style `Referer`/`Origin`.

Fresh alignment test:

- Source: Synoptic API timeseries for `UUWW`, local timezone `Europe/Moscow`.
- Date span: 2026-05-01 through 2026-06-18.
- Settlement labels: local `runtime/weather_edge_v1/market_data/cache/pm_history/Moscow_*.json`.
- Result: 34/34 matched using Synoptic `air_temp_set_1` daily max.

Verdict:

- Moscow should no longer be treated as unknown-effective-source.
- Proposed registry update: `settlement_source_class=non_wu_source_by_rules`, `official_station_or_feed=https://www.weather.gov/wrh/timeseries?site=UUWW`, `primary_source=synopticdata_timeseries`, `live_eligible=false` until a runtime client and latency monitor exist.
- Moscow may be eligible for source research after implementing the Synoptic/weather.gov adapter; it is not yet a live speed candidate.

## Shenzhen

Current rules still say Wunderground for Shenzhen Bao'an / `ZGSZ`:

```text
https://www.wunderground.com/history/daily/cn/shenzhen/ZGSZ
```

But the backend behind the WU history API previously resolved:

```text
location: ZGSZ:9:CN
obs_name: Lau Fau Shan
key: 45035
```

Evidence:

- Existing batch2 alignment:
  - IEM/Synoptic/METAR `ZGSZ`: very poor; fresh Synoptic test gave 4/35.
  - WU backend `ZGSZ:9:CN` -> Lau Fau Shan: 20/28, 71.4%.
- WU feed mismatches are concentrated before 2026-05-25. From 2026-05-25 to 2026-06-09, cached WU backend rows match 16/16.
- Fresh WU API calls currently return `403 Bad Request - Blocked`, so 2026-06-10 onward cannot be refreshed from this machine without a browser/API workaround.

Verdict:

- Shenzhen remains unresolved, but the failure mode is more specific now:
  WU rule text says ZGSZ, WU backend maps to Lau Fau Shan, and recent cached data may have stabilized.
- Do not admit Shenzhen to live candidate yet.
- Next research step is to recover WU backend access or use Browser/Chrome to capture the current WU history payload, then rerun 2026-06-10 onward. If the post-2026-05-25 perfect streak persists over more settled days, Shenzhen can move from `blocked_unresolved` to `default_source_watchlist`, not directly to live.

## Action table

| City | New status | Next code/data action |
|---|---|---|
| HongKong | confirmed special source | Build HKO live adapter; keep banned from METAR-cross |
| Moscow | source identified/fixable | Add Synoptic/weather.gov client and update source profile from blocked to non-WU source |
| Shenzhen | unresolved but narrowed | Refresh WU backend data; verify whether post-2026-05-25 alignment persists |

## Commands used

Focused validation only:

```bash
.venv/bin/python - <<'PY'
# Synoptic UUWW vs pm_history: 34/34 matched
PY
```

## References

- HKO Daily Extract landing page: https://www.weather.gov.hk/en/cis/climat.htm
- HKO CLMMAXT API: https://data.weather.gov.hk/weatherAPI/opendata/opendata.php?dataType=CLMMAXT&station=HKO&rformat=json&year=2026
- HKO current weather API: https://data.weather.gov.hk/weatherAPI/opendata/weather.php?dataType=rhrread&lang=en
- NOAA WRH timeseries: https://www.weather.gov/wrh/timeseries?site=UUWW
- Wunderground Shenzhen rule URL: https://www.wunderground.com/history/daily/cn/shenzhen/ZGSZ
