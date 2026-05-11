# Polymarket Weather Markets - Data Source Guide

## 0. 这份文件解决什么问题

这份文件只负责一件事：

**告诉你在 Polymarket 的天气温度盘口里，哪些页面该当主锚，哪些页面只能做校验，哪些页面只能当噪音。**

这里不维护城市链接，不维护具体盘口，也不维护每个城市的例外规则。  
当前生产城市/机场映射统一由 `weather-predict` 维护：

- `/home/rui/projects/weather-predict/pm_edge_compare.py::CITIES`
- `[airport-selection-current.md](/home/rui/projects/weather-predict/docs/airport-selection-current.md)`

本仓库旧 `city/*.yml` 已归档到 `archive/manual_airport_research/`，只作为手工 source audit 和历史研究参考，不能覆盖生产映射。

先把底层逻辑讲清楚：

- Polymarket 的天气盘不是“看起来哪个页面更顺眼就用哪个”，而是要先认 rules 指定的站点。
- 真正重要的不是“网站名气大不大”，而是“这个页面和最终结算是不是同一个站点对象”。
- 同站点的 Wunderground History / Daily 是结算真值，所以它负责最终答案、复盘和回测。
- 同站点的 Wunderground Hourly 是最接近结算口径的盘前交易锚，所以它负责盘前定价。
- 其他机场页有价值，但主要是帮你检查 WU 有没有明显偏暖、偏冷，或者市场是不是在看别的体系。
- 城市页经常会把人带偏，因为它们看的是城市对象，不是机场结算对象。
- 城市页不是完全没用，但它们只能用来解释“为什么市场会错锚”，不能反过来改掉你的主判断。

---

## 1. 总原则

### 1.1 先看规则，再看天气

每次交易前，先开 Polymarket rules，确认四件事：

1. 结算站点是谁
2. 结算页面属于哪个网站体系
3. 结算单位是 `°C` 还是 `°F`
4. 市场看的到底是当日最高温、最低温，还是别的指标

如果这四件事没确认，后面所有天气判断都不稳。

### 1.2 主锚不是“最权威”，而是“最贴结算”

天气盘最常见的错误，是把“官方”“热门 app”“看起来更完整的城市页”误当成主锚。  
但交易时真正重要的是：**它和最终结算是不是同一个站点、同一个对象、同一种口径。**

默认优先级如下：

1. `WU History / Daily` 同站点页：最终结算真值
2. `WU Hourly` 同站点页：盘前主交易锚
3. `weather.com / TWC` 同栈页面：解释市场共识
4. 机场级辅助源：做 sanity check
5. 城市页：识别错锚污染

### 1.3 先把对象认对，再讨论温度

很多页面写着同一个城市名，但对象并不是同一个：

- 有的是机场站点
- 有的是城市行政区
- 有的是景点 POI
- 有的是更宽泛的 metro 区域

天气盘口里，**对象错了，后面的高一度低一度都没有意义。**

---

## 2. 源分层

### 2.1 Tier 1: 结算源

**这是什么**  
同站点的 `Wunderground History / Daily` 页面。它是最终结算、复盘和回测的真值页。

**什么时候看**  
目标日结束后再看。只有页面数据 finalized 之后，才把它当最终答案。

**能做什么判断**  

- 这一天最终结算到底是多少
- 回头验证你盘前交易判断是否偏了
- 建立历史 truth table

**不能拿它做什么**  

- 不能拿未 finalized 的历史页提前当最终答案
- 不能拿错站点的 History 页代替 rules 指定站点

**固定要求**

- 只认 rules 指定站点
- 只认 finalized 后的数据
- 只认 rules 指定单位
- 如果 rules 说按整度结算，就按整度执行

### 2.2 Tier 2: 主交易锚

**这是什么**  
同站点的 `Wunderground Hourly / Forecast` 页面。它不是结算页，但通常是盘前最贴近结算体系的交易锚。

