# Lead Window Report

Lead is `official_first_seen_at_utc - source_detect_ts_utc`, measured on 841 linked events.

| City | events | dates | p10 sec | median sec | p90 sec |
|---|---:|---:|---:|---:|---:|
| Amsterdam | 87 | 16 | 247.0 | 830.1 | 1480.8 |
| Tokyo | 42 | 14 | 561.1 | 730.9 | 1275.7 |
| Helsinki | 59 | 15 | 593.9 | 717.8 | 1278.7 |
| Seoul | 268 | 14 | 423.3 | 691.1 | 971.7 |
| Busan | 385 | 16 | 459.3 | 947.0 | 1585.4 |

The source clocks show substantial nominal lead. This is not yet tradable lead: the same-row executable book funnel collapses to one 5-share/30-second oracle observation.

