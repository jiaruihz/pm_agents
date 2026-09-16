# Range RV Forecast Update Cadence v1

> generated_at_utc: `2026-06-25T16:25:14.709850+00:00`
> target_metric: `range_rv_forecast_update_cadence_v1`

## Data Snapshot

- Evidence: weather-predict paper snapshots plus Range RV zero-notional shadow journal.
- snapshot_groups: `164852` from `2510` snapshot files.
- snapshot range: `2026-05-12T15:47:43Z` -> `2026-06-25T15:30:53Z`.
- range_rv_shadow_rows: `7105`.
- `forecast_values_hash` is only available in newer snapshots; older rows fall back to `model_init + forecast_max + peak_time` as state key.

### Mandatory SQL Self-Check

```json
{
  "candidate_coverage": {
    "eligible": 13709,
    "live_filled": 348,
    "paper_ordered": 5440,
    "rows": 38009
  },
  "max_fact_built_at_utc": "2026-06-25T16:17:01.134691+00:00",
  "order_fill_coverage": [
    {
      "orders": 33,
      "status": "error",
      "with_fill": 0
    },
    {
      "orders": 961,
      "status": "submitted",
      "with_fill": 855
    }
  ],
  "settlement_status_distribution": [
    {
      "rows": 150,
      "settlement_status": ""
    },
    {
      "rows": 4250,
      "settlement_status": "settled"
    }
  ],
  "trade_class_distribution": [
    {
      "rows": 855,
      "trade_class": "live_real"
    },
    {
      "rows": 624,
      "trade_class": "live_simulated"
    },
    {
      "rows": 2285,
      "trade_class": "paper"
    },
    {
      "rows": 636,
      "trade_class": "snapshot_replay"
    }
  ]
}
```

## Main Correction

`local hour` is not a forecast information state. For Range RV, the unit to analyze is `city + event_date + forecast_source + model_init/run_age/hash + snapshot_ts_utc`.

The historical Range RV journal did not persist model init, run age, or forecast hash. This report reconstructs them from paper snapshots, which is good enough for audit but not enough for clean forward telemetry; current runner rows should write these fields directly.

## City/Source Cadence Sample

