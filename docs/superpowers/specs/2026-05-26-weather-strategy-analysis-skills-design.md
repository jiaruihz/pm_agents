# Weather Strategy Analysis Skills — Design

> Date: 2026-05-26  
> Status: Draft for review  
> Author: brainstorming session (Claude + user)

## 目标与动机

随着 weather 策略实盘数据积累，需要持续做**绩效分析 / 单日血缘排查 / A/B 对比 / 持仓敞口**等多种分析。目前的问题：

- 每个 agent（Claude / Codex / MiniMax）来分析时都自己写一次性 pandas 脚本
- 各自定义 PnL / win_rate / 切片维度，结论之间无法对比
- 经常绕过已有的 `weather.db` / Dashboard API / `t24_paper_ledger_summary.json`，直接去刨 N100 raw
- 报告产物格式各异，沉淀不下来

**核心目标**：用一份"分析口径契约"+ 三个职责单一的 skill，强制所有 agent 走同一条路径，产出格式一致、口径一致、可对比的分析报告。

本设计不写代码，只规定 skill 文件结构、契约文档骨架、报告模板骨架。后续由 Codex/Claude 按 plan 实施。

## 系统组成

```
skills/                                               ← 项目内 skill 目录（已有约定）
  weather-strategy-performance/SKILL.md               ← M1 绩效切片 + M3 A/B 对比
  weather-strategy-lineage/SKILL.md                   ← M2 单日血缘
  weather-strategy-exposure/SKILL.md                  ← M4 持仓敞口

docs/
  WEATHER_ANALYSIS_CONTRACT.md                        ← 口径真相，唯一来源
  analysis/
    templates/
      performance.md                                  ← M1 报告骨架
      performance-compare.md                          ← M3 报告骨架
      lineage.md                                      ← M2 报告骨架
      exposure.md                                     ← M4 报告骨架
    YYYY-MM/                                          ← 报告输出，按月份分子目录
      YYYY-MM-DD-<mode>-<topic>.md
```

三个 skill 互相独立、共享同一份 contract 与模板族。

## 三个 Skill 的分工

| Skill | 模式 | 触发词（description 关键字） |
|---|---|---|
| `weather-strategy-performance` | M1 单跑绩效切片 / M3 双策略 A/B 对比 | 绩效、PnL、ROI、win rate、切片、对比策略、A/B、回测结果、策略表现 |
| `weather-strategy-lineage` | M2 单日血缘（信号→计划→订单→成交→结算） | 单日、血缘、逐笔、为什么下了这单、信号到结算、当日复盘 |
| `weather-strategy-exposure` | M4 持仓敞口快照 | 持仓、敞口、未结算、未平仓、风险、当前仓位 |

**合并理由**：M1 和 M3 是同一类数据形态（聚合切片），M3 = 跑两次 M1 + 并表 + 算 delta，模板里多一个"对比"段即可；拆开会重复 80% 流程。M2 和 M4 与 M1 数据形态完全不同（逐笔追踪 / 时间点快照），各自独立。

## Skill 通用结构（三者一致）

每个 `SKILL.md` 控制在 ~100 行以内，包含：

1. **YAML 头**（name / description / 触发词）
2. **强制前置**：第一步必须读 `docs/WEATHER_ANALYSIS_CONTRACT.md`，未读不得继续
3. **模式确认**：跟用户确认本次分析的 mode + 时间窗 + 数据源 + 策略选择器
4. **数据源优先级**（硬规定）：
   - 优先级：`weather.db` > Dashboard API > 镜像 JSON/CSV (`runtime/.../research/`) > N100 raw
   - 每降一级必须在报告里写明"为什么上一级满足不了"
5. **禁止清单**：
   - 不准在 `/tmp` 或未存档目录里写一次性 pandas 脚本
   - 不准自己定义新指标、新切片维度、新 strategy_id（必须用 contract 里的）
   - 不准只输出数字结论而不写 Markdown 报告
   - 不准跳过"数据完整性自检"段
