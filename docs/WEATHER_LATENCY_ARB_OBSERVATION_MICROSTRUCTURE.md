# Weather Latency Arb：观测源、轮询和盘口反应

状态：当前参考文档  
更新：2026-06-28  
用途：解释 weather latency arb 这条线现在怎么拿天气数据、怎么轮询、慢在哪里、市场反应有多快，以及后续研究该往哪里走。  
相关入口：`WEATHER_STRATEGY_ENTRYPOINT.md`、`WEATHER_STRATEGY_REGISTRY.md`、`analysis/post_cross_repricing.md`

这份文档管的是 weather latency arb 的“速度层”。核心策略很简单：

> 当某个城市的官方/准官方实时观测第一次把日内最高温从 `T-1` 或更低推到 `T` 时，马上去买 `T-1` 档位的 NO。  
> 如果还能以合理价格吃到 NO ask，这笔理论上基本不承担天气方向风险，主要输在“别人更早看到”或者“盘口已经没货”。

截至 2026-06-25，最重要的结论是：

**我们的本地轮询通常不是最大瓶颈。**  
在源文件一更新后，bot 多数情况下能在几秒内看到。真正拖慢的是上游数据源本身晚几分钟才公开，另外市场里有一部分 bot 会在报告时间前后很快把相关盘口拉到 `0.99+` 或直接清空 ask。

所以这条线分成两件事：

1. 继续找更快、更接近原始观测链路的数据源。
2. 不只盯着已经 cross 的那一档，也研究 cross 后其他温度档位是否还有价格滞后。

## 现在看哪些日志

N100 上主要看这些原始日志。它们是速度和盘口行为的证据，不是最终 PnL 事实表。

| 文件 | 粒度 | 用来回答什么 |
|---|---|---|
| `runtime/weather_edge_v1/source_orderbook_timing/sources.jsonl` | 每个城市、每个源的一次观测 | 某个天气源什么时候报出 `report_ts`，我们本地什么时候看到。 |
| `runtime/weather_edge_v1/source_orderbook_timing/books.jsonl` | 某个 token 一次盘口快照 | 某个 bracket 什么时候变成 `no_asks`、`0.997+`，或者明显被重新定价。 |
| `runtime/weather_edge_v1/metar_cross_prev_no_shadow/cycles.jsonl` | crossing bot 的城市轮询 | 验证 live bot 自己多久扫到一次城市、第一次看到新报文是什么时候。 |
| `runtime/weather_edge_v1/metar_cross_prev_no_shadow/opportunities.jsonl` | 一次 crossing 机会 | 连接城市、温度、report time、market、token 和当时盘口。 |
| `runtime/weather_edge_v1/metar_cross_prev_no_shadow/orders.jsonl` | 一次真实/模拟下单尝试 | 验证 taker 下单耗时、是否成交、为什么被挡。 |
| `runtime/weather_edge_v1/source_orderbook_timing/source_basis_rmk_proxy_state.jsonl` | 每个城市一次 RMK proxy 状态 | 记录 routine RMK、fast/coarse sources、WU current 是否互相冲突。 |
| `runtime/weather_edge_v1/source_orderbook_timing/source_basis_rmk_proxy_opportunities.jsonl` | RMK source-basis + 盘口 | 记录 fast source 看似穿档但 routine RMK 没穿时，proxy bracket 的 YES 是否还便宜。 |

如果问题是“这条策略实际赚了多少钱”，要回到 canonical 表，尤其是 `fact_trades`。  
如果问题是“我们慢在哪里、盘口什么时候动”，看上面这些 raw timing logs。

## RMK source-basis 研究任务

SFO 2026-06-25 的 `68-69°F` case 暴露出一个和原始 crossing bot 不同的形态：

