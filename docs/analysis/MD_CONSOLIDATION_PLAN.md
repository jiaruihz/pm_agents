# Weather 文档清理账本

Status: current-reference
Updated: 2026-08-06
Source of truth: no
Used by: `WEATHER_DOCS_INDEX.md`; `docs/analysis/` living docs

这不是策略结论或又一份文档索引，只记录文档负债的清理口径、已完成批次和下一批队列。当前研究结论以
`WEATHER_STRATEGY_REGISTRY.md`、对应家族 living doc 和最新 frozen/reset report 为准；生产状态以 controller、manifest
和 raw/exchange evidence 为准。

## 清理合同

整理一个历史报告前必须先读内容，并完成以下四步：

1. 锁定 owner living doc，提取仍成立的机制结论、证据窗口和失效边界。
2. 把耐久结论写入 owner；日期报告只保留当时 snapshot，不再声明“当前结论”。
3. 检查文档链接、代码默认路径、测试和 artifact consumer；存在消费者就保留。
4. 只有确认是重复、已被吸收或数字已失效，且用户已授权删除时，才删除/归档；`inconclusive` 和 dormant 不等于 dead。

禁止用“移动文件”“改索引状态”冒充内容整合，也禁止把旧 PnL、旧城市排名或旧 live 标签复制进 living doc。

## 权威归属

| 内容 | Owner |
|---|---|
| 概率、校准、model-vs-market | `model_vs_market.md` |
| 盘口结构、range/RV、repricing | `market_structure_edge.md` |
| fill、fee、成交覆盖、执行窗口 | `execution_quality.md` |
| 入场时钟与 city×timing | `entry_timing.md` |
| side 表达 | `side_alpha.md` |
| 城市池与 city-day portfolio | `city_selection.md` |
| observed path、reheat、current/d1 | `reheat_risk.md` |
| sizing 与价格带 | `sizing_entry_band.md` |
| blender/shadow | `blender_shadow.md` |
| live 历史表现 | `live_performance.md` |
| 钱包和账户对账 | `account_reconcile.md` |
| side flip、候选/成交血缘、数据缺口 | `data_integrity.md` |

WCIR、数据源、生产与 canonical contract 不写进上述研究 living doc，分别回到 WCIR 设计、data-feed、repo boundary、
system contract 和 ops runbook。

## 已完成的历史批次

- 2026-06-11：2026-05 报告迁入历史区；早期 UNKNOWN/maker-queue、旧 city pool、旧 PnL 数字不再作当前证据。
- 2026-06-11：部分 2026-06-03..09 重复或 invalidated 报告迁入历史区；near-binary、fill recovery、
  `missing_bracket` 污染窗口保留为失败史。
- 2026-06-16：observed-max 新研究归入 `reheat_risk`；`observed_max/` 只保留历史 producer/station-basis 邻接工作。
- 2026-08-05..06：大型 generated 产物迁到 JRS 内容寻址库；可精确重放的派生对象用 tombstone 清理，raw/canonical 不动。
- 2026-08-06：删除本文件原有 400 余行逐文件流水账；已执行的迁移由 git 历史、索引和 living doc 承担，
  不再维护第二份平行清单。

- 2026-08-06：完成“切片冒充全量”专项清理。D-1 的 5,561 rows 已还原为
  `42,705 artifact rows → 16,916 best-model rows → 5,561 May–Aug training slice`，修正 registry、活跃报告、
  runner 与 summary metadata；artifact 全部 rows 也不自动等于项目全部历史。
- 2026-08-06：dated `current-reference` 从 73 份收敛到 8 份；保留项均被 `scripts/ops` 作为运行实例的冻结
  source/preregistration/provenance 合同引用，其余65份统一降为 `snapshot`。新增 docs check allowlist，防止日期报告重新
  膨胀为平行当前真相。
- 2026-08-06：city-day basket 删除11个已吸收文件：06-07 同 runner 的较早 baseline/distribution、除时间戳外完全
  重复的 walk-forward、已被 living doc 吸收的 optimizer/protocol，以及两份过渡 handoff；当前只保留06-08较新证据并
  由 `city_selection.md` 统一结论。blender 的过渡 handoff 已吸收进 `blender_shadow.md`；三份不同 denominator 的 producer
  报告继续保留为 snapshot evidence，不误删成“重复”。

## 仍有效的历史边界

- 2026-06-07 前的 live PnL、胜率、城市/策略排名，若依赖旧 near-binary、缺 fill 或 `missing_bracket`，不得用于当前决策。
- paper/shadow、proxy ask、少量 selected trades 和运行中的 probe 不能证明 live alpha。
- 后来的 fee、fill、settlement 或 denominator 修正只作勘误；不得把旧 gross 结论继续留在 living doc 主结论中。
- 日期报告可以保留 lineage、失败模式和预注册规则，但当前动作只能在 owner living doc/registry 出现一次。

## 当前清理队列

1. **dated current-reference 收敛**：日期报告原则上降为 snapshot；只有仍被运行合同直接消费的 protocol/entrypoint 可保留 current-reference。
2. **重复家族合并**：优先 live-period slice、city-day basket、blender、range/RV、current-YES/reheat 和 source-event 多版本报告。
3. **历史脚本去重**：先抽公共 helper/config runner，再删除仅城市、日期、参数不同的入口；保留算法或数据合同真正不同的 producer。
4. **机器产物修复**：没有 frozen input inventory、producer revision 或 exact replay 的对象继续保留，不因文件大而删。
5. **非天气分离**：copy-trade、wallet 与硬件研究进入各自 owner，不继续混入天气结论索引。

每批交付必须报告：吸收了哪些结论、删除/保留了哪些文件、为什么安全、链接/测试结果，以及是否影响生产。
