# weather_edge_v1 技能说明

> doc_role: machine_directive  
> doc_pair: `README.md` <-> `SKILL.md`  
> workflow_version: `v1`  
> sync_rule: 如果流程、输入输出、日志要求、黑名单或风控约束变化，必须同步修改这两个文件；不同步视为严重违规。

这份技能文档是**模型入口**，但它也保持人类可读。

它的职责不是替代 `README`，而是告诉模型：

- 什么时候应该调用这套天气文档
- 先读哪几份文件
- 哪些事情属于抓取
- 哪些事情属于分析
- 哪些事情仍然必须由人决定

当任务是下面这些类型时，使用这套技能：

- 确认当前生产城市/机场映射应以哪个项目为准
- 判断旧手工机场研究是否还能提供定性辅助
- 手工抓取或半自动抓取天气页面，用于 source audit 或复盘
- 整理某个城市的 manual source notes，但不能直接覆盖生产配置
- 更新 watch 基线、做盘中复核、做结算复盘

## 先读什么

按这个顺序读：

1. `[README.md](/home/rui/projects/pm_agent/src/strategies/weather_edge_v1/README.md)`
   先看这套体系的总结构。
2. `[airport-selection-current.md](/home/rui/projects/weather-predict/docs/airport-selection-current.md)`
   先看当前生产城市、机场、单位、排除项和 source caveat。
3. `/home/rui/projects/weather-predict/pm_edge_compare.py::CITIES`
   这是当前 paper snapshot 和模型计算使用的生产映射。
4. `[WEATHER_EXECUTION_ARCHITECTURE.md](/home/rui/projects/pm_agent/docs/WEATHER_EXECUTION_ARCHITECTURE.md)`
   再看 weather-predict 与 pm_agent 的边界。
5. `[archive/manual_airport_research/README.md](/home/rui/projects/pm_agent/src/strategies/weather_edge_v1/archive/manual_airport_research/README.md)`
   只在需要回看旧手工机场源研究时阅读。
6. `[DECISION_WORKFLOW.md](/home/rui/projects/pm_agent/src/strategies/weather_edge_v1/DECISION_WORKFLOW.md)`
   最后看分析、监测、告警和人工决策流程。

如果只想先知道结论，优先看：

- `operator_notes.summary`
- `operator_notes.main_risks`
- `operator_notes.common_wrong_anchor`
- `operator_notes.usage_order`

## 这套技能的边界

当前默认定位是：

- 模型负责抓取
- 模型负责整理和分析
- 模型负责监测和告警
- 人负责最后决策
- 生产城市/机场选择由 `weather-predict` 维护，旧手工配置只能作为归档参考

也就是说，当前这不是“自动交易 skill”，而是“抓取 + 分析 + 监测 + 人类在环”的 skill。

## 硬约束

下面这些不是建议，而是必须遵守的执行约束。

### 1. 禁止编造天气数值

- 如果没有抓到目标日期、目标站点、目标对象的真实页面或真实结构化数据：
  - 不得生成 `reported_high`
  - 不得生成 `peak_hour`
  - 不得生成“今天最高温大概会是 X”这类伪数据结论
- 不得使用以下替代路径去脑补数值：
  - 当前温度 + 常识
  - 当前温度 + 历史经验
  - 当前温度 + 城市页趋势
  - 当前温度 + 市场价格

### 2. 缺数据时必须降级

满足任一条件时，必须明确输出“数据缺口”而不是继续硬推：

- 同站点 `WU Hourly` 没拿到
- 同站点结算页对象未确认
- 目标日期不在页面里
- 页面对象像城市页而不是机场页
- 辅助源和主锚冲突，但没有足够证据判定谁错

此时允许的动作只有：

- `manual_check`
- `review_only`
- `cannot_confirm`
- `data_gap`

### 3. 先 fresh pass，后 compare pass

- 每次先基于当前抓到的数据独立分析。
- 只有 fresh pass 完成后，才能读取旧 case file。
- 读取旧 case file 的目的只能是：
  - 提取上次结论
  - 对比变化
  - 记录 delta
- 不得把旧 case file 的结论直接复用为本次结论。

### 4. 数据时效纪律与防旧值原则

