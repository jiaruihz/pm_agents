# weather_edge_v1

> doc_role: human_manual  
> doc_pair: `README.md` <-> `SKILL.md`  
> workflow_version: `v1`  
> sync_rule: 只要流程、输入输出、日志要求、黑名单或风控约束发生变化，必须同步修改这两个文件；只改一份视为文档失配。

这是 Weather Edge v1 的**人类总入口**。

Weather Edge v1 已经不再是纯 theta / carry 策略。当前主线是：

- 用 `weather-predict` 的 T-24 天气概率模型生成 bracket 概率；
- 和 Polymarket 当前盘口价格比较；
- 只有当 `model probability - market price` 达到 edge 阈值时生成信号；
- 在 `pm_agent` 内统一转成 trade plan，再并行支持 paper/live 执行。

旧的 theta/no 命名只作为历史名称保留在 archive 文档里，不再作为主策略名。

如果你想知道这套东西到底在做什么、应该先读哪份文档、哪些内容属于抓数据、哪些内容属于分析和监测，从这里开始。

## 这套体系分成哪几层

### 1. 抓数据层

只解决：

- 去哪里看
- 哪些页面是主锚
- 哪些页面只能做辅助
- 哪些页面是错锚污染源
- 页面怎么抓，直接 HTTP 还是浏览器 fallback
- 市场数据和 orderbook 应该走哪条项目内接口，避免手工读错

主文档：

- `[DATA_SOURCE.md](/home/rui/projects/pm_agent/src/strategies/weather_edge_v1/DATA_SOURCE.md)`
- `[city/CITY.md](/home/rui/projects/pm_agent/src/strategies/weather_edge_v1/city/CITY.md)`
- `city/*.yml`

### 2. 分析与监测层

只解决：

- 怎么把多源天气整理成盘前判断
- 怎么做盘中复核
- 怎么判断主区间漂移
- 怎么生成建议动作和告警
- 最后哪些事情必须由人拍板

主文档：

- `[DECISION_WORKFLOW.md](/home/rui/projects/pm_agent/src/strategies/weather_edge_v1/DECISION_WORKFLOW.md)`

### 3. 信号与交易计划层

只解决：

- 从 `weather-predict` 的 paper decision 导入不可变信号
- 去重，避免同一个 snapshot 重复下单
- 把信号转成 paper/live 共用的 trade plan
- 在进入 executor 前做本地 `SafetyGuard` 风控

关键文件：

- `tools/execution_pipeline.py`
- `scripts/ops/weather_signal_importer.py`
- `scripts/ops/weather_trade_planner.py`
- `runtime/weather_edge_v1/signals/signals.jsonl`
- `runtime/weather_edge_v1/plans/trade_plans.jsonl`

### 4. 策略执行层

只解决：

- 入场
- 仓位
- 止盈止损
- 撤单和平仓
- maker/taker 选择

这层主要在策略代码、ops 脚本和配置里。live 默认关闭，必须显式 `--live --confirm` 才能进入真实下单路径。

关键文件：

- `config/trading_profile.yml`
- `config/risk_profile.yml`
- `src/strategies/pmm/variants/weather_edge_v1.py`

## 先读什么

### 如果你是人

按这个顺序读：

1. `README.md`
2. `DATA_SOURCE.md`
3. `city/CITY.md`
4. `city/AIRPORT_CONTEXT.md`
5. 目标城市的 `city/<CITY>.yml`
6. `DECISION_WORKFLOW.md`

### 如果你是模型

模型入口仍然是 `[SKILL.md](/home/rui/projects/pm_agent/src/strategies/weather_edge_v1/SKILL.md)`。
但它现在也用普通语言写，不会故意写成只有模型能读懂的格式。

## 当前原则

这套体系当前默认是：

- **weather-predict 负责模型概率和 paper decision**
- **pm_agent 负责 signal import、trade plan、paper/live executor**
- **paper 默认打开**
- **live 默认关闭，必须经过人工显式确认和风控**

也就是说，当前重点是把“信号”和“执行”物理隔离。模型可以产生候选信号，但不能绕过 trade plan、风控和 live confirmation。

## 关键提示词设计

这一节不是写给模型执行器的冷指令，而是写给人看的“哪些约束必须明确告诉模型”。

如果你后面继续改这套系统，至少保留下面这些设计原则。

### 1. 先 fresh，再 compare

- 每次分析先基于**当前**市场快照和**当前**天气源独立做一遍判断。
- 只有在 fresh pass 完成后，才去看旧 case file 做对比。
- 旧记录只用于回答“这次和上次哪里变了”，不能直接继承旧结论。

### 2. 明确告诉模型：缺数据时宁可停，也不准补

- 天气盘里最危险的错误不是算错一度，而是**模型在关键数据缺失时自己编出一个高温**。
- 只要目标日期的同站点小时预报、结算页或关键观测拿不到，就应该输出：
  - `data_gap`
  - `manual_check`
  - `cannot_confirm`