**什么时候看**  
开仓前先看，持仓中也要反复看，尤其是在峰值临近前。

**能做什么判断**  

- 明日 high 或目标日 high 目前落在哪个中心带
- 白天峰值大概出现在几点
- 盘前到盘中有没有明显上修、下修

**不能拿它做什么**  

- 不能把 forecast 机械当成结算值
- 不能因为 Hourly 给出一个整数，就直接认定 market 一定结算在那一档

**正确理解方式**

它更像“分布中心”，不是“最终答案”。  
例如它报 `44°F`，合理理解是“市场中心大概在 44°F 一带”，而不是“这单必然结算 44°F”。

### 2.3 Tier 3: 同栈共识源

**这是什么**  
`weather.com` 或 The Weather Company 体系下的相关页面。它和 WU 属于同栈生态，但不一定是同一个页面对象。

**什么时候看**  
当你想知道市场是不是在按另一个同栈页面交易时再看。

**能做什么判断**  

- 市场为什么比 WU 主锚更暖或更冷
- 是否有一部分交易者正在看同栈但不同对象的页面

**不能拿它做什么**  

- 不能替代 rules 指定站点的 WU 页面
- 如果只是城市页，不能当主定价依据

### 2.4 Tier 4: 辅助校验源

**这是什么**  
机场级辅助源，例如：

- 官方航空气象页
- CheckWX METAR / TAF 机场页
- AccuWeather 机场页
- meteoblue 机场页
- 其他明确指向机场对象的天气页

**什么时候看**  
在确认主交易锚之后，再拿它们做交叉检查。

**能做什么判断**  

- WU 是否明显偏暖或偏冷
- 盘前中心带是否存在 1 到 2 档以上的明显分歧
- 峰值时间、升温路径、暖尾/冷尾是否存在额外风险
- 当前机场观测是否支持或反驳盘前高温路径

**不能拿它做什么**  

- 不能替代 rules 指定的结算源
- 不能因为某个辅助源更顺眼，就把主锚从同站点 WU 改掉
- 不能把实时 METAR 观测直接当成当日最高温结算值

**经验阈值**

- 如果辅助源基本围着主锚转：可以交易
- 如果分歧已经到 `2` 档左右：只适合做远尾 `No`
- 如果 exact 附近分歧很大：不要重仓 exact

### 2.5 Tier 5: 噪音源

**这是什么**  
城市页、非站点页、非机场对象页，或者任何无法确认对象是否与结算一致的页面。

**什么时候看**  
只在你想解释“为什么盘口会偏热/偏冷”时看一眼。

**能做什么判断**  

- 市场里是否存在错锚资金
- 为什么盘口短时间会朝一个不该去的方向偏

**不能拿它做什么**  

- 不能直接作为主定价依据
- 不能覆盖主锚判断
- 不能替代 rules 指定站点

一句话：**它们可以解释盘口，但不能决定你的下单。**

---

## 3. 数据源配置和获取方式

这一节回答两个实际问题：

1. 每类源应该怎么配
2. 这些页面在本地是直接 HTTP 抓，还是要上浏览器

### 3.1 建议的 source config 字段

如果后面要把页面抓取程序化，建议每个 source 至少维护这些字段：

- `source_family`
  例如 `wunderground`、`weather.com`、`accuweather`、`kma_amo`、`meteoblue`
- `page_role`
  例如 `settlement_truth`、`trading_anchor`、`consensus_explainer`、`sanity_check`、`pollution_anchor`
- `location_object`
  明确它看的是 `airport_station`、`airport`、`city` 还是 `metro`
- `station_required`
  是否必须和 rules 里的站点代码完全匹配
- `fetch_method`
  例如 `direct_http_html`、`direct_http_html_with_tls_override`、`browser_fallback`
- `validation_keys`
  抓到页面后必须核对的键，例如 title、canonical、station code、地点名
