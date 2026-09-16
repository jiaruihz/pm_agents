# PAP v1.0 规划输入与评审

本文包含两部分：
1) 原文记录（用户提供，未改动）
2) 评审与建议（策略可靠性、执行方案补充、改进建议）

## 一、原文记录（用户提供）

预测市场 Alpha 框架 (The Prediction Market Alpha Framework)
核心哲学： 我们不预测未来。我们寻找“市场价格”与“客观约束”之间的结构性错配 (Structural misalignment)。

🧱 第一层：市场物理学 (The Physics - 底层机制约束)
核心问题： 战场的物理定律是什么？什么是不可能的？
核心要素	机制描述	战略推论 (Our Edge)
1. 二元归一性 (Binary Finality)	所有资产最终只会是 $1 或 $0。中间状态是暂时的。	这是负和博弈（含手续费）。利润 100% 来自对手盘的永久性亏损，而非资产增值。必须以“清零风险”为前提思考。
2. 时间价值的非线性 (Non-linear Time Value)	资金机会成本随时间指数级上升。$1 锁定一年的成本远高于锁定一周。	APY 是唯一指标。 必须剥离“时间溢价”。利用对手盘的“不耐烦”来低价收购长期高确定性资产（Yield Farming）。
3. 预言机裁决权 (Oracle Sovereignty)	UMA (或特定信源) 的裁决 > 客观事实。“真理”是被定义的。	交易标的是“定义”，而非“事实”。 Alpha 来源于“大众对事实的理解”与“预言机对定义的执行”之间的偏差（Rules Lawyering 的物理基础）。

🧬 第二层：市场生物学 (The Biology - 参与者缺陷)
核心问题： 对手盘是谁？他们为什么持续送钱？
核心要素	缺陷描述	剥削路径 (Exploitation Path)
1. 散户/赌徒 (Degens)	将预测市场视为彩票/赌场。追求高赔率，忽视基础概率。	Favorite-Longshot Bias (最爱-长标偏差): 系统性高估小概率事件（Longshot）。Action: 批量做空高估的垃圾盘（Bet NO on <5%）。
2. 信仰者/意识形态者 (Ideologues)	基于愿望（Wishful Thinking）而非逻辑下注。对负面信息免疫。	信仰溢价 (Faith Premium): 无论基本面多差，价格有硬底（如某些政治/Meme人物）。Action: 在信仰崩塌前夕做空，或利用溢价做对冲。
3. 懒惰者/标题党 (Headline Readers)	只看标题，不读细则 (Rules) 和定义。线性外推最近的新闻。	认知偏差 (Cognitive Gap): 认为“新闻说的”等于“合约赔付的”。Action: 利用细则中的“互斥条款”进行降维打击（如 $STAR 局）。