```text
MADISHF / HFMETAR / METAR 正文整数C 看起来已经穿到更高华氏档
routine METAR RMK T-group 的十分之一摄氏度没有穿
WU historical/final 事后更贴近 routine RMK
市场先跟 fast/coarse source 打掉低档 YES，随后又按 WU/RMK 口径纠正
```

这条线的交易方向不是继续买 `T-1 NO`，而是研究：

> 当 fast/coarse source 误导盘口、但 routine RMK 这个 settlement proxy 仍说明低档没死时，是否可以买回对应 bracket 的 YES。

当前实现：

- source 适配仍由 `weather_data_feed/observation_sources` 和 `weather_source_orderbook_timing_monitor.py` 负责；
- `weather_data_feed/source_basis.py` 只做源口径判定，不碰 orderbook、不下单；
- `scripts/ops/weather_rmk_source_basis_opportunity_monitor.py` 消费 timing monitor 的 `sources.jsonl`，再拉对应 bracket 的 CLOB book，输出 shadow/research 日志；
- `routine RMK` 是 US WU/F 市场的 primary settlement proxy；
- `weather_com_current` 只做实时审计，如果 WU current/max 已经高于 RMK proxy，机会行标成 `wu_current_contradicts_proxy`，不算干净机会；
- `weather_com_history_hourly` / WU final 更适合事后 audit 和城市 ban，不适合作为实时撤单触发。

当前任务是研究/监控，不是真钱策略。只有当实时样本证明 `RMK proxy` 稳定贴 WU final、且盘口确实在 WU/RMK 纠正前留下可吃 YES ask，才进入 tiny-live 讨论。

## 城市和数据源怎么准入

城市准入和数据源选择必须集中在 source profile 里，不要再散落在 CSV、命令行参数或者临时脚本里。

当前权威位置：

- `src/strategies/weather_edge_v1/official_observation_feed/source_profiles.json`
- `src/strategies/weather_edge_v1/official_observation_feed/source_registry.py`
- `src/strategies/weather_edge_v1/official_observation_feed/models.py`

每个城市至少要写清楚：

- 结算/规则源是什么；
- 实时观测源是什么；
- station/feed id 是什么；
- 温度是 floor、round，还是整度原始值；
- 是否允许 live；
- 如果不允许 live，ban reason 是什么。

原则很直接：  
**只有实时观测源和结算规则高度一致的城市，才可以进入 live candidate。**  
source/settlement 不清楚、站点不一致、特殊源无法验证的城市，先 `research_only` 或 ban。

这不是保守主义，是因为这个策略吃的是“已经发生的 crossing”。如果观测源和结算源不是同一个事实基础，所谓无方向风险就不成立。

## 当前 N100 上怎么跑

2026-06-25 看到的 N100 live 进程大致是这样：

```text
weather_metar_cross_prev_no_shadow.py loop
  --obs-source noaa_tgftp_station_txt
  --scheduler-mode per_city
  --base-interval-sec 45
  --burst-interval-sec 1
  --burst-window-min 12
  --city-hot-pre-window-min 1
  --city-hot-chase-window-min 10
  --fallback-report-minutes 0,30,53
  --learned-burst-window-min 6
  --market-cache-ttl-sec 1800
  --max-workers 16
  --max-ask 0.995
  --max-notional-per-trade 10
  --max-notional-per-city-day 10
  --max-notional-total-day 50
  --source-by-city-file runtime/weather_edge_v1/metar_cross_prev_no_shadow/source_by_city.json
  --prebuild-live-client
  --include-station-diff
  --live --confirm-live
```

这些参数翻成人话：

