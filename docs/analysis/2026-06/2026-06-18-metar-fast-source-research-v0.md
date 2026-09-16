# METAR Fast Source Research v0

Status: snapshot
Updated: 2026-06-18
Source of truth: no
Used by: weather latency arb / METAR-cross source research

## Target metric

目标不是先下单，而是把 `report_ts -> local_detect_ts -> orderbook_seen_ts` 这条链路量出来：

- `source_arrival_lag_sec = local_detect_ts_utc - source_report_ts_utc`
- `source_fetch_latency_sec = source_fetch_end_utc - source_fetch_start_utc`
- `book_fetch_latency_sec = book_fetch_end_utc - book_fetch_start_utc`
- `market_state_at_detection = crossing 附近 T 或 T-1 bracket 的 YES/NO best bid/ask/size`

如果要做“刚穿 T 就抢 T-1 NO”，真正需要的是 `source_arrival_lag_sec <= 10s` 量级。AWC cache / CheckWX / tgftp 这类 HTTP 镜像只能作为公开源赛马，不应默认视为够快。

## Current evidence

本机和 N100 的执行层暂时不是主瓶颈：

- 本机 CLOB book fetch 在此前样本约 0.4-0.6s。
- N100 直连 CLOB/Gamma 会 timeout，但走本地 xray proxy `127.0.0.1:10809` 后约 0.6-0.8s。
- N100 和本机都能连通 `idd.unidata.ucar.edu:388`，说明 LDM/IDD PoC 的网络前提存在。

本轮新增的 NOAA tgftp station TXT 候选源可读：

```text
https://tgftp.nws.noaa.gov/data/observations/metar/stations/ZSPD.TXT
https://tgftp.nws.noaa.gov/data/observations/metar/stations/RJTT.TXT
```

2026-06-17T16:25Z 本机单轮 `Shanghai Tokyo` 观测：

| Source | ZSPD report | ZSPD fetch latency | RJTT report | RJTT fetch latency | 结论 |
|---|---:|---:|---:|---:|---|
| `aviationweather_metar` | 16:00Z | 0.617s | 16:00Z | 0.704s | 下载快，但本轮已经是 report 后 25m；不是 10s 证据 |
| `noaa_tgftp_station_txt` | 16:00Z | 2.234s | 16:00Z | 1.075s | 可作为官方 HTTP 镜像候选；本轮不比 AWC 快 |
| `checkwx_html` | 16:00Z | 1.767s | 16:00Z | 1.165s | 同步到同一报文；仍是下游镜像 |

同轮盘口状态已经接近二元：

| City | Target date | Bracket | NO bid | NO ask | 含义 |
|---|---|---:|---:|---:|---|
| Shanghai | 2026-06-18 | 24C or below | 0.995 | 0.998 | 仍有 ask，但已经很贵 |
| Tokyo | 2026-06-18 | 21C or below | 0.996 | 0.999 | 仍有 ask，但几乎没有可吃空间 |

这说明单纯把 HTTP polling 从 60s 降到 2-10s，不足以保证能抢到肉；需要更靠近上游的 feed。

2026-06-17T16:39Z 追加一轮 `Shanghai Tokyo Paris London Chicago`，含 station-diff 城市：

| City | Source group | Latest report seen | Detect lag at sample | Note |
|---|---|---:|---:|---|
| Chicago/KORD | AWC / tgftp / CheckWX | 16:33Z SPECI | ~360-363s | 美国 SPECI 能插入；weather.gov latest 可读但本轮 timestamp 16:15Z，未领先 |
| Paris/LFPB | AWC / tgftp / CheckWX | 16:30Z | ~551-553s | 三个 HTTP 镜像同层 |
| London/EGLC | AWC / tgftp / CheckWX | 16:20Z | ~1146-1149s | 三个 HTTP 镜像同层 |
| Shanghai/ZSPD | AWC / tgftp / CheckWX | 16:30Z | ~556-558s | 三个 HTTP 镜像同层 |
| Tokyo/RJTT | AWC / tgftp | 16:30Z | ~563-564s | CheckWX 本轮仍停在 16:00Z |

这轮结论更清楚：HTTP 镜像之间会有小差异，但都没有证明能稳定进入 10s；KORD 这种美国城市可能因为 SPECI/ASOS 链路有额外研究价值。

## Implemented monitor changes

`scripts/ops/weather_source_orderbook_timing_monitor.py` 现在支持：

- 新 source：`noaa_tgftp_station_txt`，alias：`tgftp` / `noaa_tgftp`。
- 新 source：`synopticdata_timeseries`，alias：`synoptic` / `weather_gov_wrh` / `wrh_timeseries`。它通过 weather.gov WRH 页面使用的 Synoptic Data timeseries API 拉 `air_temp`，可覆盖 `UUWW/LTFM/LLBG/ZSPD/RJTT/KORD/LFPB/EGLC` 等站。
- 研究城市模式：`--include-research-cities` 可以把 source profile 里 blocked/non-WU 城市纳入 timing monitor，但这只影响观测，不改变 live eligibility 或交易准入。
- 并发 cycle：source fetch 与 orderbook fetch 改为 `--max-workers` 并发，8 城 x 4 源 x nearby books 的本机 cycle 从约 60s 降到约 7-10s。
- 分离代理：
  - `TIMING_MONITOR_WEATHER_PROXY_MODE` / `TIMING_MONITOR_WEATHER_PROXY`
  - `TIMING_MONITOR_MARKET_PROXY_MODE` / `TIMING_MONITOR_MARKET_PROXY`
