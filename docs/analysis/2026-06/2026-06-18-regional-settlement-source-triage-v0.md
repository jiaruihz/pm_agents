# Regional Settlement Source Triage v0

Status: snapshot
Updated: 2026-06-18
Source of truth: no
Used by: weather latency arb source eligibility

## Target metric

`regional_settlement_source_triage` = for each active highest-temperature city family, decide whether the settlement source is already clear enough for METAR/source-speed monitoring, or whether it needs a deeper source reconstruction before any latency-arb shadow/live path.

This is a source research note only. It does not change N100 behavior or `source_profiles.json`.

## Inputs Checked

- Runtime source profile: `src/strategies/weather_edge_v1/official_observation_feed/source_profiles.json`.
- Current Gamma rule descriptions fetched for June 18-20, 2026 where slugs existed.
- Prior registry: `docs/analysis/2026-06/2026-06-14-settlement-source-registry-v0.md`.
- Follow-up: `docs/analysis/2026-06/2026-06-18-hk-shenzhen-moscow-settlement-source-reroute-v0.md`.

Current Gamma pull found 64 event descriptions across June 18-19 for 32 cities. Most current Asia/Europe markets still cite Wunderground station history pages. No broad Americas slate appeared for those dates via direct slug probing, so Americas conclusions below lean more on the June 10 source registry and should be refreshed when US/LATAM markets reopen.

## Current Rule Source Sample

| Region | Current examples found | Rule source pattern | Immediate read |
|---|---|---|---|
| Europe | Amsterdam, Ankara, Helsinki, London, Madrid, Milan, Moscow, Munich, Paris, Warsaw | Mostly Wunderground ICAO pages; Moscow uses NOAA/weather.gov WRH | Europe is mostly source-clear. Moscow is non-WU and needs adapter; London/Milan/Paris are station-diff but validated historically. |
| Asia | Beijing, Busan, Chengdu, Guangzhou, HK, Istanbul, Jeddah, Karachi, KualaLumpur, Manila, Seoul, Shanghai, Shenzhen, Singapore, Taipei, TelAviv, Tokyo, Wuhan | Mostly Wunderground ICAO pages; HK uses HKO; Istanbul/TelAviv use NOAA/weather.gov WRH | Asia has the most unresolved/special cases. Shanghai/Tokyo remain cleanest latency candidates; HK/Shenzhen/Seoul need deeper source work. |
| Americas | Atlanta, Austin, BuenosAires, Chicago, Dallas, Denver, Houston, LA, MexicoCity, Miami, NYC, PanamaCity, SanFrancisco, SaoPaulo, Seattle from prior registry | Mostly Wunderground ICAO pages; Chicago/Panama are station-diff; MexicoCity watchlist; Boston/Minneapolis/Phoenix no recent source | Americas are not the main settlement-source blocker, but US cities may be best for faster ASOS/MADIS/SPECI source research when markets are open. |

## Priority Buckets

### P0: Do not use for latency arb until source is solved

| City | Region | Current status | Why |
|---|---|---|---|
| Shenzhen | Asia | unresolved WU backend | Rule says WU `ZGSZ`, but WU backend/page behavior previously mapped through Lau Fau Shan / PWS-like paths. METAR `ZGSZ` alignment was poor. Needs real browser network capture of WU daily observations. |
| Seoul | Asia | unresolved WU/RKSI mismatch | Current rules still say WU `RKSI`, but historical WU/METAR alignment was only 28/36. Need mismatch-day reconstruction from rendered WU final daily rows before admitting. |
| HongKong | Asia | special HKO source | Settlement is HKO Daily Extract, not VHHH/METAR. Needs HKO live/running-max adapter and mapping to finalized Daily Extract. |
| Boston, Minneapolis, Phoenix | Americas | no recent rule source in registry | Do not use until current market rules exist and station/source are extracted. |

### P1: Source identified, but needs source-specific adapter or speed monitor