- `scheduler-mode per_city`：不是每隔几秒全量扫所有城市，而是每个城市有自己的下一次轮询时间。
- `base-interval-sec 45`：平时城市不在热点窗口时，低频扫，避免所有城市一起挤。
- `burst-interval-sec 1`：城市进入可能更新时间窗口后，按 1 秒级别高频扫。
- `city-hot-pre-window-min 1`：预期 report 前 1 分钟开始加速。
- `city-hot-chase-window-min 10`：预期 report 后继续追 10 分钟，因为很多公开源会晚几分钟才放出来。
- `fallback-report-minutes 0,30,53`：如果还没学到某个城市自己的更新时间，就先假设这些分钟附近可能更新。
- `learned-burst-window-min 6`：有足够历史后，用城市自己学出来的更新时间窗口。
- `market-cache-ttl-sec 1800`：market/token 映射缓存 30 分钟，避免每次重新查市场。
- `max-workers 16`：最多并发处理 16 个城市/任务。
- `max-ask 0.995`：NO ask 高于这个价格就不吃，避免买到几乎没利润或异常盘口。
- `max-notional-*`：真实下单资金上限，目前是小额试运行。

现在这套调度的意义是：  
**热点城市自己加速，非热点城市错峰低频扫。**  
这比“全局每 6 秒扫一遍所有城市”更合理，因为每个城市 METAR/观测更新时间不同。

## 当前有哪些数据源

| 数据源 | 当前作用 | 现状判断 |
|---|---|---|
| `noaa_tgftp_station_txt` | 当前 live 触发主源 | 目前实测最快的公共 HTTP 路径之一，但很多城市仍会比 `report_ts` 晚几分钟公开。 |
| `aviationweather_metar` | 对照/备选 | 有时接近 tgftp，但全球城市上没有稳定领先。 |
| `aviationweather_cache_csv` | 对照 | 公共 cache 层，通常不是最快；cache 更新周期本身约几十秒到一分钟。 |
| `checkwx_html` | 对照/备选 | 常常不是第一名，但可作为独立镜像确认。 |
| `synopticdata_timeseries` | 研究源 | 对部分站可能有价值，但不是全球通用最快源。 |
| `weather_gov_latest` | 研究源 | 有些站点页面能看到 latest state，但覆盖不均。 |
| MADIS public HFMETAR/ASOS | 研究源 | 公共 ASOS 只有 5 分钟粒度；1 分钟 ASOS 被限制给 Gov/NOAA。 |
| LDM/IDD / 商业 OPMET | 尚未成为 active source | 最可能改善源延迟，但需要审批、白名单或付费试用。 |

之前 N100 24h 观测里，这条工作流的排序大致是：

1. `noaa_tgftp_station_txt`
2. `checkwx_html`
3. `synopticdata_timeseries` / AviationWeather cache 明显更慢

这个排序不是永久真理。每次要改 live 策略前，都应该用最新 logs 重新算城市级 winner。

## 也要维护 forecast 更新时间

要。  
如果只做“刚 cross 就抢 `T-1 NO`”，forecast 不是触发源，只是辅助判断城市风险。  
但如果要研究 `post_cross_repricing`，forecast 就变成核心输入之一，因为市场提前撤单、提前把 `T+1`/`T+2` 拉贵，很多时候不是因为已经拿到新观测，而是因为它认为后续继续升温的概率变了。

所以这里要维护两套“更新时间”：

1. **观测更新时间**：某城市真实观测报文什么时候出来，我们什么时候看到。  
   这回答“crossing 事实我们慢了多少”。
2. **预报更新时间**：GFS/ECMWF/JMA/HRRR/ICON/AROME 等 forecast 什么时候刷新，我们什么时候拿到新版本。  
   这回答“市场是不是在用新 forecast 或 nowcast 提前改价”。

当前项目里 forecast cache 已经进入数据血缘，主要镜像位置见 `WEATHER_DATA_PIPELINE.md`：

