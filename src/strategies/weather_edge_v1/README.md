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

### 1. 数据与模型层

只解决：

- 当前生产跑哪些城市、机场和单位
- 哪些城市应该排除出策略统计和 live rollout
- weather probability、snapshot、paper decision 从哪里来
- 历史天气和 forecast cache 的来源、覆盖范围和 caveat
- 市场数据和 orderbook 应该走哪条项目内接口，避免手工读错

权威来源：

- `[airport-selection-current.md](/home/rui/projects/weather-predict/docs/airport-selection-current.md)`
- `/home/rui/projects/weather-predict/pm_edge_compare.py::CITIES`
- `[weather_predict_integration.yml](/home/rui/projects/pm_agent/src/strategies/weather_edge_v1/config/weather_predict_integration.yml)`

旧的手工机场源研究已经归档，只能作为定性参考，不能作为当前生产城市/机场配置：

- `[archive/manual_airport_research/README.md](/home/rui/projects/pm_agent/src/strategies/weather_edge_v1/archive/manual_airport_research/README.md)`

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

这层主要在策略代码、ops 脚本和配置里。live 默认关闭，必须显式 `--live --confirm-live` 才能进入真实下单路径。

关键文件：

- `config/weather_edge_v1.yml`
- `tools/execution_pipeline.py`
- `pmm_adapter.py`

Weather Edge 自己的配置、数据源、文档、工具和 PMM adapter 都收口在 `src/strategies/weather_edge_v1/`。`pmm/` 只保留 PMM 做市策略和共享执行引擎；Weather Edge 可以复用 PMM engine，但策略归属不放在 `pmm/variants`。

## 先读什么

### 如果你是人

按这个顺序读：

1. `README.md`
2. `[airport-selection-current.md](/home/rui/projects/weather-predict/docs/airport-selection-current.md)`
3. `[WEATHER_EXECUTION_ARCHITECTURE.md](/home/rui/projects/pm_agent/docs/WEATHER_EXECUTION_ARCHITECTURE.md)`
4. `[weather_edge_v1_todo.md](/home/rui/projects/pm_agent/src/strategies/weather_edge_v1/plan/weather_edge_v1_todo.md)`
5. `[archive/manual_airport_research/README.md](/home/rui/projects/pm_agent/src/strategies/weather_edge_v1/archive/manual_airport_research/README.md)`，仅在需要回看旧手工机场源研究时阅读

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

## 当前状态（2026-05-11）

### 已经跑通

- N100 远端的 `weather-predict` 已经用 systemd timer 每 30 分钟跑 `paper_snapshot.py`。
- `weather-predict` 已经持续写入 snapshot 和 paper ledger。
- 15 城历史数据已补齐到约 736 天量级；其中 Seoul 因历史校准表现差，策略研究和出单默认排除。
- 当前生产城市/机场选择以 `weather-predict` 为准；本仓库旧 `city/*.yml` 研究已归档，不能覆盖生产映射。
- 当前 paper ledger 已经产生多日订单；2026-05-10 有 44 单，其中部分亚洲市场已经可以用实况代理粗算。
- `pm_agent` 已经有统一的信号导入、trade plan、paper executor 和可选 live executor。
- `weather_edge_v1` 已经从旧 `weather_theta_no_v1` 命名中独立出来，旧命名只作为 archive 历史保留。

### 还没正式打开

- 真实 live 下单没有在远端定时运行。
- live 代码路径已经接上，但必须先做一笔小额 smoke test：提交限价单、记录回执、立即撤单。
- 结算分析目前主要依赖 Polymarket settled cache；如果当日 cache 还没被 daily pipeline 拉到，只能先用 IEM/WU proxy 做临时估算。
- 当前历史观测源标记为 `obs_source_v1_iem_proxy`；后续仍需用真实 WU 或 Polymarket final settlement 做交叉验证。

### 当前不应误解的点

- 现在正在生产运行的是 paper 观测链路，不是 live trading bot。
- paper 单和 live 单必须共用同一份 trade plan；不能再维护两套规则。
- paper 结果在正式 settled cache 到来前只能算初步结果。
- 当前策略不是纯 theta/carry，而是天气模型概率相对盘口价格的 edge 策略。