- 不能让模型用“当前温度 + 历史经验 + 常识”去脑补最高温。

### 3. 每次都要写时间和延迟说明

- 不只写“看了哪个网站”，还要写：
  - 抓取时间
  - 页面内最新更新时间
  - 观测时间
  - 这个源是不是天然滞后
- 如果缺少这些时间信息，后面复盘时就分不清到底是判断错，还是数据本身慢了。

### 4. 让模型先做源分级，再做结论

- 不要上来就问“该不该持有”。
- 先让模型判断：
  - 哪个是主锚
  - 哪个只是辅助
  - 哪个是污染源
- 只有源分级明确了，后面的交易判断才可信。

### 5. 让模型显式写出“不确定”

- 如果主锚缺失、对象没确认、来源互相冲突，输出里必须允许出现：
  - `不确定`
  - `需要人工复核`
  - `不能自动下结论`
- 不要把提示词写成“无论如何都要给结论”，这会强迫模型胡编。

## 潜在盲点

### ⚠️ 缺乏对幻觉的硬防御

这是当前天气分析里最需要长期盯住的问题。

- 当 `WU Hourly`、同站点结算页、或目标日期关键小时数据拿不到时，模型**很容易**根据：
  - 当前温度
  - 机场地理常识
  - 历史经验
  - 市场价格
  去推断一个“看起来合理”的最高温。
- 这种推断在表达上常常很像真数据，最危险。

所以这套体系必须长期坚持一个原则：

- **没有抓到的数据，宁可明确写“拿不到”，也不能补。**

如果以后你看到分析里出现这种说法，要立刻怀疑是否已经越界：

- “虽然小时预报没拿到，但看当前温度应该最高到 14C 左右”
- “结合历史经验，今天大概率会在 13-14C”
- “从当前升温速度推算，最高温应该还能再涨 1 度”

这些话可以作为**假设**讨论，但不能伪装成“已经抓到的数据”。

## 当前目录地图

### 入口文档

- `[README.md](/home/rui/projects/pm_agent/src/strategies/weather_edge_v1/README.md)`：人类总入口
- `[SKILL.md](/home/rui/projects/pm_agent/src/strategies/weather_edge_v1/SKILL.md)`：模型入口，也尽量保持人类可读

### 抓数据文档

- `[DATA_SOURCE.md](/home/rui/projects/pm_agent/src/strategies/weather_edge_v1/DATA_SOURCE.md)`：跨城市通用源规则和获取方式
- `[city/CITY.md](/home/rui/projects/pm_agent/src/strategies/weather_edge_v1/city/CITY.md)`：城市摘要入口
- `[city/AIRPORT_CONTEXT.md](/home/rui/projects/pm_agent/src/strategies/weather_edge_v1/city/AIRPORT_CONTEXT.md)`：机场地理位置、微气候特性和执行提醒
- `city/*.yml`：城市 canonical source config

### 分析文档

- `[DECISION_WORKFLOW.md](/home/rui/projects/pm_agent/src/strategies/weather_edge_v1/DECISION_WORKFLOW.md)`：监测、分析、告警和人工决策流程

### 工具与实现

- `[tools/airport_weather_tool.py](/home/rui/projects/pm_agent/src/strategies/weather_edge_v1/tools/airport_weather_tool.py)`：当前 watch 基线工具
- `[tools/market_query_tool.py](/home/rui/projects/pm_agent/src/strategies/weather_edge_v1/tools/market_query_tool.py)`：天气 skill 的市场查询入口，统一走内部 Gamma/CLOB client，不直接 `curl`
- `[tools/profile_resolver.py](/home/rui/projects/pm_agent/src/strategies/weather_edge_v1/tools/profile_resolver.py)`：将天气输入转成 `daily_overrides` 和 `action_suggestion`
- `[tools/codex_weather_advisor.py](/home/rui/projects/pm_agent/src/strategies/weather_edge_v1/tools/codex_weather_advisor.py)`：漂移后的人类辅助解释
- `[tools/execution_pipeline.py](/home/rui/projects/pm_agent/src/strategies/weather_edge_v1/tools/execution_pipeline.py)`：signal import 与 trade planner 的核心逻辑

### 运行产物

- `[plan/watch/](/home/rui/projects/pm_agent/src/strategies/weather_edge_v1/plan/watch)`：按日期滚动的 watch 文件
- `plan/cases/<CITY>/<DATE>.md`：按 `city + date` 滚动追加的人类可读案例记录
- `runtime/weather_decision_journal.db`：天气决策日志

## 当前不做什么

当前文档体系**不把模型定义成自动交易员**。

原因很直接：

- 天气源仍在整理中
- 页面对象很容易看错
- 某些站点仍有证书、反爬、错页等问题
- 这时把最后下单权交给程序，工程上不稳

等抓取层和分析层足够稳定之后，再决定要不要给执行层更多自动化权限。