| city | source | groups | dates | state_changes | unique_states | hash_rate | init_top | change_hours_local_top | run_age_p50 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Amsterdam | ecmwf | 3730 | 46 | 474 | 362 | +17.7% | 12Z:243,00Z:231 | 5:73,17:71,9:59,2:51,14:46 | 8.5 |
| Amsterdam | gfs | 9 | 9 | 9 | 9 | +22.2% | 06Z:7,12Z:1,00Z:1 | 15:3,14:2,21:1,12:1,8:1 | 8.0 |
| Ankara | ecmwf | 3554 | 45 | 459 | 303 | +18.4% | 00Z:232,12Z:227 | 11:76,7:73,4:64,19:63,16:57 | 8.5 |
| Ankara | gfs | 9 | 7 | 8 | 8 | +0.0% | 00Z:4,06Z:3,12Z:1 | 12:2,21:1,15:1,17:1,11:1 | 8.1 |
| Atlanta | gfs | 3739 | 44 | 1201 | 802 | +17.4% | 12Z:322,06Z:320,00Z:314,18Z:245 | 17:83,11:78,23:74,5:73,9:72 | 6.5 |
| Austin | gfs | 3755 | 45 | 1210 | 699 | +17.5% | 00Z:336,06Z:325,12Z:315,18Z:234 | 20:86,10:81,22:77,16:76,4:75 | 6.0 |
| Beijing | ecmwf | 3156 | 45 | 408 | 317 | +20.4% | 12Z:211,00Z:197 | 12:72,16:71,9:66,4:43,0:42 | 8.5 |
| Beijing | gfs | 8 | 5 | 7 | 7 | +0.0% | 06Z:5,00Z:2 | 16:2,18:2,21:1,20:1,19:1 | 6.0 |
| BuenosAires | ecmwf | 3738 | 44 | 463 | 247 | +14.9% | 12Z:247,00Z:216 | 1:70,13:69,5:67,17:64,22:56 | 8.5 |
| BuenosAires | gfs | 6 | 5 | 6 | 6 | +66.7% | 06Z:4,12Z:1,00Z:1 | 11:3,13:1,2:1,10:1 | 7.0 |
| Busan | ecmwf | 3105 | 46 | 392 | 275 | +20.4% | 12Z:196,00Z:196 | 13:72,17:71,10:61,5:40,1:36 | 8.5 |
| Busan | gfs | 5 | 4 | 4 | 4 | +40.0% | 06Z:3,12Z:1 | 22:2,4:1,21:1 | 7.0 |
| CapeTown | ecmwf | 3636 | 45 | 461 | 235 | +18.1% | 12Z:231,00Z:230 | 6:71,10:71,18:69,3:62,15:59 | 8.5 |
| CapeTown | gfs | 12 | 7 | 9 | 8 | +25.0% | 06Z:5,18Z:2,00Z:1,12Z:1 | 12:2,14:1,3:1,17:1,6:1 | 6.0 |
| Chengdu | ecmwf | 3220 | 45 | 414 | 268 | +20.7% | 12Z:210,00Z:204 | 16:75,12:73,9:66,4:42,0:41 | 8.5 |
| Chengdu | gfs | 16 | 12 | 16 | 16 | +37.5% | 00Z:7,06Z:6,18Z:2,12Z:1 | 18:2,16:2,17:2,13:2,11:1 | 7.0 |
| Chicago | gfs | 3585 | 45 | 1109 | 821 | +18.6% | 00Z:311,06Z:280,12Z:274,18Z:244 | 20:83,22:78,10:73,16:72,4:71 | 6.0 |
| Chongqing | ecmwf | 3274 | 45 | 411 | 273 | +20.6% | 12Z:208,00Z:203 | 16:74,12:73,9:65,4:42,0:41 | 8.5 |
| Chongqing | gfs | 6 | 5 | 6 | 6 | +0.0% | 00Z:2,06Z:2,12Z:1,18Z:1 | 17:2,18:2,0:1,8:1 | 4.5 |
| Dallas | ecmwf | 3749 | 45 | 464 | 298 | +17.8% | 00Z:238,12Z:226 | 22:70,10:67,2:60,14:51,20:50 | 8.0 |
| Dallas | gfs | 10 | 7 | 10 | 10 | +0.0% | 06Z:5,00Z:3,18Z:2 | 8:2,4:2,19:1,21:1,22:1 | 8.0 |
| Denver | gfs | 3511 | 44 | 1108 | 842 | +19.1% | 00Z:312,06Z:293,12Z:281,18Z:222 | 21:75,9:72,3:71,15:70,1:67 | 6.0 |
| Guangzhou | gfs | 3182 | 45 | 483 | 340 | +20.5% | 00Z:148,18Z:123,06Z:122,12Z:90 | 8:76,12:72,18:61,14:53,0:41 | 5.5 |
| Helsinki | ecmwf | 3730 | 46 | 478 | 282 | +17.9% | 00Z:240,12Z:238 | 6:73,18:70,10:64,3:51,15:50 | 8.5 |
| Helsinki | gfs | 12 | 9 | 12 | 12 | +25.0% | 06Z:5,12Z:3,18Z:2,00Z:2 | 12:2,14:1,16:1,18:1,19:1 | 6.0 |
| HongKong | ecmwf | 3244 | 45 | 410 | 253 | +21.0% | 00Z:211,12Z:199 | 12:71,16:70,9:63,21:53,4:41 | 8.5 |
| HongKong | gfs | 8 | 7 | 8 | 8 | +37.5% | 06Z:3,12Z:2,00Z:2,18Z:1 | 18:2,19:1,0:1,16:1,12:1 | 4.8 |
| Houston | gfs | 3760 | 45 | 1184 | 726 | +17.7% | 00Z:329,06Z:312,12Z:305,18Z:238 | 20:88,10:78,22:77,4:76,16:75 | 6.0 |
| Istanbul | ecmwf | 3673 | 45 | 460 | 300 | +18.2% | 12Z:232,00Z:228 | 7:74,11:73,19:69,4:62,16:56 | 8.5 |
| Istanbul | gfs | 12 | 8 | 11 | 11 | +33.3% | 00Z:5,06Z:5,12Z:1 | 11:2,13:2,10:1,17:1,15:1 | 6.4 |
| Jakarta | ecmwf | 568 | 9 | 82 | 60 | +0.0% | 12Z:41,00Z:41 | 11:15,8:11,15:11,23:8,7:7 | 9.0 |
| Jakarta | gfs | 1 | 1 | 1 | 1 | +0.0% | 18Z:1 | 5:1 | 4.0 |
| Jeddah | ecmwf | 3561 | 45 | 449 | 245 | +18.5% | 00Z:229,12Z:220 | 11:74,7:72,4:62,19:61,16:56 | 8.5 |
| Jeddah | gfs | 4 | 3 | 3 | 3 | +0.0% | 12Z:2,00Z:1 | 0:1,20:1,11:1 | 8.1 |
| Karachi | ecmwf | 3432 | 45 | 445 | 216 | +19.3% | 12Z:223,00Z:222 | 13:75,9:74,6:65,21:57,18:49 | 8.0 |
| Karachi | gfs | 6 | 5 | 6 | 6 | +33.3% | 00Z:4,12Z:2 | 21:2,12:1,9:1,13:1,11:1 | 4.8 |
| KualaLumpur | ecmwf | 3159 | 45 | 405 | 191 | +20.6% | 12Z:206,00Z:199 | 16:73,12:72,9:64,0:42,4:42 | 8.5 |
| KualaLumpur | gfs | 12 | 7 | 10 | 10 | +50.0% | 00Z:4,06Z:3,18Z:2,12Z:1 | 18:2,13:1,16:1,15:1,8:1 | 6.5 |
| LA | gfs | 3692 | 45 | 1189 | 645 | +18.1% | 12Z:331,00Z:329,06Z:313,18Z:216 | 8:83,14:82,20:81,2:76,0:75 | 6.0 |
| Lagos | ecmwf | 244 | 4 | 34 | 32 | +0.0% | 12Z:20,00Z:14 | 17:6,2:5,5:5,9:5,21:5 | 8.5 |
| London | ecmwf | 3774 | 46 | 485 | 361 | +17.4% | 12Z:249,00Z:236 | 16:73,4:73,8:59,13:51,1:50 | 8.5 |
| London | gfs | 7 | 5 | 6 | 6 | +42.9% | 06Z:3,00Z:2,12Z:1 | 13:1,8:1,10:1,14:1,9:1 | 7.0 |
| Lucknow | ecmwf | 3420 | 45 | 432 | 275 | +19.6% | 12Z:217,00Z:215 | 9:73,13:70,21:52,6:50,18:45 | 8.0 |
| Lucknow | gfs | 10 | 10 | 10 | 10 | +30.0% | 00Z:4,06Z:3,12Z:2,18Z:1 | 13:2,19:1,17:1,21:1,2:1 | 5.8 |
| Madrid | ecmwf | 3674 | 45 | 462 | 306 | +15.4% | 12Z:234,00Z:228 | 17:73,5:73,9:59,2:49,14:47 | 8.5 |
| Madrid | gfs | 15 | 9 | 13 | 13 | +40.0% | 00Z:6,06Z:4,18Z:2,12Z:1 | 15:2,9:2,7:2,21:1,5:1 | 7.1 |
| Manila | ecmwf | 1 | 1 | 1 | 1 | +0.0% | 12Z:1 | 0:1 | 4.5 |
| Manila | gfs | 3293 | 45 | 478 | 244 | +20.4% | 00Z:153,18Z:122,06Z:116,12Z:87 | 12:74,8:72,18:66,14:46,0:42 | 5.5 |
| MexicoCity | ecmwf | 3648 | 45 | 458 | 259 | +17.6% | 00Z:235,12Z:223 | 2:72,22:70,10:67,14:64,7:58 | 8.0 |
| MexicoCity | gfs | 6 | 5 | 6 | 6 | +33.3% | 00Z:3,06Z:2,18Z:1 | 0:2,4:2,23:1,19:1 | 5.2 |
| Miami | gfs | 3843 | 45 | 1229 | 671 | +16.8% | 06Z:324,00Z:323,12Z:308,18Z:274 | 17:82,11:80,23:80,5:77,21:76 | 7.0 |
| Milan | ecmwf | 3620 | 45 | 470 | 362 | +15.2% | 12Z:235,00Z:235 | 17:71,5:71,9:62,2:53,14:52 | 8.5 |
| Milan | gfs | 10 | 8 | 9 | 9 | +10.0% | 00Z:4,06Z:3,18Z:1,12Z:1 | 8:2,11:2,9:2,3:1,14:1 | 7.0 |
| Moscow | ecmwf | 3602 | 46 | 462 | 368 | +18.1% | 12Z:231,00Z:231 | 11:72,7:71,19:66,4:66,16:61 | 8.5 |
| Moscow | gfs | 11 | 5 | 9 | 9 | +72.7% | 06Z:4,00Z:3,12Z:1,18Z:1 | 17:2,15:1,11:1,20:1,2:1 | 8.3 |
| Munich | ecmwf | 3670 | 46 | 466 | 355 | +17.7% | 12Z:238,00Z:228 | 5:71,17:68,9:61,2:53,14:46 | 8.5 |
| Munich | gfs | 8 | 6 | 7 | 7 | +25.0% | 06Z:3,12Z:2,00Z:2 | 11:2,10:2,20:1,17:1,13:1 | 5.2 |
| NYC | gfs | 3882 | 45 | 1244 | 942 | +17.4% | 00Z:321,06Z:317,12Z:316,18Z:290 | 11:84,17:81,21:81,23:77,5:75 | 6.5 |
| PanamaCity | gfs | 3674 | 44 | 584 | 346 | +17.9% | 00Z:156,06Z:148,18Z:141,12Z:139 | 23:69,5:67,11:65,17:60,1:43 | 5.5 |
| Paris | gfs | 3740 | 46 | 564 | 438 | +17.9% | 00Z:153,06Z:148,12Z:138,18Z:125 | 17:73,5:72,11:70,1:68,7:51 | 5.5 |
| SanFrancisco | ecmwf | 3650 | 45 | 414 | 258 | +18.3% | 00Z:227,12Z:187 | 20:70,8:69,0:61,5:50,12:49 | 8.0 |
| SanFrancisco | gfs | 6 | 3 | 6 | 6 | +50.0% | 06Z:3,00Z:1,12Z:1,18Z:1 | 2:2,1:1,6:1,9:1,20:1 | 4.5 |
| SaoPaulo | ecmwf | 3693 | 44 | 468 | 282 | +15.0% | 12Z:243,00Z:225 | 1:71,13:69,5:67,10:63,17:60 | 8.0 |
| SaoPaulo | gfs | 2 | 2 | 2 | 2 | +0.0% | 06Z:1,00Z:1 | 9:1,5:1 | 6.0 |
| Seattle | gfs | 3626 | 44 | 1129 | 847 | +18.3% | 06Z:311,12Z:308,00Z:296,18Z:214 | 8:83,14:77,20:76,0:76,6:75 | 6.0 |
| Seoul | ecmwf | 3012 | 46 | 397 | 252 | +20.6% | 12Z:200,00Z:197 | 13:70,17:69,10:64,5:42,1:37 | 8.0 |
| Seoul | gfs | 4 | 4 | 4 | 4 | +0.0% | 18Z:2,06Z:1,00Z:1 | 20:1,12:1,16:1,8:1 | 7.5 |
| Shanghai | gfs | 3090 | 45 | 460 | 309 | +20.9% | 00Z:148,18Z:121,06Z:104,12Z:87 | 12:71,8:67,18:53,0:41,13:38 | 5.5 |
| Shenzhen | ecmwf | 3248 | 45 | 416 | 231 | +20.5% | 12Z:209,00Z:207 | 16:72,12:71,9:66,21:47,4:43 | 8.0 |
| Shenzhen | gfs | 9 | 7 | 7 | 7 | +55.6% | 06Z:5,12Z:1,00Z:1 | 19:2,18:2,0:1,20:1,14:1 | 5.5 |
| Singapore | gfs | 3078 | 44 | 471 | 193 | +17.6% | 00Z:148,18Z:119,06Z:118,12Z:86 | 12:72,8:64,18:62,0:40,6:37 | 5.5 |
| Taipei | gfs | 3098 | 45 | 460 | 348 | +20.6% | 00Z:150,18Z:119,06Z:103,12Z:88 | 12:69,8:60,18:57,0:40,13:39 | 5.5 |
| TelAviv | gfs | 3509 | 45 | 505 | 317 | +18.4% | 00Z:139,06Z:139,18Z:118,12Z:109 | 6:72,2:70,12:69,18:60,0:43 | 5.5 |
| Tokyo | gfs | 3018 | 46 | 444 | 325 | +21.3% | 00Z:142,18Z:118,06Z:101,12Z:83 | 13:69,9:59,19:46,14:39,1:37 | 5.5 |
| Warsaw | ecmwf | 3714 | 46 | 483 | 322 | +17.9% | 12Z:246,00Z:237 | 17:72,5:72,9:59,2:56,14:54 | 8.5 |
| Warsaw | gfs | 4 | 4 | 4 | 4 | +50.0% | 18Z:3,00Z:1 | 4:2,23:1,10:1 | 8.2 |
| Wellington | gfs | 2963 | 46 | 402 | 234 | +22.6% | 00Z:135,18Z:111,12Z:79,06Z:77 | 16:71,12:56,10:37,4:36,22:35 | 5.5 |
| Wuhan | ecmwf | 3081 | 44 | 400 | 250 | +18.0% | 12Z:203,00Z:197 | 12:71,16:70,9:66,4:43,21:41 | 8.0 |
| Wuhan | gfs | 6 | 5 | 6 | 5 | +33.3% | 06Z:4,18Z:1,00Z:1 | 20:1,19:1,11:1,18:1,13:1 | 5.2 |

