# Polymarket Weather Plan — 2026-03-12（v1 建仓单）

> 这是一版给人执行的建仓 list，不是自动下单指令。
> 今天这版先按“只做规整尾部 `NO`、只挂 maker、先小后大”的标准来筛。
> 在真正下单前，先用同站点 `WU Hourly` 再过一遍；下面的天气中心目前主要用机场坐标的 Open-Meteo 结果做盘前代理检查。

## 快照元信息

- `analysis_date`: `2026-03-12`
- `analysis_run_time_local`: `2026-03-12 09:27 CST`
- `analysis_mode`: `historical_intraday_snapshot`
- `market_page_fetch_time_local`: `2026-03-12 09:27 CST`
- `weather_proxy_fetch_time_local`: `2026-03-12 09:27 CST`
- `airport_observation_source`: `not captured in this plan version`
- `data_staleness_note`:
  - Polymarket 页面是抓取当刻看到的盘口分布，不代表它没有在前几分钟内跳动过
  - Open-Meteo 这里记录的是抓取当刻拿到的当日 forecast 代理，不是官方结算源
  - 这份 `3/12` 文件是在补齐元信息时做的回填，所以只回填到当时已明确保存下来的盘前时间点

## 当天关键快照

- `Paris`
  - market center: `13°C 34% / 14°C 29% / 12°C 18% / 15°C 11.3%`
  - weather proxy: `13.0°C`
- `Miami`
  - market center: `86-87°F 36% / 84-85°F 34% / 88-89°F 19% / 82-83°F 9%`
  - weather proxy: `86.5°F`
- `Ankara`
  - market center: `14°C 36% / 15°C+ 34% / 13°C 18% / 12°C 7%`
  - weather proxy: `13.4°C`
- `London`
  - market center: `12°C 49% / 13°C 29% / 11°C 15% / 14°C 5.3%`
  - weather proxy: `11.7°C`

---

## 今天我实际查过的源