⚔️ 第三层：核心战略论 (The Strategy Doctrines - 获利方法论)
核心问题： 基于上述机制和缺陷，我们如何系统性收割？
战略代号	核心逻辑 (Why it works)	典型猎物特征	执行要点 (Execution Key)
S1. 规则律师 (Rules Lawyering)	利用认知差。 大众的模糊理解 vs. 合约的精确定义。赚“阅读理解”的钱。	商业品牌互斥（Ticker）、严格法律定义（宣战/衰退）、僵尸立法（过不了委员会）。	验尸官思维： 先寻找“怎么才能死（判NO）”的硬条款。确认无后门后再动手。
S2. 时间收租 (Duration Arb / Yield Farming)	利用时间差。 大众的急躁 vs. 物理/行政流程的缓慢。赚“耐心”的钱。	物理定律锁死的长周期局（登月/火星）、复杂的官僚流程（基建/审批）。	持有到底或时间止盈： 不要在中途因噪音波动离场。只在逻辑兑现或年化收益率下降时平仓。
S3. 量化套利 (Quant / Computational Arb)	利用计算力差。 大众的直觉概率 vs. 数学上的贝叶斯/相关性。赚“模型精度”的钱。	条件概率倒挂（$P(A\|B) < P(A$）、强相关标的价差异常、跨平台套利。	Agent 监控： 必须依赖自动化脚本发现和执行。拼的是发现速度而非逻辑深度。

🛡️ 第四层：执行控制论 (The Execution Protocol - 生存法则)
核心问题： 如何确保在收割时不被黑天鹅炸死？
控制要素	执行法则	备注
1. 仓位管理 (Position Sizing)	四分之一凯利 (Quarter Kelly) 或固定比例法（<5-8%）。	永远假设存在 0.1% 的“上帝风险”（平台跑路/预言机被黑）。单注决不 All-in。
2. 进场纪律 (Entry Discipline)	仅在 My Edge > Safety Margin 时行动。	设定明确的 Edge 阈值（如：S级策略需 >40% Edge）。不符合不进场，宁可踏空。
3. 熔断机制 (Kill Switch)	预设“证伪信号 (Falsification Signal)”。	信号一旦触发（如“SpaceX 官宣使用 $STAR”），无视价格，立即无脑平仓。拒绝幻想。
4. 工具武装 (Tooling)	建立个人信息情报系统 (Agent/Bot)。	监控源：立法官网、SEC Edgar、FAA 公告、特定信源推特 API。信息速度 = Alpha。

🏗️ Polymarket Alpha Pipeline (PAP) v1.0 架构规范
1. 系统概览 (System Overview)
设计目标： 构建一个自动化流水线，从 Polymarket 海量数据中筛选出具备“结构性套利”机会（S1/S2/S3 策略）的标的，并自动生成深度决策简报，供人类或高级模型（Gemini Ultra/GPT-4o）做最终裁决。
核心哲学：
1. 状态机驱动 (State-Machine Driven): 数据库是唯一的真实来源（Single Source of Truth）。
2. 漏斗式过滤 (Funnel Filtering): 用 SQL 和规则杀掉 95% 的无效盘口，仅对高价值盘口消耗 Token。
3. 动静分离 (Hot/Cold Data Separation): 价格/流动性是热数据（实时更新），规则解析是冷数据（一次性生成），通过 ID 关联。

2. 核心实体与数据库设计 (Data Schema)
使用 SQLite。我们需要三张表来支撑整个生命周期。
2.1 raw_markets (主表：热数据与状态)
负责维护赌局的最新状态、价格和生命周期。
字段名	类型	说明
market_id	TEXT (PK)	Polymarket ID (唯一键)
title	TEXT	标题 (冷数据)
rules	TEXT	规则全文 (冷数据)
end_date	DATETIME	结算时间 (冷数据)
last_trade_price	REAL	[热] 最新成交价 (YES Price)
volume	REAL	[热] 总交易量
liquidity	REAL	[热] 订单簿深度指标
status	VARCHAR	生命周期状态 (详见第3节)
updated_at	DATETIME	上次 Fetch 更新时间
2.2 market_rule_parses (智库表：冷数据)
存储 Node C (Parser) 的 LLM 解析结果。一次写入，极少修改。
字段名	类型	说明
id	INTEGER (PK)	自增
market_id	TEXT (FK)	关联主表
prompt_version	TEXT	Prompt 版本号 (e.g., "v1.0")
strategy_tag	TEXT	'S1', 'S2', 'S3', 'NOISE'
alpha_score	INTEGER	0-100 打分
hard_constraints	JSON	提取出的排除项、截止时间、前置条件
search_keywords	JSON	为调查节点准备的搜索词
2.3 evidence_locker (证据表：调查结果)
存储 Node D (Investigator) 的搜索情报。
字段名	类型	说明
id	INTEGER (PK)	自增
market_id	TEXT (FK)	关联主表
search_summary	TEXT	搜索结果的 AI 摘要
verification_result	TEXT	'CONFIRMED', 'DEBUNKED', 'UNCERTAIN'
source_links	JSON	引用链接列表

3. 生命周期与状态流转 (State Lifecycle)
这是系统的灵魂。所有 Node 只处理特定状态的数据。
状态枚举 (Status Enum):
● NEW: 刚入库，未处理。
● IGNORED: 被 Filter 节点（流动性差/赔率极端）丢弃。
● READY_TO_PARSE: 通过初筛，等待 LLM 解析。
● PARSED: 解析完成，策略匹配度低（Score < 60），暂时搁置。
● READY_TO_SEARCH: 解析完成且是高潜盘口（Score > 60），等待搜索。
● INVESTIGATED: 搜索完成，等待风控计算。
● READY_FOR_DECISION: 终态。一切就绪，等待生成 Prompt。
关键流转逻辑 (Resurrection):
● 如果 Fetch 发现某 ID 状态是 IGNORED，但最新 liquidity > 阈值 -> 强制重置为 NEW。

4. 模块详细设计 (Component Design)
4.1 main.py (The Executor)
● 职责: 调度器。读取配置，按顺序实例化并运行各个 Node。
● 逻辑:
Python
class PipelineExecutor:
    def run(self):
        # 1. 数据更新 (Upsert & Resurrection)
        IngestorNode().execute()

        # 2. 脏数据清洗 (Filter 99% & Illiquid)
        FilterNode().execute()

        # 3. 静态解析 (LLM Parsing - Costly)
        ParserNode().execute()

        # 4. 证据搜索 (External API)
        InvestigatorNode().execute()

        # 5. 动态风控 (Real-time Edge Calc)
        # 注意：这里是只读计算，不改状态，只筛选输出
        candidates = QuantNode().evaluate()

        # 6. 生成决策 Prompt
        PromptBuilder().generate(candidates)
4.2 nodes/ingestor.py (数据摄入)
● 职责: 拉取 API，处理“热更新”和“复活”。
● 逻辑:
  ○ 拉取全量/增量 Active Markets。
  ○ 遍历每个 Market:
    ■ 存在? -> Update price, liq。Check Resurrection (若 IGNORED 且 Liq > 5000 -> NEW)。
    ■ 不存在? -> Insert, Status = NEW。
4.3 nodes/filter.py (粗筛清洗)
● 职责: 极速排除垃圾，保护 LLM Token。
● SQL 逻辑 (Pseudo):
SQL
UPDATE markets
SET status = CASE
    WHEN active = 0 OR resolved = 1 THEN 'IGNORED'
    WHEN end_at_utc <= NOW() THEN 'IGNORED'
    WHEN (rules IS NULL OR length(rules) < 20) AND (description IS NULL OR length(description) < 20) THEN 'IGNORED'
    WHEN liquidity < 5000 THEN 'IGNORED'
    WHEN price < 0.05 OR price > 0.95 THEN 'IGNORED'
    WHEN category IN ('Sports', 'Gaming') THEN 'IGNORED'
    WHEN event_tags CONTAINS ('sports','gaming') THEN 'IGNORED'
    ELSE 'READY_TO_PARSE'
END
WHERE status = 'NEW';
4.4 nodes/parser.py (智能解析)
● 职责: 结构化提取。
● 输入:SELECT * FROM raw_markets WHERE status = 'READY_TO_PARSE'
● AI 任务: 识别 S1/S2/S3 模式，提取 Exclusions (排除项)。
● 输出: 写入 market_rule_parses。
● 状态流转: 根据 Score > 60 分流为 READY_TO_SEARCH 或 PARSED (低分冷藏)。
4.5 nodes/investigator.py (调查员)
● 职责: 联网验证。
● 输入:SELECT * FROM raw_markets WHERE status = 'READY_TO_SEARCH'
● 动作: 调用 Tavily/SerpApi，搜索 search_keywords。
● AI 任务: 总结搜索结果是否支持做空逻辑。
● 状态流转: Update status -> INVESTIGATED。
4.6 nodes/quant.py (动态风控)
● 职责: 实时计算 Edge。注意，这步是动态的，每次运行 Pipeline 都要重算。
● 逻辑:
  ○ 拉取所有 status IN ('INVESTIGATED', 'PARSED') 的数据（即使之前没搜过，如果赔率变好了也可以捞回来，看你策略）。通常只看 INVESTIGATED。
  ○ Edge 计算:My_Prob (from Parser Score) - Current_Price。
  ○ 过滤: 只返回 Edge > 15% 的候选列表给 Builder。
4.7 nodes/builder.py (提示词工厂)
● 职责: 组装最终产品。
● 动作: 遍历 Quant 筛选出的 Candidates，生成 markdown 文件。

5. 最终交付物设计 (The Prompt Template)
这是 nodes/builder.py 输出的内容，也是你直接复制给 Gemini 的内容。
Markdown
# 🕵️‍♂️ Polymarket Alpha Decision Request
**Date:** 2026-01-XX | **Generated by:** PAP v1.0

I have analyzed the market data and identified a potential **Structural Arbitrage** opportunity. I need your final verdict as a Portfolio Manager.

---

### 1. Market Snapshot
* **Market:** [Title of the Market]
* **Strategy:** [S1 - Rules Lawyer] (Alpha Score: 85)
* **Current Odds:** YES @ **34¢** (Implied 34%)

### 2. The Structural Deadlock (Parser Analysis)
* **The Trap:** The rules explicitly exclude "sub-orbital flights", but the media hype is all about a sub-orbital test.
* **Hard Constraints:**
    * Resolution Source: NASA Official Press Release (High Certainty)
    * Deadline: Must occur *before* Feb 1, 2026.

### 3. Evidence Locker (Investigator Report)
* **Fact 1:** Search confirms NASA has delayed the Artemis update to March (Source: NASA.gov).
* **Fact 2:** No flight manifest exists for January.

### 4. Order Book Health
* **Liquidity:** $250k (Excellent)
* **Spread:** 1.2% (Low slippage)

---

### 🎯 Your Task (Decision)
Please act as the **Final Decision Maker**. Review the evidence above and output:

1.  **Verification:** Is the "Hard Constraint" logic sound?
2.  **Risk Check:** Is there a "Backdoor" we missed?
3.  **Action:**
    * **STRONG BUY NO** (Target 5-8% position)
    * **BUY NO** (Target 3% position)
    * **PASS** (False Positive)

6. 开发路径 (Development Roadmap)
1. Week 1: Data Infrastructure (The Skeleton)
  ○ 搭建 SQLite。
  ○ 编写 ingestor.py (含 Resurrection 逻辑) 和 filter.py (含 99% 过滤)。
  ○ 目标： 跑通后，数据库里应该能看到一堆 READY_TO_PARSE 和 IGNORED 的数据。
2. Week 2: The Brain (Parser)
  ○ 接入 LLM API。
  ○ 编写 Prompt，调试 S1/S2 的识别准确率。
  ○ 目标： 能准确识别出 SpaceX Ticker 这种陷阱局，打分 > 80。
3. Week 3: The Eyes & Voice (Investigator & Builder)
  ○ 接入搜索 API。
  ○ 实现 Prompt 组装逻辑。
  ○ 目标： 产出第一个可用的 .md 决策报告。
这个架构已经足够细致，可以直接进入 Coding 阶段。所有的逻辑闭环（复活、脏数据、动静分离）都已在设计层面解决。

## 二、评审与建议（完整）

### 1) 策略是否靠谱

结论：框架方向正确，但前提条件很多，必须显式量化，否则“结构性错配”会被噪声和执行摩擦吞噬。

核心优点：
1. 关注“定义 vs. 直觉”的结构性错配，比纯预测更稳定。
2. 明确将时间价值、裁决权纳入定价逻辑，是预测市场的关键壁垒。
3. 把参与者行为偏差作为“可重复边际优势”的来源，符合市场微观结构现实。

关键风险与未显式假设：
1. 规则误读风险：S1 依赖“条款解释唯一性”，实际常有模糊条款、仲裁裁量空间、申诉期反转。
2. 时间价值不只看 APY：延期结算、流动性折价、撤单成本、对冲成本会吞噬收益。
3. 信息速度并非完全可持续：市场参与者会学习，边际优势会因“公开套利模板”衰减。
4. 负和假设成立，但“对手盘永久性亏损”并不等价于“我们必赚钱”，仍受价格波动和流动性影响。

建议的补强认知：
1. 把“结构性错配”拆成可量化的三类信号：规则不对称、时间不对称、信息不对称。
2. 明确“模型置信度与执行阈值”的映射（如：置信度 0.8 但流动性低则不进场）。
3. 引入“历史判例库”，对相似规则市场的历史裁决做回归分析，量化仲裁风险。

### 2) 执行方案是否需要补充

整体架构完整，但存在四个关键缺口：

1) 数据层缺口（与现有代码结构不一致）
- 当前项目已有 markets/prices/orderbook_levels/market_rule_parses/scores 的结构。
- 规划中的 raw_markets 与当前 markets + markets_raw 的分层不一致。
建议：明确是否迁移为“状态机表 + 规则解析表”的双层结构；否则会出现两套系统并存、数据口径冲突。