6. **报告模板路径**：指向 `docs/analysis/templates/<mode>.md`
7. **输出位置规约**：`docs/analysis/YYYY-MM/YYYY-MM-DD-<mode>-<topic>.md` + git commit
8. **报告头必填项**：数据源、数据快照时间、数据完整性自检（行数 / unsettled 占比 / missing_bracket 数）

通用规约（数据源优先级、禁止清单、报告头必填项）**放在 contract 的"§0 通用规约"章节**，三个 SKILL.md 用一句话引用，避免重复。

## Contract 文档骨架（`docs/WEATHER_ANALYSIS_CONTRACT.md`）

```
§0 通用规约
   - 数据源优先级（DB > API > 镜像 > raw）
   - 禁止清单（同 skill 规约）
   - 报告头必填项

§1 数据源清单
   - weather.db：表清单、覆盖时间、刷新方式
   - Dashboard API：可用 endpoint、参数、典型 response
   - 镜像产物：每个 CSV/JSON 的生成路径、覆盖范围、已知缺口
   - N100 raw：仅在 §0 允许降级时使用，存放位置

§2 核心指标定义
   2.1 PnL
       - 已结算 PnL：公式 + SQL 片段
       - 未结算 PnL：必须单独列出、估值方法（mid / bid / last_fill 三估值并列）
       - 两种入场价口径：plan_price（信号下计划价） vs fill_price（实际成交价），都要算
       - fill_qty（成交数量）必须出现在报告里，不能只看 PnL
   2.2 Win rate
       - by_order_count：胜单数 / 总单数（含未结算视为 0 / 排除未结算 两套）
       - by_notional：胜单 notional / 总 notional
       - 报告必须同时列两套
   2.3 ROI、平均仓位、max drawdown、sharpe-like（公式 + SQL）

§3 时间规约
   - 双时区显示：北京时间（Asia/Shanghai）+ 当地时间（按城市 timezone 表）
   - 跨日订单归属：以下单时间戳所在日为准
   - 城市 timezone 表（写死，与 N100 配置对齐）

§4 策略身份
   - 策略身份 = strategy_id（即使 sizing 改了仍是同一个）
   - 切片用 code_version × strategy_config 子键
   - 切片白名单见 §5

§5 切片维度白名单
   by_date / by_city / by_model / by_side / by_pool / by_pool_side / by_pool_model
   （新切片必须先 PR 进 contract 再使用）

§6 默认城市池
   - "T1 + 新增 8 个"：列出具体城市名
   - 其他池（T2 research / 旧池 / 混合）使用前必须由用户在 prompt 里指定

§7 报告模板字段顺序（与 templates/*.md 对齐）

§8 待定项（遇到再补）
   - 滑点扣减口径
   - 基准对比（vs 随机 / vs 全 BUY_NO / vs 持有 YES 到结算）
   - 重复计数处理（同一笔单在 ledger / replay / DB 多份的去重）
   - 其他口径分歧
```

## 报告模板骨架

每个模板规定固定 H2 段落顺序，agent 不得删段、不得改顺序，最多在末尾追加"观察与建议"段。

**`templates/performance.md`（M1 单跑）**
```
## 数据快照
## 总览（已结算 / 含未结算 两栏并列）
## 切片：by_date
## 切片：by_city
## 切片：by_model
## 切片：by_side
## 切片：by_pool
## Top winners / Top losers
## 数据完整性自检
## 观察与建议（自由发挥）
```

**`templates/performance-compare.md`（M3 A/B 对比）**
```
## 数据快照（A / B 两栏）
## 对比设定（selector A vs selector B）
## 总览对比（A | B | delta | delta %）
## 切片对比：by_date / by_city / by_model / by_side / by_pool（每段同样三列）
## 显著差异 Top-N
## 数据完整性自检
## 观察与建议
```

