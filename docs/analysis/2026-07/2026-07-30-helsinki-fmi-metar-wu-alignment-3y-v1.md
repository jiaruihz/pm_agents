# Helsinki FMI → EFHK METAR → WU 三年对齐 v1

## 结论

- 窗口 `2023-07-30..2026-07-30`，共 `1097` 个 Helsinki 本地完整日期。
- 覆盖：FMI `1096` 天 / METAR `1097` 天 / WU 可用（≥40 rows/day）`1093` 天、接口不完整 `4` 天；METAR `52640` 条，WU `52469` 条。
- EFHK METAR 日最高 vs WU EFHK 日最高 exact：`1093/1093 (100.00%)`；不一致 `0` 天，详见 `mismatch_metar_vs_wu.csv`。
- FMI 10m half-up 日最高 vs WU exact：`901/1092 (82.51%)`；FMI vs METAR exact：`904/1096 (82.48%)`。
- 有真实 Polymarket winner 的 `79` 天：WU 命中 `79/79 (100.00%)`，METAR 命中 `79/79 (100.00%)`，FMI 命中 `61/79 (77.22%)`；WU/METAR 任一不匹配 `0` 天。

## 数据身份与口径

- FMI：站号 `100968` Helsinki-Vantaa，官方 WFS 10 分钟采样。
- METAR：IEM 归档的官方 EFHK METAR/SPECI（report types 3/4）；IEM 是归档镜像，不是 settlement source。
- WU：历史页背后的 `api.weather.com`，location `EFHK:9:FI`，返回站名 `Helsinki/Vantaa`、units `m`。
- 2023–市场上线前的 WU 日期只是 settlement-source reconstruction；只有本地 `pm_history` 有单一 final winner 的日期才叫真实市场 settlement validation。
- 所有三年数据都是历史回取，没有 first-seen clock，不能用于证明实时 lead 或执行 ROI。

## 产物

- `daily_fmi_metar_wu_alignment.csv`：逐日本地日最高与三方差值。
- `mismatch_metar_vs_wu.csv`：METAR/WU 不一致逐日清单。
- `wu_coverage_gaps.csv`：WU 历史接口不完整日，不进入 exact 分母。
- `market_settlement_mismatches.csv`：真实市场 winner 不一致清单。
- `metar_observations.csv`、`wu_observations.csv`：同步后的观测明细。
- `summary.json`：机器可读覆盖与命中率。