- `capture_targets`
  这页要记录哪些字段，例如 daily high、peak hour、finalized status
- `fallback_method`
  直接 HTTP 失败后，下一步是浏览器查看、人工复核，还是暂时停用

这些字段的目的不是让文档更像 schema，而是为了让后面真正写脚本时不用重新定义一套语言。

### 3.2 本地验证结果

以下结论来自本机在 **2026-03-12** 的实际抽样，不是记忆猜测。

#### WU History / Daily

- 本地结果：可直接 `HTTP GET` 到完整 HTML
- 已看到的稳定特征：
  - `<title>Incheon, South Korea Weather History | Weather Underground</title>`
  - `canonical` URL
  - 页面内可见 station 相关文本和 history 模块
- 推荐获取方式：`direct_http_html`
- 推荐用途：先做标题、站点、日期、finalized 状态和结算字段抽取

#### WU Hourly

- 本地结果：可直接 `HTTP GET` 到完整 HTML
- 已看到的稳定特征：
  - hourly 页面 canonical
  - `Hourly` 模块
  - 页面内嵌的同站点 forecast 内容和时间分段
- 推荐获取方式：`direct_http_html`
- 推荐用途：抓 `Tomorrow High`、小时峰值和峰值时段

#### weather.com

- 本地结果：可直接 `HTTP GET` 到完整 HTML
- 已看到的稳定特征：
  - `Hourly Weather` 标题
  - `daypartName`
  - `hourlyWxPhrase`
  - 多个已展开的 hourly detail block
- 推荐获取方式：`direct_http_html`
- 推荐用途：解释同栈市场共识，必要时抓 hourly 细节

#### AccuWeather 机场 POI

- 本地结果：机场 POI 路径可直接 `HTTP GET` 到完整 HTML
- 注意事项：
  - 错误的地点 ID 或旧路径可能返回 `503`
  - 机场 POI 路径要优先于泛城市路径
- 已看到的稳定特征：
  - airport canonical URL
  - `<title>Incheon International Airport ... | AccuWeather</title>`
  - `Place` 结构化数据和机场经纬度

#### CheckWX METAR / TAF

- 本地结果：可直接 `HTTP GET` 到完整 HTML
- 已看到的稳定特征：
  - `<title>METAR ... KORD</title>` 这类机场 METAR 标题
  - 页面内有机场 ICAO、原始 `METAR`、观测时间、风、能见度、云、温度、露点、气压
- 推荐获取方式：`direct_http_html`
- 推荐用途：
  - 做机场实时观测校验
  - 盘中确认当前温度爬升是否符合主锚路径
  - 当 AviationWeather 页面不顺手时，快速看机场 `METAR / TAF`
- 不推荐用途：
  - 它不是结算源
  - 它不是盘前主交易锚
  - 它只能帮助判断“当前机场状态”，不能单独决定最终 high 会落在哪一档
- 推荐获取方式：`direct_http_html`
- 推荐用途：外部第二意见，不做主锚

#### KMA AMO

- 本地结果：
  - 直接 HTTPS 请求先碰到证书链校验失败
  - 跳过本机 TLS 校验后，页面内容可正常取回
- 已看到的稳定特征：
  - `Aerodrome Weather - Aviation Meteorological Office`
  - `INCHEON Int'l Airport`
  - `RKSI` 相关机场页内容
- 推荐获取方式：`direct_http_html_with_tls_override`
- 推荐用途：官方机场口径 sanity check
- 风险说明：
  - 真正写程序时，不要默认长期 `verify=False`
  - 应优先补 CA bundle 或单独处理证书链

#### meteoblue

- 本地结果：可以拿到 HTML，但抽样中出现了地点错页
- 已见问题：
  - 请求的是 Incheon International Airport，返回内容却落到了其他地点页面