- **必须带时间戳**：任何当前最新温度值，必须附带：
  - 采集时间戳（Fetch Time）
  - 数据源自身标称的观测时间戳（Obs Time）
- **源冲突优先级**：当多个源出现冲突，严禁简单平均或靠常识脑补。**永远以时间戳更新的一级源（如航空气象厅、官方机场页）为准**。旧的二级源抓取记录只能用于解释历史路径。

### 5. Exact 盘特别硬规则（三维核验法）

如果盘口是 Exact (例如：最高温是否 exactly `10°C`)，必须强制按以下顺序核查，消除凭感觉的 late surge 判断：
1. **今日已印否**：必须先查“今天该站点是否已经打印过该目标整数”。只要印过一次，No 面临实质性失败风险。
2. **侦测同站小数门槛**：绝不能只盯着整数 METAR（例如 10°C 有时只是 9.5-9.8°C 的进位）。只要能找到，必须寻找 `AMOS / AWS / 官方机场页` 的真实小数点，核实该整数是“薄整数”（如 9.5）还是“厚整数”（如 10.4），判断暖端小数是否已逼近打印门槛。
3. **不得用 TAF 静态抵消实时 Risk**：即使 TAF / Hourly Forecast 预报后段天气会变坏（阵雨/大风/云增多），只要此坏天气尚未真正落地，且距离日内典型高温窗口结束仍有 60-90 分钟，模型就**绝不能**认为 late surge（晚拉升）风险已解除。实时气温路径的优先级永远高于静态预报！

### 6. 强制读取气象观测三要素 (METAR Physics)

真正的交易 Edge 来自于对物理环境的诊断，而不仅仅是看温度刻度。在读取任何 METAR 或 Hourly 数据时，**必须**同步抓取并解析以下三大机制要素：
- **风场 (Wind)**：必须查阅风向 (Dir) 和风速 (Speed)。风向决定了气团来源（海风带来压制与平台；陆风下沉带来干热激增；风速的急剧变大通常意味着锋面或混合层打通）。
- **湿度与露点 (Dew Point / Humidity)**：露点的骤降和湿度的极化，是促成干空气卷入、导致温度脱离常规曲线出现脉冲式峰值（Late Surge）的核心推手。
- **云量 (Cloud Cover)**：必须明确天空中是 `SKC/CLR/FEW`（暴晒，随时有 Surge 动能）还是 `OVC/BKN`（温度压制，物理封顶）。
**禁止**在未评估风/湿/云的情况下，盲目根据历史经验推断最高温。

### 7. 终极气象量化执行引擎 (V4 判决树)

当执行精确的天气盘口打分和操作建议时，必须强制内化以下引擎逻辑作为思考模板：

**Step 1. 宏观极值与物理背景对齐 (Fact Check)**
- 强制提取官方预报锚点：读取 TAF 的 `TX` (最高温预报值)，以及获取今日历史真实极值 (Historical High)。
- 评估云量突变的边际效用（如 SCT->FEW），不要把高云/中云的短暂变化夸大为绝对的升温引擎。
- 在无强暖平流风系（如缺乏 >15KT 南风/焚风）时，确认升温只能依靠当前太阳短波辐射的剩余时长。

**Step 2. 动能变量比对 (Delta Check)**
不再笼统地使用“温度横盘/滞胀”，必须拆分当前温度与日高的物理关系：
- **【衰退型退潮】**：当前温度 < 日内最高实况 (例如之前冲到14，现在跌回13并横盘 半小时以上)。物理证据表明动能在瓦解。
- **【抗压型高位】**：当前温度 = 日内最高实况 (例如之前是15，现在仍死死稳在15)。物理证据表明多空在僵持。

**Step 3. 物理极值决策树 (Physics Deduction & Execution)**
基于前两步，匹配以下三种状态并输出最终交易策略：
- **🟢【前沿拉锯区 (Tug-of-war)】（警告/防守单）** 
  => 命中：(当前处于【抗压型高位】) AND (官方预测仍存高点，或距离日落尚有 2 小时以上)。
  => 逻辑：虽然受阻，但多头高地未失，大自然仍握有随时冲刺的合法通关文牒。
  => 执行：对于互斥盘口，远端 NO 可充当常规胜率头寸，但**严禁重仓当做 Theta 提款机**，必须睁着眼睛睡觉防范假死反扑。