**`templates/lineage.md`（M2 单日血缘）**
```
## 数据快照
## 当日策略身份与配置
## 全链路表（按 city 分组）
   - signal → plan → order → fill → settlement，逐笔横向展开
## 异常订单列表（missing_bracket / 重复下单 / 计划价与成交价偏差大）
## 当日 PnL 汇总（与 M1 口径一致）
## 数据完整性自检
## 观察与建议
```

**`templates/exposure.md`（M4 持仓敞口）**
```
## 快照时间
## 未结算持仓清单（per market × per side）
## 聚合：by_market / by_city / by_settle_date
## 未实现 PnL：三估值并列（mid / bid / last_fill）
## 集中度风险（单市场 / 单到期日的占比 Top-N）
## 数据完整性自检
## 观察与建议
```

## 触发与发现

- 三个 skill 的 `description` 互相不重叠，触发词分工见上表
- 在 `CLAUDE.md` / `AGENTS.md` 里加一行硬引导：「任何 weather 策略分析（绩效 / 血缘 / 敞口）必须先 invoke 对应的 `weather-strategy-*` skill，不准跳过」
- skill description 显式标注："禁止在不读 contract 的情况下写一次性分析脚本"

## 范围与非目标

**本设计包含**：
- 三个 skill 的 SKILL.md 骨架与触发词
- contract 文档的章节结构
- 四个报告模板的 H2 段落顺序
- 报告输出路径与命名规约

**本设计不包含（留给后续 plan / 实现）**：
- contract 各章节的具体内容填充（指标 SQL、城市 timezone 表、默认池城市列表等具体值）——这部分需要核对 `weather.db` schema、`WEATHER_DATA_PIPELINE.md` 现有口径、N100 实际配置后由 Codex 落地
- skill 内部的具体步骤话术与示例
- 模板里每个段落具体要哪些字段、表格列名（应在实施时与 `t24_paper_ledger_summary.json` 已有字段对齐）
- §8 待定项（滑点 / 基准 / 去重）——实际遇到时再补

## 实施顺序建议

1. 先写 `docs/WEATHER_ANALYSIS_CONTRACT.md`（§0 通用规约 + §1 数据源 + §2 PnL/Win rate + §3 时间 + §4 策略身份 + §5 切片白名单 + §6 默认池）
2. 写四个 template 文件
3. 写三个 SKILL.md（薄，引用 contract）
4. 更新 `CLAUDE.md` / `AGENTS.md` 加硬引导
5. 用一个真实分析需求跑通验证（例如"分析 2026-05-23 t1_trading 当日绩效"）

## 设计决策记录

| 决策 | 选择 | 理由 |
|---|---|---|
| 单 skill vs 多 skill | 拆 3 个（performance / lineage / exposure） | 关注点单一，触发词清晰，SKILL.md 短小 |
| 口径放 skill 还是 contract | contract 文档为唯一真相，skill 薄 | 其他 agent 即使不走 skill 系统也能对齐；contract 可独立演进 |
| M1 与 M3 合并 | 合并到 performance skill | A/B 对比 = 跑两次 M1 + 并表，数据形态一致 |
| 产出格式 | Markdown 报告（不引入 JSON 中间产物） | 个人项目，YAGNI；已有 JSON summary 由 contract 引用即可 |
| 报告输出位置 | `docs/analysis/YYYY-MM/` 按月份分子目录 | 长期沉淀不会让单目录爆炸 |
| 未结算 PnL | 计入但单独列、三种估值并列 | 用户明确要求 |
| 入场价口径 | plan_price 与 fill_price 都算并列展示 | 用户明确要求 |
| 时间显示 | 北京时间 + 当地时间双栏 | 用户明确要求 |
| 策略身份 | strategy_id 算一个，sizing 改了仍同一 | 用户明确要求；切片用 code_version × config 子键 |
| 默认城市池 | T1 + 新增 8 个 | 用户明确要求；具体列表在 contract §6 |
