# 统一策略大盘产品计划（前端专区）

> 状态：计划已冻结，暂不执行实现（按你的要求先落文档，后续再推进开发）
>
> 日期：2026-03-04

## 1. 目标与范围

在本仓库新增前端专区，建设统一策略大盘，支持：

1. 按策略分类查看全量策略。
2. 查看每个策略实例的运行状态与效果。
3. 查看账户信息（聚合视图 + 实例拆分视图）。
4. 每个策略拥有独立前端页面并可切换。
5. 整合 `pm-research`（规则律师）与 `backtest viewer`，删除旧前端页面入口。

当前阶段仅完成产品与技术计划，不实施代码改造。

## 2. 已确认决策

1. 前端形态：React SPA。
2. 整合方式：深度整合重构（不是 iframe、不是仅导航聚合）。
3. 账户信息：做全账户聚合 + 实例级明细。
4. 策略页覆盖：全部已注册策略（按策略目录动态展示）。
5. 规则律师页面：旧界面删除，仅保留信息能力并纳入新大盘。
6. 旧入口策略：立即删除旧页面（无过渡跳转）。
7. 接口演进：前端引入数据适配层，支持文件源 -> API 源切换。
8. 聚合口径：按账户标识去重求和（`account_id`/`wallet`）。

## 3. 仓库结构规划

### 3.1 新增目录

1. `frontend/strategy_dashboard/`
2. `src/interfaces/web/strategy_dashboard_server.py`
3. `docs/frontend/`（当前文档所在目录）

### 3.2 删除目录（实施阶段执行）

1. `web_ui/research`
2. `web_ui/backtest`

### 3.3 保留并复用能力

1. `src/domains/research/*` 的数据能力。
2. `src/domains/pmm/ops/instance_store.py` 的策略与实例数据。
3. `src/domains/pmm/backtest/*` 的结果解析能力。

## 4. 产品信息架构与路由

1. `/dashboard`：策略总览（分类、健康、运行概况）。
2. `/strategies`：策略目录（按 `strategy_group` 分类）。
3. `/strategies/:strategyKey`：策略独立页面。
4. `/instances/:instanceId`：策略实例详情页。
5. `/accounts`：账户聚合页（总览 + 拆分）。
6. `/research`：规则律师信息页（列表/详情/状态）。
7. `/backtests`：回测查看页（整合原 backtest viewer）。

## 5. 前端模块规划

1. `AppShell`：全局导航、布局、全局刷新控制。
2. `StrategyCatalog`：策略分组与运行统计。
3. `InstanceMonitor`：实例状态、心跳、PnL/Equity/USDC 监控。
4. `StrategyWorkspace`：按 `strategyKey` 渲染独立策略页面。
5. `AccountCenter`：账户聚合卡片与实例明细表。
6. `ResearchInsight`：规则律师信息展示（去工具化）。
7. `BacktestExplorer`：回测结果列表、表格、详情。
8. `DataAdapter`：前端域数据适配层（切换数据源）。

## 6. API 与数据接口规划

前端统一调用 BFF，BFF 聚合现有域能力。

### 6.1 对外 API（BFF）

1. `GET /api/v1/strategies`
2. `GET /api/v1/instances?status=&strategy_key=&limit=`
3. `GET /api/v1/instances/:id`
4. `GET /api/v1/instances/:id/history?limit=`
5. `GET /api/v1/accounts`
6. `GET /api/v1/research/markets`
7. `GET /api/v1/research/markets/:marketId`
8. `GET /api/v1/backtests/runs`
9. `GET /api/v1/backtests/table?run=&q=`
10. `GET /api/v1/backtests/scenario?run=&scenario_id=&profile=`

### 6.2 前端适配层接口（内部）

1. `DashboardProvider`（统一业务接口）
2. `FileBackedProvider`（当前可落地）
3. `HttpBackedProvider`（后端 API 稳定后切换）

环境变量建议：`DASHBOARD_DATA_MODE=file|http`

## 7. 账户聚合口径

1. 主键优先：`account_id`。
2. 回退主键：`wallet_address`。
3. 均缺失时：落入 `unknown_account`，前端展示口径告警。
4. 聚合指标：
   - `equity_total`
   - `usdc_total`
   - `pnl_total`
   - `open_orders_total`
   - `running_instances`

## 8. 迁移与下线计划（实施阶段）

1. 搭建新前端骨架与统一路由。
2. 接入策略与实例数据。
3. 接入账户聚合数据。
4. 接入 research 信息页。
5. 接入 backtest explorer。
6. 联调与回归测试。
7. 删除旧页面与旧静态入口。
8. 更新 README 与运维文档。

## 9. 验收标准

1. 可按策略分类浏览全部策略。
2. 可查看每个策略实例运行状态与核心效果指标。
3. 可查看账户聚合与实例拆分。
4. 可在前端切换进入策略独立页面。
5. `pm-research` 与 `backtest viewer` 能力在新大盘内可用。
6. 旧 `web_ui/research` 与 `web_ui/backtest` 被下线。
7. 数据适配层可在 `file/http` 模式间切换而不改页面层。

## 10. 测试计划

1. API 单测：策略、实例、账户、research、backtest 端点。
2. 聚合单测：账户去重与 `unknown_account` 逻辑。
3. 前端组件测试：策略目录、实例状态、账户汇总。
4. 前端路由测试：总览到策略到实例到账户链路。
5. 兼容测试：`file` 模式与 `http` 模式一致性。
6. 回归测试：旧入口移除后无残留引用。

## 11. 风险与默认假设

1. 后端将提供 `account_id` 或 `wallet_address` 至少一种标识。
2. 现阶段部分数据读取仍可能来自文件工件，后续将 API 化替换。
3. `runtime/pmm_instances.db` 可能存在历史 schema，需兼容迁移。
4. 第一版默认单用户内部运营场景，不做鉴权体系。

## 12. 后续执行说明

本文件仅记录冻结计划，未实施任何功能改造。  
当你确认开始执行时，按本计划分阶段落地，并在每阶段结束后更新本文件的“执行进度”小节（后续补充）。