| Forecast cache | 大致含义 | 主要用途 |
|---|---|---|
| `runtime/weather_edge_v1/market_data/cache/gfs_v4/` | GFS hourly forecast | 全球基线预报。 |
| `runtime/weather_edge_v1/market_data/cache/gfs_daily/` | GFS daily aggregate | 日最高温聚合。 |
| `runtime/weather_edge_v1/market_data/cache/ecmwf_v4/` | ECMWF hourly forecast | 全球中期预报，和 GFS 分歧可作为不确定性信号。 |
| `runtime/weather_edge_v1/market_data/cache/jma_v5/` | JMA forecast | 亚洲城市可能更有参考价值。 |
| `runtime/weather_edge_v1/market_data/cache/hrrr_v5/` | HRRR forecast | 美国城市短临/小时级更新更重要。 |
| `runtime/weather_edge_v1/market_data/cache/icon_eu_v5/` | ICON-EU forecast | 欧洲城市区域模型。 |
| `runtime/weather_edge_v1/market_data/cache/arome_v5/` | AROME forecast | 欧洲局地短临模型。 |

对 latency arb 来说，不能只记录“用了哪个模型”，还要记录版本和本地可见时间。每个城市、每个 forecast 源最好沉淀这些字段：

- `city`
- `forecast_source`
- `provider_endpoint`
- `model_init_ts_utc` 或可推断的 model run
- `provider_visible_ts_utc`
- `local_fetch_ts_utc`
- `forecast_values_hash`
- `forecast_max_native`
- `forecast_peak_time_local`
- `forecast_peak_time_utc`
- `changed_since_last`
- `local_fetch_after_provider_sec`

这里要特别小心：  
**理论更新频率不能直接当交易依据。**  
GFS/ECMWF/HRRR/ICON/AROME 这些模型有自己的发布节奏，但我们实际用的是 Open-Meteo、官方接口、cache 或镜像层时，真正可交易的是“我们什么时候能拿到新版本”。因此文档和研究报告里应该优先写实测 cadence，而不是只写模型理论 cadence。

`post_cross_repricing` 研究里，forecast 更新时间至少要回答三个问题：

1. cross 前几分钟盘口提前动的时候，当时 forecast 有没有刚刷新？
2. `T+1`/`T+2` 被提前拉贵，是不是 forecast peak 上移或 peak hour 后移导致？
3. 哪些城市的 market repricing 更像观测驱动，哪些更像 forecast/nowcast 驱动？

## 已经抢到过什么

目前 confirmed tiny-live fills：

| 城市 | 报告时间 | bot 看到 | live 尝试 | ask | 结果 | 怎么理解 |
|---|---:|---:|---:|---:|---|---|
| Busan 22C NO | 2026-06-24 23:00Z | +277.654s | +286.434s | 0.992 | matched | 市场慢，残余 ask 还在。 |
| BuenosAires 8C NO | 2026-06-25 05:00Z | +244.323s | +247.219s | 0.993 | matched | 主要慢在源发布时间；看到后约 2.9 秒就发起下单。 |

BuenosAires 这笔很能说明问题：

```text
05:03:57.843Z  bot 还看到 04:00 report, temp=8
05:04:01.061Z  bot 还看到 04:00 report, temp=8
05:04:04.323Z  bot 看到 05:00 report, temp=9，触发 crossing
```

也就是说，这次不是我们本地轮询慢 244 秒。  
`tgftp` 源本身大概到 05:04:04 才把 05:00 的报文放出来，而 bot 在源可见后约 3 秒内就抓到了。

## 市场到底有多快

已 crossed 的 `T-1 NO` 是最容易、也最快被市场重新定价的部分。

最近一组 45 个清晰 crossed token 的 book-path 样本里，第一次变得不可买，大致是：

| 分位 | 从 report_ts 到不可买 |
|---|---:|
| p25 | 约 10 秒 |
| p50 | 约 28 秒 |
| p75 | 约 43 秒 |
| p90 | 约 97 秒 |

所以答案很残酷但清楚：

- 有些城市，公共 tgftp 到我们这里时，盘口早就是 `no_asks` 或 `0.999`。
- 有些慢市场，几分钟后还有尾部 ask，可以捞一点汤。
- 如果只做 crossed `T-1 NO`，这基本就是纯速度赛。