- Polymarket:
  - [London](https://polymarket.com/event/highest-temperature-in-london-on-march-12-2026)
  - [Paris](https://polymarket.com/event/highest-temperature-in-paris-on-march-12-2026)
  - [Miami](https://polymarket.com/event/highest-temperature-in-miami-on-march-12-2026)
  - [Ankara](https://polymarket.com/event/highest-temperature-in-ankara-on-march-12-2026)
  - [Toronto](https://polymarket.com/event/highest-temperature-in-toronto-on-march-12-2026)
- Open-Meteo proxy:
  - [London City Airport area](https://api.open-meteo.com/v1/forecast?latitude=51.5048&longitude=0.0495&daily=temperature_2m_max,temperature_2m_min&hourly=temperature_2m&start_date=2026-03-12&end_date=2026-03-12&timezone=auto)
  - [Paris CDG area](https://api.open-meteo.com/v1/forecast?latitude=49.0097&longitude=2.5479&daily=temperature_2m_max,temperature_2m_min&hourly=temperature_2m&start_date=2026-03-12&end_date=2026-03-12&timezone=auto)
  - [Miami MIA area](https://api.open-meteo.com/v1/forecast?latitude=25.7959&longitude=-80.2870&daily=temperature_2m_max,temperature_2m_min&hourly=temperature_2m&start_date=2026-03-12&end_date=2026-03-12&timezone=auto)
  - [Ankara ESB area](https://api.open-meteo.com/v1/forecast?latitude=40.1281&longitude=32.9951&daily=temperature_2m_max,temperature_2m_min&hourly=temperature_2m&start_date=2026-03-12&end_date=2026-03-12&timezone=auto)
  - [Toronto Pearson area](https://api.open-meteo.com/v1/forecast?latitude=43.6777&longitude=-79.6248&daily=temperature_2m_max,temperature_2m_min&hourly=temperature_2m&start_date=2026-03-12&end_date=2026-03-12&timezone=auto)

---

## 全局执行规则

- 只做 `NO`。
- 只挂 maker，不追价。
- 默认两档挂单：`60%` 第一档，`40%` 第二档。
- 任一单在下单前都要再看一次同站点 `WU Hourly`。
- 如果最新同站点主锚已经向你的目标桶靠近到约 `1` 度以内，放弃。
- 如果辅助机场源比主锚冷/暖超过 `1°C`，默认降仓；超过 `2°C`，直接不碰 exact 邻近单。

---

## 1. Paris — 15°C NO

- 市场中心：
  - `13°C 34%`
  - `14°C 29%`
  - `12°C 18%`
  - `15°C 11.3%`
- 代理天气中心：
  - `LFPG` 坐标盘前代理高温约 `13.0°C`
- 判断：
  - 市场主区间在 `13-14°C`
  - `15°C` 已经在主区间右侧一档外
  - 这是今天最干净的一个 `exact 尾部 NO`

### 挂单

- 第一档：`15°C NO @ 0.87`
- 第二档：`15°C NO @ 0.85`

### 失效条件

- 同站点 `WU Hourly` 把目标日 high 上修到 `14-15°C` 一带
- 机场辅助页同步变暖，且峰值时间稳定落在午后右侧

---

## 2. Miami — 82-83°F NO

- 市场中心：
  - `86-87°F 36%`
  - `84-85°F 34%`
  - `88-89°F 19%`
  - `82-83°F 9%`
- 代理天气中心：
  - `KMIA` 坐标盘前代理高温约 `30.3°C`，约 `86.5°F`
- 判断：
  - 市场中心和代理天气都在 `84-89°F`
  - `82-83°F` 明显在左尾
  - 只要没有午后对流或海风提前压温，这单结构是舒服的

### 挂单

- 第一档：`82-83°F NO @ 0.89`
- 第二档：`82-83°F NO @ 0.87`

### 失效条件

- 同站点 `WU Hourly` 下修到 `84-85°F` 主区间
- 机场辅助页开始明显强调云量、阵雨或对流会削掉午后高温

---

## 3. Ankara — 12°C NO（小仓条件单）

- 市场中心：
  - `14°C 36%`
  - `15°C or higher 34%`
  - `13°C 18%`
  - `12°C 7%`
- 代理天气中心：
  - `LTAC` 坐标盘前代理高温约 `13.4°C`
- 判断：
  - 这不是大 edge 单，但 `12°C` 已经被压到左侧小概率档
  - 适合小仓做规整左尾 `NO`
  - 如果你后续抓到同站点 WU 还是 `13-14°C` 中心，这单可以保留

### 挂单

- 第一档：`12°C NO @ 0.91`
- 第二档：`12°C NO @ 0.89`

### 失效条件

- 同站点 `WU Hourly` 下修到 `12-13°C`
- 官方机场页或辅助机场页比主锚更冷超过 `1°C`

---

## 4. London — 14°C NO（挂着等，不追）

- 市场中心：
  - `12°C 49%`
  - `13°C 29%`
  - `11°C 15%`
  - `14°C 5.3%`
- 代理天气中心：
  - `EGLC` 坐标盘前代理高温约 `11.7°C`
- 判断：
  - 逻辑上 `14°C NO` 是对的
  - 但赔率已经比较薄
  - 这单只适合当停车单，不适合吃单追进去

### 挂单

- 第一档：`14°C NO @ 0.93`
- 第二档：`14°C NO @ 0.91`

### 失效条件

- 同站点 `WU Hourly` 抬到 `13-14°C`
- 盘面已经把 `14°C NO` 顶到 `0.95+`

---

## 今日不做

### Toronto

- 当前市场中心是 `3°C 23% / 1°C 20% / 2°C 20% / 4°C 20%`
- 代理天气高温只有约 `0.6°C`
- 这说明盘面和代理天气并没有形成一个干净、单边的错位，反而像冬末过渡日的宽分布盘
- 这类盘不符合今天“规整尾部 No”的筛选标准

---

## 今日执行优先级

1. `Paris 15°C NO`
2. `Miami 82-83°F NO`
3. `Ankara 12°C NO`
4. `London 14°C NO`

如果你今天只想做两单，就做 `Paris` 和 `Miami`。