## 后续研究路线

### P0：继续积累 paper 样本

目标是至少积累 3-5 天、150-300 单 paper 样本，其中 80-150 单有正式结算结果。每天检查：

- 订单数、成本、PnL、ROI、最大回撤
- 分城市、分 YES/NO、分模型、分时间窗口
- 当天未结算、缺 settlement cache、缺 bracket 的数量

### P1：验证 edge 是否稳定

- 按 edge bucket 分桶：10%-15%、15%-20%、20%-30%、30%+
- 检查 BUY_YES 和 BUY_NO 是否表现显著不同
- 排查城市级偏差，继续确认 Seoul 这类异常城市是否应排除
- 比较 `gfs` / `ecmwf` 的贡献和失误类型

### P2：检查价格和流动性

- 比较 snapshot price、last trade、best ask、实际可成交价
- 估算滑点和盘口深度，确认 paper 用价是否过于乐观
- 检查同城多 bracket 同时出单时的相关性和总风险

### P3：做概率校准

- 用正式结算样本校准 `model_prob - market_price`
- 找出长期高估或低估的城市/模型/温度区间
- 决定是否提高最小 edge 阈值，或只保留某些城市/方向

### P4：live smoke test

在 paper 链路稳定后执行：

- 单笔不超过 1 USD
- planner 必须加 `--enable-live`
- executor 必须加 `--live --confirm-live --cancel-after`
- 验证下单、回执、ledger、撤单和错误处理

### P5：paper/live 并行

live smoke test 通过后，再考虑让 paper 和 tiny live 并行跑。初期应设置：

- 单笔小额上限
- 每日 notional 上限
- 每日亏损上限
- kill switch
- 每日 paper/live 差异报告

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

### 数据与模型文档

- `[airport-selection-current.md](/home/rui/projects/weather-predict/docs/airport-selection-current.md)`：当前生产城市/机场映射和选择原则
- `[weather_predict_integration.yml](/home/rui/projects/pm_agent/src/strategies/weather_edge_v1/config/weather_predict_integration.yml)`：两项目的数据边界和同步点
- `[archive/manual_airport_research/README.md](/home/rui/projects/pm_agent/src/strategies/weather_edge_v1/archive/manual_airport_research/README.md)`：已归档的旧手工机场研究入口

归档目录里的 `city/*.yml`、`station_profile.yml`、`risk_profile.yml`、`trading_profile.yml` 不是生产配置。它们只用于解释旧研究过程或为未来 source audit 提供线索。

### 分析文档

- `[DECISION_WORKFLOW.md](/home/rui/projects/pm_agent/src/strategies/weather_edge_v1/DECISION_WORKFLOW.md)`：监测、分析、告警和人工决策流程

### 工具与实现

- `[tools/airport_weather_tool.py](/home/rui/projects/pm_agent/src/strategies/weather_edge_v1/tools/airport_weather_tool.py)`：当前 watch 基线工具
- `[tools/market_query_tool.py](/home/rui/projects/pm_agent/src/strategies/weather_edge_v1/tools/market_query_tool.py)`：天气 skill 的市场查询入口，统一走内部 Gamma/CLOB client，不直接 `curl`
- `[tools/profile_resolver.py](/home/rui/projects/pm_agent/src/strategies/weather_edge_v1/tools/profile_resolver.py)`：将天气输入转成 `daily_overrides` 和 `action_suggestion`
- `[tools/codex_weather_advisor.py](/home/rui/projects/pm_agent/src/strategies/weather_edge_v1/tools/codex_weather_advisor.py)`：漂移后的人类辅助解释
- `[tools/execution_pipeline.py](/home/rui/projects/pm_agent/src/strategies/weather_edge_v1/tools/execution_pipeline.py)`：signal import 与 trade planner 的核心逻辑
- `[pmm_adapter.py](/home/rui/projects/pm_agent/src/strategies/weather_edge_v1/pmm_adapter.py)`：接入 PMM tick engine 的 adapter

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