几个典型例子：

| 城市 / bracket | 盘口变化 | report | 我们看到 | 解读 |
|---|---:|---:|---:|---|
| Manila 31C NO | report +9s 已无 ask | 03:00Z | +17s | 别人快了几秒。 |
| Singapore 30C NO | ask 0.846 -> 0.997，发生在 report +19s | 06:00Z | +76s | 对方源更快，或者更新窗口响应更好。 |
| Busan 25C NO | report -196s 已从 0.970 到 0.990；report -39s 到 0.999 | 02:00Z | +271s | 不是等 report 后才动，而是提前按 nowcast 撤风险。 |
| Shanghai 24C NO | report -493s 从 0.950 变成 no ask | 04:00Z | +318s | 市场提前预判或撤单。 |
| BuenosAires 8C NO | ask 0.993 保留到 report +217s 左右 | 05:00Z | +244s | 慢市场，尾部流动性还能吃。 |

这里有两类对手：

1. **快源 + 快执行型**：report 出来后 10-60 秒内把 crossed bracket 清掉。Manila、Singapore 更像这个。
2. **提前 nowcast / 撤风险型**：还没等下一条 report，盘口已经因为温度路径和风险预期提前变贵或无 ask。Busan、Shanghai 的部分样本更像这个。

第二类不一定说明别人有秘密数据。很多时候城市已经在 `T`，下一档 `T+1` 是否危险可以通过当前温度、日照时间、历史路径和 forecast 做概率判断，maker 会提前撤掉便宜 NO。

## 目前策略判断

纯版：

> 刚 cross 到 `T`，立刻买 `T-1 NO`

这个策略仍然能捞到小额残余 liquidity，但它不是大肉。要扩大，必须解决两个问题：

- 源要更快；
- 城市池要找到“市场反应慢但源足够可靠”的地方。

更值得继续研究的是后半段：

> crossing 发生后，其他 bracket 的价格是否反应过头、反应不足，或者不同步？

原因是：

- crossed bracket 几乎确定，会最快变贵；
- 当前新高所在 bracket 的 YES/NO 反应不一定同步；
- 更高尾部 bracket，例如 `T+1 YES`、`T+2 YES`，经常不是一起动。

所以更现实的 retail edge 可能不是“抢第一秒的无风险钱”，而是“cross 后几分钟内，市场对后续温度路径的重新定价有没有偏差”。这就是 `post_cross_repricing` 新研究任务。

## 真实下单规则

当前 live 只能小额、显式开启：

- 默认应该是 shadow；
- 真钱必须有 `--live --confirm-live`；
- crossing bot 只做 taker，不挂 maker 单；
- 不做复杂组合，不在这个 runner 里加 basket 逻辑；
- 不能因为已经抢到两口汤就提高 notional。

扩大 live 前至少要重新确认：

- 城市白名单；
- source profile 和结算规则；
- append-only opportunities/orders logs；
- 单笔、单城市、单日 notional 上限；
- max ask cap；
- 不碰 top-tail / ambiguous bracket；
- orderbook 可用且足够新。

## 还要继续做什么

1. N100 继续收集 crossing 源和盘口数据。
2. 做城市级“汤底统计”：看到 crossing 时，`T-1 NO ask <= 0.995` 的概率、源延迟、盘口不可买时间、成交/被挡原因。
3. 给每个城市维护最快源 winner，不要用一个全局源结论套所有城市。
4. 给每个城市维护 forecast 源更新时间和版本 hash，尤其是 GFS/ECMWF/JMA/HRRR/ICON/AROME 的本地可见时间。
5. 继续找真正能改变观测瓶颈的数据源：LDM/IDD、商业 OPMET trial、城市/机场自己的官方 endpoint。
6. 把 `post_cross_repricing` 和原 crossing bot 分开研究：前者是概率交易，后者才是接近无方向风险的速度交易。