- **🔴【物理绝杀死锁 (Theta 坟场)】（强力狙击单）** 
  => 命中：(出现明显的【衰退型退潮】) OR (时间进入日落前1-1.5小时的纯衰退期) OR (降临浓云/降水等物理封顶)。
  => 逻辑：净辐射即将转负，且实际温度防线开始向后撤退，最高极值已被物理学锁死。
  => 执行：大盘已定，开启重仓收网。对于 Mutually Exclusive (互斥分类) 整数盘，如果在最高温已跌落时，直接买入被锁定的 **历史极值 YES** 往往能获取极致收益爆炸；或者稳健买入高一档的 **远端 NO** 吃透时间价值。
- **🟡【混沌期观望】** 
  => 命中：早盘积累期 (<11:00) 或气象要素呈反向背离。
  => 执行：空仓等待下一份起爆期 METAR。

## 抓取工作流

### 1. 先认 rules

每次先确认：

- 结算站点
- 结算网站体系
- 市场单位
- 看的指标是最高温还是最低温

然后必须补一句机场背景判断：

- 这个机场是海边、内陆、湖边、盆地还是山前
- 哪种局地机制最容易让今天的 peak 少一档或多一档

如果这一步没做，后面很容易把城市页温度路径错套到机场对象。

### 1.5 先固定 market 数据入口

在这套 skill 里，**Polymarket 市场数据不能再直接 `curl` 公共接口**。

原因：

- 手工 `curl` 容易临时挑错 endpoint
- event / market / token 三层字段很容易混
- exact-bin 的 `bestBid` / `bestAsk` / token orderbook 很容易读串

查 market 时，默认走项目内接口：

1. `scripts/ops/weather_market_snapshot.py`
2. `src/strategies/weather_edge_v1/tools/market_query_tool.py`
3. `src/strategies/rule_lawyer/services/market_resolver.py`
4. `src/platform/clients/clob.py`

默认命令：

```bash
python scripts/ops/weather_market_snapshot.py \
  --target-market "https://polymarket.com/event/highest-temperature-in-paris-on-march-15-2026" \
  --include-orderbook true \
  --orderbook-top-n 10
```

输出里至少要取：

- `event.title / slug / description`
- `markets[].question / outcomes / outcome_prices`
- `markets[].best_bid / best_ask / last_trade_price`
- `markets[].tokens[].token_id`
- `markets[].tokens[].orderbook`

如果 market 数据没先走这条入口，就不要继续做天气判断。

### 2. 再选抓取方式

默认不要一上来就开浏览器自动化。

优先级是：

1. 直接 HTTP 抓页面 HTML
2. 解析 title / canonical / station 标识 / 关键温度字段
3. 如果页面错位、字段缺失、被拦截，再切到浏览器查看

当前本地验证结果，按 `2026-03-12` 的抽样：

- `WU History / Daily`：可直接 HTTP 获取 HTML
- `WU Hourly`：可直接 HTTP 获取 HTML
- `weather.com Hourly`：可直接 HTTP 获取 HTML，且页面中已有可匹配的 hourly DOM
- `AccuWeather 机场 POI`：可直接 HTTP 获取 HTML，但路径必须用机场 POI，错误路径可能返回 `503`
- `KMA AMO`：页面本身可取，但本机对其证书链校验失败；脚本里需要显式处理 CA 或 TLS verify
- `meteoblue`：可直接拿到 HTML，但本地抽样曾返回错页，必须二次核对 title / canonical / 地点对象

结论：

- **先写程序抓 `WU / weather.com / AccuWeather / KMA`**
- **浏览器工具只做 fallback，不做默认路径**

### 3. 什么时候使用浏览器操作工具

虽然我们不再依赖“用户手动发截图”，但**模型/系统自带的浏览器自动化工具**仍然是极其核心的获取手段之一。在以下情况，可以直接或降级使用浏览器工具：

- 纯 HTTP 抓取难以绕过站点的反爬/风控体系（如 503 拦截）。
- 页面是高度动态渲染的 SPA，或必须点击展开折叠块（如 hourly 详情）才能看到关键数值。
- HTTP 返回的对象经常漂移（如返回了城市页而非机场页），需要通过浏览器 DOM 结构或视觉校验来确认目标站点准确性。
- 作为验证疑难杂点（如特殊小数点数据获取）的备用手段。

