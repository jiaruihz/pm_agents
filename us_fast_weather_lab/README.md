# US Fast Weather Lab

This directory is the reproducible, no-trading benchmark for U.S. METAR,
SPECI, and one-minute ASOS/OMO source latency.

It is intentionally part of the `pm_agents` monorepo rather than a nested Git
repository. Every run records the parent repository commit and the exact
configuration hash.

## Safety boundary

```text
NO_LIVE_ORDERS
NO_TRADING_EXECUTION
NO_AUTH_BYPASS
NO_STOLEN_SESSION_OR_TOKEN
NO_UNBOUNDED_AGGRESSIVE_POLLING
```

The lab writes only to the selected runtime directory. Raw messages and
payloads are immutable evidence; SQLite is an indexed, replayable view.

## One-command bounded smoke run

```bash
us_fast_weather_lab/run.sh \
  --runtime-root runtime/us_fast_weather_lab \
  --vantage-id LOCAL_MAC_ASIA_NETWORK \
  --duration-sec 180
```

The smoke command:

1. probes the local clock;
2. discovers the current WIS2 Global Brokers from the GDC;
3. subscribes independently to origin/cache on each broker;
4. polls all 20 airports in one AWC request at a bounded 2-second cadence;
5. downloads the AWC full cache once per minute;
6. writes raw evidence first, then parses asynchronously;
7. generates paired-first-seen reports and evidence indexes.

Clock-invalid rows remain in evidence but are excluded from second-level
rankings. A smoke run never claims a 72-hour source disposition.

## Commands

```bash
# schema + deterministic replay tests only
.venv/bin/python -m us_fast_weather_lab.cli init --runtime-root runtime/us_fast_weather_lab
.venv/bin/python -m us_fast_weather_lab.cli replay --runtime-root runtime/us_fast_weather_lab

# regenerate reports from an existing runtime
.venv/bin/python -m us_fast_weather_lab.cli report \
  --runtime-root runtime/us_fast_weather_lab \
  --reports-root us_fast_weather_lab/reports
```

For a 24-hour or 72-hour run, use the same command with the corresponding
duration. Do not change airports, config, clock threshold, or event identity
inside a benchmark window.

Credential-gated WebSocket feeds use the same evidence schema and can be added
to a run only after the credential is placed in the process environment:

```bash
METAR_WS_API_KEY='<local-secret>' SYNOPTIC_API_TOKEN='<local-secret>' \
  .venv/bin/python -m us_fast_weather_lab.cli smoke \
  --runtime-root runtime/us_fast_weather_lab_commercial \
  --reports-root runtime/us_fast_weather_lab_commercial/reports \
  --vantage-id LOCAL_MAC_ASIA_NETWORK \
  --duration-sec 259200 \
  --enable-metar-ws \
  --enable-synoptic-push
```

Do not put either credential in Git, a report, or the command history; the
inline assignment above is illustrative. METAR.ws cached replay frames are
retained with `cached_replay_non_latency` status and excluded from paired
latency. Official METAR, HF-METAR, D-ATIS-derived observations, and Synoptic
high-frequency temperature observations remain separate source/report classes.
Only official METAR/SPECI versions enter same-report pairing; high-frequency
temperature and D-ATIS are evaluated as temporal lead studies.

When a public WIS2/AWC run is already active, use the isolated commercial
canary instead of creating an overlapping public-source run. Set the secret and
start the canary from the same shell:

```zsh
read -s 'METAR_WS_API_KEY?Paste the new METAR.ws key (hidden): '; export METAR_WS_API_KEY; echo
.venv/bin/python -m us_fast_weather_lab.cli smoke \
  --runtime-root runtime/us_fast_weather_lab_metarws_canary \
  --reports-root runtime/us_fast_weather_lab_metarws_canary/reports \
  --vantage-id ASIA_CN_MAC \
  --duration-sec 300 \
  --enable-metar-ws \
  --disable-wis2 \
  --disable-awc
```

The disable flags are opt-in; the default smoke command still starts both
public collectors. A disabled-public canary fails before creating a run unless
at least one authenticated commercial collector is enabled.

## Existing project evidence reused

The lab does not erase or duplicate the earlier U.S. source work:

- direct NOAA MADIS OMO historical NetCDF parsing and received-time analysis;
- IEM MADISHF ↔ direct MADIS identity tests;
- the Atlanta 2026-07-17 terminal-false negative control;
- AWC/TGFTP/Synoptic polling and source-orderbook timing monitors;
- the earlier bounded WIS2 discovery probe.

`reports/EXISTING_RESEARCH_CENSUS.md` records exactly what those studies did
and did not establish.

Access-gated FAA/MADIS/IDD/relay work uses `manual/README.md`; sensitive local
captures and credentials are gitignored. Public collection continues while
those applications are pending.

## Full-closure workspace

`closure/` is the auditable source/access ledger for the expanded investigation.
It includes the public WIS2 defect evidence, 20-airport direct-phone inventory,
vendor/provider contact message IDs, PeakMet public-claim fingerprint, and the
minimal manual action queue. A sent inquiry, successful connection, or compiled
collector is never recorded as a proven latency advantage.
