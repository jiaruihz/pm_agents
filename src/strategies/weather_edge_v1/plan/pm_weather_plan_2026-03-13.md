# Polymarket Weather Plan — 2026-03-13（v1 建仓单）

> 这版是 `2026-03-13` 的盘前推荐，不是自动下单指令。
> 仍然按当前简化流程来做：`Polymarket 页面 + 机场坐标 Open-Meteo 代理 + CheckWX 当前观测`。
> 真正下单前，仍建议你人工再看一次同站点 `WU Hourly`。

## 快照元信息

- `analysis_date`: `2026-03-13`
- `analysis_run_time_local`: `2026-03-13 09:10 CST`
- `analysis_last_edit_time_local`: `2026-03-13 09:17 CST`
- `analysis_mode`: `intraday_asia_refresh`
- `market_page_fetch_time_local`: `2026-03-13 09:10 CST`
- `weather_proxy_fetch_time_local`: `2026-03-13 09:10 CST`
- `airport_observation_time_utc`:
  - `ZSPD`: `2026-03-13T01:00:00Z`
  - `RKSI`: `2026-03-13T01:00:00Z`
  - `VHHH`: `2026-03-13T01:00:00Z`
  - `RJTT`: `2026-03-13T01:00:00Z`
- `data_staleness_note`:
  - Polymarket 页面没有稳定暴露统一的“行情更新时间”，这里记的是本地抓取时间
  - CheckWX 观测时间和本地运行时间之间存在固定 gap，这是正常现象，回看时必须一起看
  - Open-Meteo 记录的是抓取当刻拿到的 forecast 代理，不是最终结算源

## 当天关键快照

- `Shanghai`
  - market center: `13°C 52% / 14°C 19% / 15°C 11% / 12°C 7%`
  - weather proxy: `10.2°C`
  - airport observation: `9°C @ 2026-03-13T01:00:00Z`
- `Seoul`
  - market center: `11°C 36% / 12°C+ 30% / 10°C 29% / 9°C 7.4%`
  - weather proxy: `7.1°C`
  - airport observation: `7°C @ 2026-03-13T01:00:00Z`
- `Tokyo`
  - market center: `10°C 62% / 11°C 27% / 12°C 8% / 9°C 8%`
  - weather proxy: `9.3°C`
  - airport observation: `7°C @ 2026-03-13T01:00:00Z`
- `Hong Kong`
  - market center: `page extraction unstable at 09:10 CST`
  - weather proxy: `20.9°C`
  - airport observation: `19°C @ 2026-03-13T01:00:00Z`

## 09:10 CST 亚洲盘更新

这个时点我重新抓了 `Shanghai / Seoul / Tokyo / Hong Kong` 的盘口和机场观测。

- `Shanghai`
  - 最新盘口中心：`13°C 52% / 14°C 19% / 15°C 11% / 12°C 7%`
  - 机场代理高温：约 `10.2°C`
  - 当前机场观测：约 `9°C`
  - 更新判断：`14°C NO` 仍然是亚洲盘里最干净的一单

- `Seoul`
  - 最新盘口中心：`11°C 36% / 12°C+ 30% / 10°C 29% / 9°C 7.4%`
  - 机场代理高温：约 `7.1°C`
  - 当前机场观测：约 `7°C`
  - 更新判断：市场已经明显变暖，但和机场天气仍有落差；`12°C or higher NO` 还能做，`11°C NO` 不建议现在硬上

- `Tokyo`
  - 最新盘口中心：`10°C 62% / 11°C 27% / 12°C 8% / 9°C 8%`
  - 机场代理高温：约 `9.3°C`
  - 当前机场观测：约 `7°C`
  - 更新判断：`11°C NO` 可以列入次选，但强度弱于 Shanghai / Seoul

- `Hong Kong`
  - 机场代理高温：约 `20.9°C`
  - 当前机场观测：约 `19°C`
  - 页面提取在这个时点不够稳定
  - 更新判断：先降级为观察单，不把它列入主推

这个时点的亚洲盘优先级更新为：

1. `Shanghai 14°C NO`
2. `Seoul 12°C or higher NO`
3. `Tokyo 11°C NO`

---

## 今天实际查过的市场

