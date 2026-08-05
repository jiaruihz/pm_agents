# Weather 分析脚本归属与退场账本

Status: current-reference
Updated: 2026-08-06
Source of truth: no
Used by: `WEATHER_REPO_BOUNDARY.md`; `WEATHER_ARCHITECTURE_SPINE.md`; analysis living docs

这份文件不再维护逐文件 old→new 路径表。2026-06 的迁移已经完成，继续保留相同路径映射只会制造第二份目录索引；
历史位置由 git 追踪，当前文件位置由 `rg --files scripts/analysis scripts/etl scripts/ops` 查询。

## 当前路由规则

| 工作 | 目录 / 入口 |
|---|---|
| canonical fact 构建与 backfill | `scripts/etl/`，输出进入统一 fact layer |
| model-vs-market / forecast quality | 对应 `scripts/analysis/<family>/`，同机制复用 runner+config |
| intraday observed path / reheat / current-d1 | `scripts/analysis/reheat_risk/` 与 WCIR 公共 contract |
| execution/fill/fee 研究 | `scripts/analysis/execution_quality/`，读取 canonical facts |
| account/cash/CLOB reconcile | `scripts/analysis/account_reconcile/`，不与绩效脚本混算 |
| current production mutation | `scripts/ops/weather_production_ctl.py` 及 production contract 登记入口 |
| copy-trade / wallet | 独立策略家族，不进入 weather 分析目录 |

新的日期、城市、训练窗、阈值或输出目录不能成为新脚本的理由。算法和数据合同相同时，必须扩展现有 runner 的 config/run
manifest；新增入口要说明它在 `EventEnvelope → ... → fill → settlement` 的哪一层，以及为什么现有 owner 无法承载。

## 已完成迁移

- fact-table builder/backfill 已归 `scripts/etl/`。
- model、market structure、execution、entry timing、side、city、sizing、blender、live performance、account、data integrity
  已按 living-doc family 分目录。
- copy-trade 工具已与 weather 主线分离。
- 2026-06-16 后的 maintained observed-path 研究进入 `reheat_risk`；`observed_max/` 不是新工作的默认入口。
- 2026-08-06 起，producer 只有在输出已精确重放 tombstone、无代码/测试消费者、复现 revision 已记录时才允许退场。

## 当前脚本债务

1. **历史 producer 被当 helper import**：先把公共逻辑抽到稳定 module，再退掉一次性研究入口；不能直接删除。
2. **同家族 vN 脚本**：按 AST/行为比较，不按文件名猜重复；模型、标签、PIT clock 或数据合同不同则保留。
3. **城市专用脚本**：城市只改变 profile/config 时并入 WCIR runner；city adapter 真正不同则保留 adapter，不复制 collector/execution。
4. **动态输入**：argparse default、Path join、glob 和 imported helper 的 archived input 都必须进入 dependency audit。
5. **mutable replay**：依赖 current DB、latest.json 或后来补入的 snapshot inventory 时，先冻结输入清单；无法 exact replay 就保留。
6. **无界 self-check**：历史 producer 不应为生成一个派生 CSV 扫描当前整库；保留的 runner 改用共享、bounded manifest/gate。

## 删除验收

一个历史脚本只有同时满足以下条件才可删除：

- 不属于 production manifest/controller 注册入口；
- 没有 import、测试、文档执行命令或 active artifact consumer；
- 耐久结论已经进入 owner living doc，日期报告保留必要 lineage；
- 派生输出已保留，或 clean revision + frozen inputs 能精确重放；
- 删除后 docs check、dependency audit 和相关测试通过。

暂时不用、研究失败或被新方向覆盖，只能标 dormant/superseded-for-now；这本身不满足删除条件。
