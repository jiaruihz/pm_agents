# Weather production incident：Open-Meteo 被误路由到 market proxy

Status: fixed / deployed / impact quantified

## 结论

`paper_snapshot` 在提交 `da10a696` 后把 Open-Meteo forecast 请求也传给了只应服务 Polymarket 的 `WEATHER_DATA_FEED_MARKET_PROXY`。该代理节点对 Open-Meteo 返回 `429 daily quota`，而同机 direct request 返回 `200`；snapshot 因此静默复用旧 forecast curve cache。

修复提交 `56edaad06ed3085087a376dedc66b2334de7e018` 已部署到 Mac 当前生产：Open-Meteo 不再复用 market proxy。回归测试与相关生产测试为 `98 passed`。

## 影响窗口

- 最后一个事故前 fresh curve：`2026-07-28T13:19:15Z` 附近。
- core 因 stale curve 产生的 decision rows：`2026-07-28T13:34:43Z` .. `2026-07-28T15:50:17Z`。
- core 在部署时停机，forecast data feed 于 `2026-07-28T16:36:44Z` 发布首个修复后 capture。
- 首个修复后 capture：103/103 city-target rows，52 cities，3 target dates，`cached_curve_fallback_count=0`，GFS 44 / ECMWF 59。

## 决策影响

core-carry 在受影响窗口消费了：

- 7 个 paper snapshots；
- 36 个 score rows；
- 29 个 unique checkpoint keys；
- 11 个 city-days；
- 36/36 `decision_status=not_eligible`；
- 0 entry plans；
- 0 submitted orders；
- 0 fills。

因此 confirmed wrong live orders = `0`，confirmed wrong fills = `0`。事故窗口没有资金损失。

无法诚实恢复的是该窗口每个时点本应直接抓到的 Open-Meteo publication vintage；修复后的 16:27Z forecast 不能冒充 13:34–15:50Z 的 PIT forecast。因此 missed-entry 反事实不能给伪精确数字，后续研究必须把这段标成 `forecast_vintage_missing_due_proxy_429`，不能把 stale-cache rows 混入 feature 或 forward evidence。

## 根因与修复验证

- 根因：forecast HTTP client 错误继承 `MARKET_PROXY`。
- 不是 Open-Meteo 服务整体不可用：事故排查时 proxy=429、direct=200。
- 修复：删除 Open-Meteo `_fetch_live_forecast(..., proxy=PROXY)` 的 proxy 参数；Polymarket market/CLOB 请求仍继续使用 market proxy。
- deployment preflight：`critical_source_dirty=false`，deployed SHA 包含 `56edaad0`。
- fresh capture lineage：103 rows 全部 `open_meteo_live_gfs/ecmwf`，无 cache fallback，`available_at_utc=2026-07-28T16:36:44.463550Z`。
- data-feed health 的 forecast/snapshot/observation critical sections 均为 `ok`；整体 health 的非本事故告警为 disabled high-frequency observation state stale，以及非 trading 城市 Shenzhen weather state missing。

## 数据治理

以下窗口在 morphology、forecast revision、peak-clock alias、model probability 和交易绩效研究中必须 quarantine：

```text
2026-07-28T13:19:15Z < decision_snapshot_ts_utc <= 2026-07-28T15:50:17Z
reason = forecast_vintage_missing_due_proxy_429
```

这与 7/24–7/26 的 observation PIT selection 事故是两个独立问题，不能合并归因。
