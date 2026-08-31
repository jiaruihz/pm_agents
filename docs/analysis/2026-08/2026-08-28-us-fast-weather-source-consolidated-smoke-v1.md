# U.S. airport fast-weather sources: consolidated smoke v1

Status: `bounded smoke complete / 24h+72h blocked / no-live-change`

## Decision

Current disposition is `BLOCKED_BY_ACCESS_OR_INSUFFICIENT_EVIDENCE`. Keep the
append-only collector running, but do not name a fastest source or change any
trading behavior. The old U.S. OMO work is reusable source-basis evidence; it
is not a substitute for a WIS2/AWC/FAA transport race.

## Fixed question and denominator

- Target: first actionable arrival of the same METAR/SPECI semantic version.
- Pairing grain: `semantic_version_id × vantage_id × source`, taking the first
  actionable receipt per source. COR/update keeps the event family and creates
  a new semantic version.
- Exclusions: invalid clock, observation-minute joins, late backfill, and OMO
  raw-hash pairing with METAR.
- Negative control: Atlanta 2026-07-17 terminal false OMO cross remains in the
  denominator and may not be removed by a speed result.

## Existing work consolidated

OBSERVED before this lab:

- the bounded HTTP timing monitor already covered AWC, TGFTP and Synoptic;
- the prior WIS2 probe proved only a basic MQTT skeleton, not a four-broker
  raw/actionable race;
- direct NOAA MADIS had 12,775 OMO observations and 1,522 exact-time overlaps
  with IEM, with 1,522/1,522 rounded-temperature agreement; median NOAA
  `receivedTime-observationTime` was 136.0s versus 1,269.1s for the old IEM
  collector;
- Atlanta 2026-07-17 printed OMO 91.4F with `temperatureQCR=0`, while routine
  METAR/WU finished at 89F;
- the U.S. execution study had 22/23 directionally correct settled candidates,
  but 0/22 correct cases became executable within 10 minutes under the old
  policy. The only false event filled 15 shares and lost $13.05655 including
  verified fee.

Therefore “OMO can arrive earlier” is OBSERVED historically, while “OMO gives
a safe early settlement signal” is disproved by the mandatory negative control.

## New implementation

`us_fast_weather_lab/` now provides:

- SQLite WAL/FULL, immutable raw envelopes and content-addressed payloads;
- wall-clock plus monotonic stamps, chrony/SNTP health, and clock-valid gates;
- common collectors, raw/semantic/event-family identity, correction/update
  retention, replay audit, and final paired Parquet;
- GDC-discovered WIS2 broker/topic configuration, TLS, origin/cache channels,
  callback-first receipt stamps, raw-first queueing, inline/link payloads,
  gzip/TAC/JSON/XML inventory, and lossless BUFR preservation;
- one 20-airport AWC request every two seconds plus a one-minute full-cache
  verifier, custom User-Agent, request/first-byte/finish stamps;
- an exact-commit deployment script for `US_EAST` and `ASIA_SG_OR_MY`.

## 2026-08-28 bounded execution

Artifact root:
`runtime/us_fast_weather_lab_wis2_5m_20260828/`  
Report root: `us_fast_weather_lab/reports/`

OBSERVED:

- duration 303.8s; 144 successful AWC API requests, five full-cache downloads,
  and one preserved HTTP error;
- 149/149 raw messages indexed, 21 semantic observation versions, 2,980
  source-seen rows, and replay identity 21/21 stable;
- 21 AWC API↔cache same-semantic pairs exist, but all are excluded from the
  latency ranking because all five Mac SNTP probes failed the 20ms gate
  (offset 93.226–111.063ms; uncertainty 184.111–222.394ms); clock-valid paired
  rows are 0;
- the current WIS2 GDC returned NOAA/NWS, Météo-France, CMA and INMET broker
  endpoints for the U.S. METAR core topic. CMA/Météo-France/INMET produced six
  verified TLS sessions and six successful origin/cache SUBACKs;
- NOAA's advertised endpoint resolved, but its reachable TLS peer presented
  common name `wis2cacheqa.qa.globaldata.nws.noaa.gov` for expected hostname
  `wis2globalbroker.nws.noaa.gov`. Verification was not bypassed. No target WIS2
  notification arrived during the five-minute bounded window.

The current broker/topic catalog is independently visible in the official
[WIS2 Global Discovery Catalogue](https://wis2-gdc.weather.gc.ca/collections/wis2-discovery-metadata/items/urn%3Awmo%3Amd%3Aus-noaa-nws%3Aaviation-metar-observations?f=html).
The AWC implementation follows the official [Data API limits and cache
cadence](https://aviationweather.gov/data/api/): maximum 100 requests/minute,
custom User-Agent, and the METAR cache updated once per minute.

## Evidence funnel

| Funnel | Count | Status |
|---|---:|---|
| WIS2 brokers discovered | 4 | OBSERVED |
| non-NOAA broker origin/cache TLS + SUBACK paths | 6 | OBSERVED |
| WIS2 target notifications | 0 | INSUFFICIENT_EVIDENCE, not source failure |
| AWC transport messages | 149 | OBSERVED |
| same-semantic AWC API/cache pairs | 21 | OBSERVED |
| clock-valid public-source pairs | 0 | BLOCKED |
| two-vantage paired events | 0 | BLOCKED |
| frozen 24h / 72h windows | 0 / 0 | BLOCKED |
| FAA SWIFT, live MADIS LDM, IDD, paid trials | 0 | BLOCKED_BY_ACCESS |

FAA remains an application-gated comparator: the current FAA description says
SWIFT supplies near-real-time SWIM data over Solace JMS and directs consumers
to the current subscription/Jumpstart material; no queue, JNDI or service name
has been guessed. See [FAA Getting Access to SWIM](https://www.faa.gov/air_traffic/technology/swim/products/get_connected)
and [FAA SWIM standards](https://www.faa.gov/air_traffic/technology/swim/governance/standards).

## Readiness and next fixed run

- `US_EAST`: not deployed.
- `ASIA_SG_OR_MY`: deployment script exists; current Mac smoke is not a named
  production vantage.
- Clock: FAIL for second-level ranking; fix host time discipline before the
  frozen window rather than adjusting observed timestamps after capture.
- Public benchmark: run unchanged collectors for 24 hours, then 72 hours on
  both vantages; a final claim still requires 7–14 days and at least 500 paired
  events under the acceptance contract.
- FAA/MADIS/IDD/commercial: remain blocked on normal account, agreement,
  upstream or trial access. Public paths continue independently.

No plan, order, fill, live process, production config, or canonical weather DB
was changed by this research.
