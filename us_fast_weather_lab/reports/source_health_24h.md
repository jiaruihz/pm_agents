# Source health smoke report

OBSERVED duration: `303.8s`; raw messages: `149`; actionable seen rows: `2980`.
OBSERVED raw-index coverage: `149/149`; unique transport payloads: `16`.
UNVERIFIED 24-hour health: this run has not completed 24 hours.

| source | messages | unique payloads |
|---|---:|---:|
| AWC_API | 144 | 11 |
| AWC_CACHE | 5 | 5 |

## Access evidence

| source | endpoint | phase | status | n |
|---|---|---|---|---:|
| AWC_API | aviationweather.gov | http_request | error | 1 |
| WIS2 | gb.wis.cma.cn | connect_tls | ok | 2 |
| WIS2 | gb.wis.cma.cn | dns | ok | 2 |
| WIS2 | gb.wis.cma.cn | suback | ok | 2 |
| WIS2 | globalbroker.inmet.gov.br | connect_tls | ok | 2 |
| WIS2 | globalbroker.inmet.gov.br | dns | ok | 2 |
| WIS2 | globalbroker.inmet.gov.br | suback | ok | 2 |
| WIS2 | globalbroker.meteo.fr | connect_tls | ok | 2 |
| WIS2 | globalbroker.meteo.fr | dns | ok | 2 |
| WIS2 | globalbroker.meteo.fr | suback | ok | 2 |
| WIS2 | wis2-gdc.weather.gc.ca | dynamic_discovery | ok | 1 |
| WIS2 | wis2globalbroker.nws.noaa.gov | connect_tls | error | 16 |
| WIS2 | wis2globalbroker.nws.noaa.gov | dns | ok | 2 |
| WIS2 | wis2globalbroker.nws.noaa.gov:8883 | tls_certificate_hostname | error | 1 |