Full CSV: `docs/analysis/2026-06/generated/range_rv_forecast_update_cadence_v1/city_source_cadence.csv`.

## Range RV Re-read

Policies below are exploratory re-reads using reconstructed forecast metadata. `prev_day_latest` means latest candidate before the city-local target date; `d0_morning_latest` means latest target-date candidate from local 06:00-11:59.

| policy | rows | dates | cities | cost | pnl | roi | hit | avg_state_age_min |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| prev_day_latest | 159 | 11 | 17 | 103.338 | +2.662 | +2.6% | +66.7% | 135.0 |
| d0_morning_latest | 69 | 6 | 17 | 48.195 | +3.805 | +7.9% | +75.4% | 126.1 |

## Source/Init Breakdown

| policy|source|init | rows | dates | cities | cost | pnl | roi | hit | avg_state_age_min |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| prev_day_latest|open_meteo_live_ecmwf|00Z | 44 | 10 | 8 | 26.698 | +1.302 | +4.9% | +63.6% | 157.9 |
| prev_day_latest|open_meteo_live_gfs|18Z | 31 | 10 | 9 | 21.767 | +3.233 | +14.9% | +80.6% | 86.3 |
| prev_day_latest|open_meteo_live_ecmwf|12Z | 30 | 9 | 8 | 17.582 | +0.418 | +2.4% | +60.0% | 180.8 |
| d0_morning_latest|open_meteo_live_ecmwf|00Z | 27 | 6 | 7 | 17.514 | +0.486 | +2.8% | +66.7% | 161.5 |
| prev_day_latest|open_meteo_live_gfs|06Z | 24 | 9 | 9 | 16.213 | -1.213 | -7.5% | +62.5% | 168.4 |
| prev_day_latest|open_meteo_live_gfs|00Z | 20 | 9 | 9 | 12.557 | -0.557 | -4.4% | +60.0% | 78.6 |
| d0_morning_latest|open_meteo_live_gfs|18Z | 18 | 5 | 5 | 12.615 | +0.385 | +3.1% | +72.2% | 157.6 |
| d0_morning_latest|open_meteo_live_gfs|06Z | 13 | 6 | 3 | 10.019 | +0.981 | +9.8% | +84.6% | 26.9 |
| prev_day_latest|open_meteo_live_gfs|12Z | 10 | 5 | 5 | 8.521 | -0.521 | -6.1% | +80.0% | 36.1 |
| d0_morning_latest|open_meteo_live_gfs|12Z | 5 | 5 | 2 | 4.272 | +0.728 | +17.0% | +100.0% | 18.2 |
| d0_morning_latest|open_meteo_live_ecmwf|12Z | 4 | 4 | 2 | 2.390 | +1.610 | +67.4% | +100.0% | 262.6 |
| d0_morning_latest|open_meteo_live_gfs|00Z | 2 | 2 | 2 | 1.385 | -0.385 | -27.8% | +50.0% | 0.0 |

## Interpretation

- The previous `latest_before_local_18` framing is too coarse and should not be used as a candidate label.
- D0 morning can be a valid research branch, but only as `forecast-update Range RV`, not as prev-day Range RV.
- The next selection variable should be forecast-state arrival and market repricing lag: source/init/run_age/hash-change, not just local clock hour.
- Regime/METAR layers should stay separate: they can calibrate or size forecast-update trades, but they are not the same as forecast distribution generation.

## Required Runtime Fix

`range_rv_shadow_v0` must persist these fields before future forward shadow can be treated as clean evidence: `model_init_utc_estimated`, `model_run_age_hours_estimated`, `forecast_values_hash`, `forecast_state_changed`, `forecast_state_first_seen_utc`, and `forecast_state_age_minutes`. This is implemented for new runner rows; historical rows in this audit are reconstructed from snapshots.

significance=NA, baseline=NA, forward=NA, conclusion=diagnostic_only
