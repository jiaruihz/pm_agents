# Existing U.S. fast-source research census

Status: observed project evidence as of 2026-08-28.

## Already completed

| Asset | Verified result | What it establishes |
|---|---|---|
| `weather_source_orderbook_timing_monitor.py` | AWC, TGFTP, Synoptic and other HTTP sources can be raced with source/book timing; one historical 8-city loop completed in 9.549s with 32 source rows, 38 book rows, and 0 errors | Existing bounded HTTP/source-to-book instrumentation; not a push-source benchmark |
| `weather_wis2_metar_probe.py` | Bounded MQTT probe, TLS credentials, broad aviation topic filtering, compact notification capture, basic tests | WIS2 connectivity/probing skeleton only; no four-broker raw payload race, actionable parsing, or 72-hour paired evidence |
| direct NOAA MADIS OMO study | 12,775 direct observations; 1,522 exact-timestamp direct↔IEM overlaps; rounded temperature agreement 1,522/1,522; NOAA `receivedTime-observationTime` median 136.0s versus IEM collector median 1,269.1s | Direct public MADIS historical files carry the same OMO family much earlier than IEM; not live LDM transport evidence |
| OMO settlement-basis study | Atlanta 2026-07-17 OMO 91.4F had `temperatureQCR=0`, while routine METAR and WU final were 89F; persistent terminal false remained 1 event | Mandatory negative control: QC pass and persistence do not make OMO settlement truth |
| US execution-competition study | 22/23 settled IEM-MADISHF candidates directionally correct, but 0/22 correct candidates became executable under the active policy within 10 minutes; the sole false Atlanta signal filled 15 shares and lost $13.05655 | The old 23.9-minute-median IEM route was too late/adversely selected; lower latency alone still requires basis calibration |

## Not completed before this lab

- No 24-hour WIS2 smoke or frozen 72-hour benchmark.
- No current GDC-driven NOAA/Météo-France/CMA/INMET connection matrix.
- No raw-first WIS2 notification plus canonical/update payload archive.
- No same-contract AWC 2-second batch poll and one-minute full-cache comparison.
- No unified `raw_report_id` / `semantic_version_id` / `event_family_id` pairing across those transports.
- No US_EAST versus Asia two-vantage race.
- No authorized FAA SWIFT/SCDS consumer or live MADIS LDM feed.
- No commercial relay with a timestamp-semantics RFI and seven-day trial.

The new lab reuses these completed findings as priors and negative controls. It
does not count them as WIS2/AWC paired latency samples.