- 推荐获取方式：`browser_fallback`
- 推荐用途：趋势和不确定性辅助参考
- 风险说明：
  - 在没有核对 title / canonical / 地点对象前，不要信任何数值

### 3.3 程序抓取还是浏览器工具

当前最合理的路线不是二选一，而是分层：

#### 默认路径：先写程序抓

优先对这些源写小程序：

- `WU History / Daily`
- `WU Hourly`
- `weather.com`
- `AccuWeather 机场 POI`
- `KMA AMO`

原因很简单：

- 本地已证明它们大多能直接拿到 HTML
- 直接 HTTP 比浏览器更稳、更快、更便宜
- 这些页面先做 title / canonical / station 验证，再抽关键字段，已经足够实用

推荐技术路线：

- Python `requests` 或 `httpx`
- 固定桌面浏览器 `User-Agent`
- 跟随重定向
- 每个 source 单独写 parser，不做“大而全”的万能 parser

#### fallback：浏览器工具只在这些场景启用

- 页面返回错地点
- 关键字段只在交互后才出现
- 页面启用更强的反爬
- 需要截图留档
- 需要人工确认“这到底是不是机场对象页”

也就是说：

**默认先写程序抓，浏览器工具只做 fallback 和人工复核。**

### 3.4 抓取前必须验证什么

无论程序抓还是浏览器看，拿到页面后都先验证：

1. `title`
2. `canonical URL`
3. `station code / airport name`
4. `location_object` 是机场还是城市
5. `target_date`

这五项过不了，后面的温度值一律不进入主判断。

---

## 4. 默认交易 SOP

### Step 1: 先确认 rules

先确认：

- station
- unit
- resolution url
- finalized 说明
- rounding 说明

### Step 2: 先看同站点 WU Hourly

优先记录：

- 目标日 high
- 小时峰值
- 峰值大概出现在几点
- 是否出现日内明显上修或下修

### Step 3: 再看同站点 WU History / Daily

这一步不是为了盘前改锚，而是为了确认你盯的是同一个站点、同一个体系。  
等目标日结束后，它会变成最终结算和复盘依据。

### Step 4: 再做机场级交叉检查

看 1 到 3 个辅助机场源：

- 如果都围着主锚转：可以做
- 如果分歧已经接近 2 档：只做远尾 `No`
- 如果 exact 附近差异很大：宁可不做，也不要自信过度

### Step 5: 最后再看城市页

只回答一个问题：

**市场有没有可能被错锚页面带偏？**

如果答案是有，那就把它记成“污染源”，而不是把它升级成主锚。

---

## 5. 常见字段怎么理解

这些字段只适用于已归档的 manual source notes；生产城市/机场映射以 `weather-predict` 为准：

- `primary_settlement`：最终结算真值页
- `primary_trading_anchor`：盘前最该盯的主锚
- `consensus_sources`：帮助理解市场共识的页面
- `supporting_sources`：机场级交叉验证页面
- `forbidden_sources`：只能当污染源，不能定价
- `finalized_required`：是否必须等数据 finalized
- `object_type`：这个页面看的到底是机场、城市，还是别的对象

如果你是人类读者，不需要先记住所有字段名。  
先看每个城市的 `operator_notes.summary` 和 `usage_order`，再看具体 sources。

---

## 6. 维护规则

### 5.1 必须维护的内容

- 结算站点
- 结算页面归属
- 主交易锚页面归属
- 市场单位
- 时区
- 常见错锚来源

### 5.2 可以后补的内容

- 经纬度
- 更细的长期误差模型
- 历史盘口数据库
- 更细的季节性统计

### 5.3 什么时候要更新

- 新城市加入时
- Polymarket 改规则模板时
- 结算站点或单位变更时
- WU / weather.com / 辅助页面结构明显变化时

---

## 7. 一句话结论

**结算只认同站点 WU History / Daily；盘前主锚只看同站点 WU Hourly；机场辅助源只做校验；城市页只做错锚污染识别。**