2) 状态机缺口（缺少“状态一致性”与“退化路径”）
- 仅定义“复活”，但没有定义“降级路径”（例如：解析过期、证据失效、订单簿变薄）。
建议：引入状态退化逻辑：
  - READY_TO_SEARCH -> READY_TO_PARSE（规则变更或解析过期）
  - READY_FOR_DECISION -> INVESTIGATED（证据过期或赔率剧变）

3) 风控缺口（边际利润 vs. 成本未闭环）
- Quant Node 只用 My_Prob - Price，没有计入手续费、滑点、撤单成本、对冲成本。
建议：Edge = (Prob - Price) - (Fees + Slippage + TimeCost + DisputeRiskPenalty)

4) 证据节点缺口（证据可信度与可追溯性）
- evidence_locker 只有 summary 和链接，没有时间戳、来源权重、冲突信号。
建议：加入 source_score、retrieved_at_utc、conflict_flags，允许后续规则审计。

### 3) 我的建议（可直接落地）

第一优先级（立刻补）：
1. 明确 schema 对齐策略：要么迁移为三表模型，要么扩展现有表并加 status/state_history。
2. 定义“解析 TTL”：规则解析超过 N 天强制重跑，避免过期条款。
3. 量化“仲裁风险分”：基于历史 dispute/appeal 记录做惩罚项。

第二优先级（近期增强）：
1. 增加“市场类型过滤”：多结果市场/特殊结算条款单独处理。
2. 引入“微结构指标”：如价差、深度、盘口稳定性，用于执行过滤。
3. 建“案例库”与“规则模板库”：用于 S1/S2 准确率提升与人工审阅加速。

第三优先级（长期竞争力）：
1. 事件驱动告警：规则/新闻/裁决触发的自动退场信号。
2. 跨平台价差对比：若可获取其他预测市场价格，作为 S3 的真实套利信号。
3. 回测与监控：对策略分层（S1/S2/S3）单独评估胜率与收益分布。

### 4) 简要结论

整体思路是靠谱的，尤其是“规则/时间/信息错配”三角结构。但要避免变成“高概念、低执行”的陷阱，必须尽快落地以下两件事：
1. 把状态机与数据口径统一。
2. 把 Edge 计算做成“可落地的净收益模型”。