由于天气页面的复杂性，浏览器操作工具是保证数据准确性的重要一环，应将其作为强大的可选项。

## 推荐的数据抓取接口总结

如果要写程序，为了不干扰决策核心，抓取回来的状态只需固定传递这 5 个核心状态变量：

1. `station_id` (ICAO/WMO 或明确的站点标识)
2. `current_temp_with_decimals_if_avail` (尽量去找带小数点的真实观测)
3. `today_highest_printed` (今日该站已打印的高点)
4. `local_obs_time`
5. `fetch_time`

## 分析入口

当 source capture 完成后，分析流程看：

- `[DECISION_WORKFLOW.md](/home/rui/projects/pm_agent/src/strategies/weather_edge_v1/DECISION_WORKFLOW.md)`

分析层的目标是输出：

- 当前主锚中心
- 多源分歧
- 风险 flags
- `action_suggestion`
- 必须人工确认的点

## 当前自动化工具的定位

现有本地工具仍然有用，但它们不是这套 source stack 的主抓取器：

- `[airport_weather_tool.py](/home/rui/projects/pm_agent/src/strategies/weather_edge_v1/tools/airport_weather_tool.py)`
  当前基于 `AviationWeather + Open-Meteo`，适合 baseline/watch 流程，不等于 WU/TWC 主锚。
- `[profile_resolver.py](/home/rui/projects/pm_agent/src/strategies/weather_edge_v1/tools/profile_resolver.py)`
  在你已经拿到天气输入之后，输出 `daily_overrides` 和 `action_suggestion`。
- `[codex_weather_advisor.py](/home/rui/projects/pm_agent/src/strategies/weather_edge_v1/tools/codex_weather_advisor.py)`
  适合在漂移告警后做二次解释，不适合替代数据抓取。

## 运行时最常见输入

如果你在做盘前或盘中判断，至少准备这些输入：

- `city_key`
- `local_date`
- `baseline_forecast`
- `latest_forecast`
- `latest_observation`
- `latest_orderbook`

其中：

- `baseline_forecast` / `latest_forecast` 最好来自同站点主锚或明确标注来源
- `latest_observation` 要区分机场观测和城市观测
- `latest_orderbook` 至少带 `best_bid` / `best_ask`

## 交易侧短规则

这套策略更适合做规整尾部 `NO`，不适合去硬碰天气结构不稳定的过渡日。

当前短规则不变：

- 开仓前优先要求 forecast 与目标桶位至少有 `2` 度距离
- edge 只有 `1-2` 度时默认降仓
- 优先挂 maker，不主动吃 ask
- 如果 forecast 正在向目标桶回归，不在后段继续加仓
- 如果持仓后 forecast edge 压缩到约 `1` 度以内，把它当成 weather-driven exit

## 目录地图

- `[README.md](/home/rui/projects/pm_agent/src/strategies/weather_edge_v1/README.md)`：人类总入口
- `[DATA_SOURCE.md](/home/rui/projects/pm_agent/src/strategies/weather_edge_v1/DATA_SOURCE.md)`：跨城市通用源规则和获取方式
- `[airport-selection-current.md](/home/rui/projects/weather-predict/docs/airport-selection-current.md)`：当前生产城市/机场映射
- `[archive/manual_airport_research/README.md](/home/rui/projects/pm_agent/src/strategies/weather_edge_v1/archive/manual_airport_research/README.md)`：已归档的旧手工机场研究入口
- `archive/manual_airport_research/city/*.yml`：旧 manual source notes，不是生产配置
- `[DECISION_WORKFLOW.md](/home/rui/projects/pm_agent/src/strategies/weather_edge_v1/DECISION_WORKFLOW.md)`：分析、监测、告警和人工决策流程
- `[tools/airport_weather_tool.py](/home/rui/projects/pm_agent/src/strategies/weather_edge_v1/tools/airport_weather_tool.py)`：当前 watch 基线工具
- `[plan/watch/](/home/rui/projects/pm_agent/src/strategies/weather_edge_v1/plan/watch)`：按日期滚动的 watch 文件