| City | Region | Source | Why |
|---|---|---|---|
| Moscow | Europe/Asia | NOAA/weather.gov WRH `site=UUWW` -> Synoptic backend | Fresh follow-up matched 34/34 against pm_history, so source is fixable. Needs Synoptic/weather.gov runtime adapter and latency monitor before live eligibility. |
| Istanbul | Asia/Europe | NOAA/weather.gov WRH `site=LTFM` | Rules are non-WU. Needs same Synoptic/weather.gov adapter family as Moscow and direct alignment check. |
| TelAviv | Asia | NOAA/weather.gov WRH `site=LLBG` | Same as Istanbul: source is clear, but not covered by METAR/WU HTTP mirror policy yet. |
| Chicago | Americas | WU `KORD`, profile configured station was `KMDW` | Station-diff is historically 37/37 aligned. For speed edge, use official KORD, and prioritize US-only fast ASOS/MADIS/weather.gov paths. |
| Paris, London, Milan | Europe | WU official station differs from configured station | Historically validated at official station. They are eligible for monitor only with explicit station-diff include and rules recheck. |
| KualaLumpur, Jakarta | Asia | WU official station differs from configured station | KualaLumpur is current-rule visible as WMKK. Jakarta was confirmed WIHH/Halim previously, but current slugs were not present in this pull. Both need official-station monitor, not configured-station shortcut. |
| PanamaCity | Americas | WU `MPMG`, configured station was `MPTO` | Station-diff historically validated. Needs current-rule refresh when markets reopen. |

### P2: Rules are WU same-station; deep source work is lower priority

| Region | Cities | Next work |
|---|---|---|
| Europe | Amsterdam, Ankara, Helsinki, Madrid, Munich, Warsaw | Settlement source is clear enough. Work should shift to source arrival speed: AWC/tgftp/CheckWX vs LDM/IDD, plus WU final-row spot checks. |
| Asia | Beijing, Busan, Chengdu, Guangzhou, Jeddah, Karachi, Lucknow, Manila, Shanghai, Singapore, Taipei, Tokyo, Wuhan | Shanghai/ZSPD and Tokyo/RJTT remain first-line speed candidates. Others are clean enough for source monitoring but need liquidity/orderbook evidence before promotion. |
| Americas | Atlanta, Austin, BuenosAires, Dallas, Denver, Houston, LA, Miami, NYC, SanFrancisco, SaoPaulo, Seattle | Source is likely straightforward WU station history. For US cities, source-speed research is more interesting than settlement-source research because ASOS/MADIS/SPECI may beat public global HTTP mirrors. |

## Recommended Next Order

1. Keep Shanghai/ZSPD and Tokyo/RJTT as the clean global METAR speed baseline.
2. Add Chicago/KORD as the US fast-source probe when a current market is open, because US ASOS/MADIS/SPECI may be the only plausible public path under 10 seconds.
3. Implement Synoptic/weather.gov adapter once, then test Moscow/UUWW, Istanbul/LTFM, and TelAviv/LLBG as a family.
4. Use Browser/Chrome network capture for Shenzhen WU daily history. If current rendered daily observations still do not map cleanly, keep Shenzhen banned.
5. Reconstruct Seoul mismatch days from WU rendered daily rows before any further latency work.
6. Treat Paris/London/Milan as second-stage station-diff monitors after explicit rule recheck; they are source-clear but not as attractive as US ASOS paths for speed.

## Bottom Line

There is no need to deeply re-litigate every WU same-station city before source-speed monitoring. The deeper work should be concentrated on:

- unresolved/special Asia: Shenzhen, Seoul, HongKong;
- non-WU WRH/Synoptic family: Moscow, Istanbul, TelAviv;
- station-diff but validated cities: Chicago, Paris, London, Milan, KualaLumpur, Jakarta, PanamaCity;
- US fast-source path: Chicago first, then other US same-station cities when markets are open.

Everything else can stay in broad research or source-speed monitoring with a per-market rules recheck.

## References

- Current Gamma API slug pattern: `https://gamma-api.polymarket.com/events?slug=highest-temperature-in-shanghai-on-june-18-2026`
- Wunderground Shanghai rule URL: `https://www.wunderground.com/history/daily/cn/shanghai/ZSPD`
- Wunderground Tokyo rule URL: `https://www.wunderground.com/history/daily/jp/tokyo/RJTT`
- Wunderground Paris rule URL: `https://www.wunderground.com/history/daily/fr/bonneuil-en-france/LFPB`
- Wunderground London rule URL: `https://www.wunderground.com/history/daily/gb/london/EGLC`
- NOAA WRH Moscow rule URL: `https://www.weather.gov/wrh/timeseries?site=UUWW`
- HKO climate extract landing page: `https://www.weather.gov.hk/en/cis/climat.htm`