- [Shanghai](https://polymarket.com/event/highest-temperature-in-shanghai-on-march-13-2026)
- [Seoul](https://polymarket.com/event/highest-temperature-in-seoul-on-march-13-2026)
- [Hong Kong](https://polymarket.com/event/highest-temperature-in-hong-kong-on-march-13-2026)
- [Munich](https://polymarket.com/event/highest-temperature-in-munich-on-march-13-2026)
- [London](https://polymarket.com/event/highest-temperature-in-london-on-march-13-2026)
- [Paris](https://polymarket.com/event/highest-temperature-in-paris-on-march-13-2026)
- [Miami](https://polymarket.com/event/highest-temperature-in-miami-on-march-13-2026)
- [Ankara](https://polymarket.com/event/highest-temperature-in-ankara-on-march-13-2026)
- [Toronto](https://polymarket.com/event/highest-temperature-in-toronto-on-march-13-2026)
- [Chicago](https://polymarket.com/event/highest-temperature-in-chicago-on-march-13-2026)

## 今天实际查过的辅助源

- [ZSPD CheckWX](https://www.checkwx.com/weather/ZSPD/metar)
- [RKSI CheckWX](https://www.checkwx.com/weather/RKSI/metar)
- [VHHH CheckWX](https://www.checkwx.com/weather/VHHH/metar)
- Open-Meteo airport proxies:
  - [Shanghai Pudong](https://api.open-meteo.com/v1/forecast?latitude=31.1443&longitude=121.8083&daily=temperature_2m_max,temperature_2m_min&start_date=2026-03-13&end_date=2026-03-13&timezone=auto)
  - [Incheon](https://api.open-meteo.com/v1/forecast?latitude=37.469&longitude=126.451&daily=temperature_2m_max,temperature_2m_min&start_date=2026-03-13&end_date=2026-03-13&timezone=auto)
  - [Hong Kong Intl](https://api.open-meteo.com/v1/forecast?latitude=22.308&longitude=113.9185&daily=temperature_2m_max,temperature_2m_min&start_date=2026-03-13&end_date=2026-03-13&timezone=auto)
  - [Munich Airport](https://api.open-meteo.com/v1/forecast?latitude=48.3538&longitude=11.7861&daily=temperature_2m_max,temperature_2m_min&start_date=2026-03-13&end_date=2026-03-13&timezone=auto)

---

## 今天的结论先看

今天不是欧洲老面孔最舒服的一天。  
`London / Paris / Miami / Ankara / Toronto` 的市场中心和机场天气代理大体贴得比较近，做不到特别舒服的错位。  
真正比较像样的错位在：

1. `Shanghai`
2. `Seoul`
3. `Hong Kong`
4. `Munich`（只给小仓条件位）

---

## 1. Shanghai — 14°C NO

- 市场中心：
  - `13°C 55%`
  - `14°C 23%`
  - `15°C 12%`
  - `12°C 9%`
- 机场天气代理：
  - `ZSPD` 高温约 `10.5°C`
- 当前机场观测：
  - `CheckWX` 最近观测 `7°C`
- 判断：
  - 市场把 `13-14°C` 当主区间
  - 机场代理更像 `10-11°C`
  - 这让 `14°C NO` 比直接硬空 `13°C` 更符合我们一贯的 tail-no 纪律

### 挂单

- 第一档：`14°C NO @ 0.79`
- 第二档：`14°C NO @ 0.77`

### 失效条件

- 同站点 `WU Hourly @ ZSPD` 抬到 `13-14°C`
- 机场辅助页同步转暖，且午后峰值时间稳定

---

## 2. Seoul — 12°C or higher NO

- 市场中心：
  - `10°C 49%`
  - `11°C 26%`
  - `12°C or higher 14%`
  - `9°C 11%`
- 机场天气代理：
  - `RKSI` 高温约 `6.9°C`
- 当前机场观测：
  - `CheckWX` 最近观测 `4°C`
- 判断：
  - 市场仍然把右侧暖尾留了不少权重
  - 但机场代理和当前观测都更偏冷
  - 直接做 `10°C NO` 属于和市场主区间硬碰，`12°C+ NO` 更像规整右尾 `NO`

### 挂单

- 第一档：`12°C or higher NO @ 0.86`
- 第二档：`12°C or higher NO @ 0.84`

### 失效条件

- 同站点 `WU Hourly @ RKSI` 抬到 `10-11°C` 以上并继续上修
- 午前机场观测明显快于预期升温

---

## 3. Hong Kong — 22°C NO

- 市场中心：
  - `21°C 64%`
  - `22°C 19%`
  - `20°C 11%`
  - `23°C or higher 9.9%`
- 机场天气代理：
  - `VHHH` 高温约 `20.7°C`
- 当前机场观测：
  - `CheckWX` 最近观测 `20°C`
- 判断：
  - 这不是巨大错位，但 `22°C` 仍在市场右侧一档
  - 机场代理和当前观测都更像 `20-21°C`
  - 适合做一笔规整、不过分激进的右尾 `NO`

### 挂单

- 第一档：`22°C NO @ 0.82`
- 第二档：`22°C NO @ 0.80`

### 失效条件

- 同站点 `WU Hourly @ VHHH` 继续把 high 稳稳放在 `22°C`
- 机场辅助页明显比代理更暖

---

## 4. Munich — 17°C NO（小仓条件单）

- 市场中心：
  - `16°C 41%`
  - `15°C 24%`
  - `17°C 17%`
  - `14°C 9%`
- 机场天气代理：
  - `EDDM` 高温约 `14.6°C`
- 判断：
  - 表面上 `17°C NO` 是能做的
  - 但 Munich 有明显 foehn / frontal 风险
  - 这单只能小仓，且必须人工再看同站点 `WU Hourly`

### 挂单

- 第一档：`17°C NO @ 0.82`
- 第二档：`17°C NO @ 0.80`

### 失效条件

- 同站点 `WU Hourly @ EDDM` 上修到 `16-17°C`
- 任何辅助机场页开始给更强暖尾

---

## 今天不主推的市场

### London / Paris / Miami / Ankara / Toronto

- 当前盘口中心和机场天气代理整体贴得比较近
- 没看到很舒服的“市场中心和天气中心显著错位”

### Chicago

- 天气代理比市场冷，但 Chicago 结构本身太容易惩罚懒判断
- 在没有同站点 `WU Hourly` 数值确认前，不把它列入今天主推

---

## 今日执行优先级

1. `Shanghai 14°C NO`
2. `Seoul 12°C or higher NO`
3. `Hong Kong 22°C NO`
4. `Munich 17°C NO`（条件小仓）

如果今天只做两单，就做 `Shanghai` 和 `Seoul`。