- N100 当时使用的环境如下（历史复现记录；直启 wrapper 已删除，不是当前运维入口）：

```bash
TIMING_MONITOR_WEATHER_PROXY_MODE=direct \
TIMING_MONITOR_MARKET_PROXY=http://127.0.0.1:10809 \
TIMING_MONITOR_SOURCES="profile_primary synopticdata_timeseries noaa_tgftp_station_txt checkwx_html" \
.venv/bin/python scripts/ops/weather_source_orderbook_timing_monitor.py
```

这样天气源直连，市场侧走 xray，不再被一个全局 proxy mode 绑死。

2026-06-18 本机晚间 monitor 配置（历史单次复现）：

```bash
TIMING_MONITOR_CITIES="Shanghai Tokyo Chicago Paris London Moscow Istanbul TelAviv" \
TIMING_MONITOR_INCLUDE_STATION_DIFF=1 \
TIMING_MONITOR_INCLUDE_RESEARCH_CITIES=1 \
TIMING_MONITOR_SOURCES="synopticdata_timeseries aviationweather_metar noaa_tgftp_station_txt checkwx_html" \
TIMING_MONITOR_BASE_INTERVAL_SEC=10 \
TIMING_MONITOR_BURST_INTERVAL_SEC=2 \
TIMING_MONITOR_BURST_WINDOW_MIN=12 \
TIMING_MONITOR_MAX_WORKERS=16 \
TIMING_MONITOR_HTTP_TIMEOUT_SEC=6 \
.venv/bin/python scripts/ops/weather_source_orderbook_timing_monitor.py
```

First verified concurrent loop cycle: `cycle_runtime_sec=9.549`, `source_rows=32`, `book_rows=38`, `errors=0`.

Important caveat: Synoptic is not uniformly faster. In a June 17 18:07Z local sample, `KORD` Synoptic had a newer 17:55Z observation while AWC/tgftp were still at 17:51Z, but `ZSPD/RJTT` Synoptic lagged AWC/tgftp at 17:30Z vs 18:00Z. Treat Synoptic as a source-race candidate and WRH/non-WU settlement adapter, not as a blanket global low-latency solution.

## Source ladder

当前分层应该这样看：

1. **LDM/IDD / WMO-AFTN-like feed**：最值得做 PoC，理论上才可能进 10s 内。下一步装 LDM tools，用 `notifyme` 或最小 receiver 只订 ZSPD/RJTT/候选站 METAR，记录 `product_arrival_ts`。
2. **NOAA tgftp station TXT**：官方 HTTP 镜像，已接入 monitor；适合赛马，不证明快。
3. **AWC API/cache**：下载快，但仍是下游分发面；适合校验和 fallback，不应单独当 10s 主触发。
4. **US-only weather.gov / MADIS / 1-minute ASOS**：只适合 Chicago 等美国城市；非全球 METAR 解法。
5. **HKO Open Data**：只适合 HongKong 特殊结算源，需要把 HKO station/place 与 market 结算口径重新对齐。
6. **CheckWX/IEM 等第三方/归档源**：只做 timing compare 或 fallback，不作为 10s live trigger。

## Next action

先继续 shadow / monitor，不 tiny-live。

下一步应做 `LDM/IDD PoC`：

1. 在本机或 N100 安装/启用 LDM client tools。
2. 用 `idd.unidata.ucar.edu:388` 订 METAR feed，过滤 `ZSPD|RJTT|KORD|LFPB|EGLC`。
3. 每条产品写 append-only JSONL：`product_id`、`product_arrival_ts_utc`、`raw_metar`、`source_report_ts_utc`、`station`、`temp_c`、`payload_hash`。
4. 同步写 orderbook snapshot，和现有 `sources.jsonl/books.jsonl` 同口径 join。
5. 只有当 LDM arrival 相对 report time、且相对盘口变化有正窗口时，再考虑 tiny-live。

## References

- NWS ASOS: https://www.weather.gov/asos/
- NWS ASOS technical: https://www.weather.gov/asos/asostech
- AviationWeather Data API: https://aviationweather.gov/data/api/
- AviationWeather cache: https://aviationweather.gov/data/cache/
- NOAA tgftp METAR station files: https://tgftp.nws.noaa.gov/data/observations/metar/stations/
- Unidata LDM docs: https://docs.unidata.ucar.edu/ldm/
- Unidata IDD: https://www.unidata.ucar.edu/data/internet-data-distribution-idd
