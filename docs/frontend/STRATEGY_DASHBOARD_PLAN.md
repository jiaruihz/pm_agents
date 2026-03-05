# 统一策略大盘产品计划（前端专区，后端重构对齐版）

> 状态：计划已冻结，暂不执行实现
>
> 日期：2026-03-05
>
> 依赖后端计划：`docs/backend/UNIFIED_STRATEGY_PLATFORM_REFACTOR_PLAN.md`

## 1. 目标与范围

在本仓库新增统一前端专区，建设策略大盘，支持：

1. 按策略分类查看全量策略。
2. 清晰查看每个策略实例运行状态与效果。
3. 查看账户信息（聚合 + 实例拆分）。
4. 每个策略具备独立前端页面并可切换。
5. 整合 `pm-research` 与 `backtest viewer`，删除旧前端入口。

前端阶段只落文档，不执行前端代码改造；后端重构已按配套计划推进。

## 2. 与后端计划对齐的关键决策

1. 前端形态：React SPA。
2. 统一服务：对接 `src/interfaces/web/strategy_dashboard_server.py`（BFF）。
3. 策略元信息唯一来源：`src/strategies/*/manifest.yaml`。
4. 运行数据主库：`runtime/strategy_runtime.db`（不再依赖 `runtime/pmm_instances.db`）。
5. `research_tasks` 不纳入第一期前端范围（无任务列表/任务状态页）。
6. 规则律师保留信息能力，不保留旧工具页形态。
7. 旧 `web_ui/research`、`web_ui/backtest` 最终删除。

## 3. 路由与页面信息架构

1. `/dashboard`：全局概览（策略组、运行状态、关键指标）。
2. `/strategies`：策略目录（按 `strategy_group` 分类）。
3. `/strategies/:strategyKey`：策略独立页面。
4. `/instances/:instanceId`：实例详情（状态、快照曲线、上下文信息）。
5. `/accounts`：账户聚合（含 `unknown_account` 告警）。
6. `/research`：research 市场信息页（列表/详情/同步动作结果）。
7. `/backtests`：回测与运维信息页（runs/table/scenario/ops/supervisor）。

## 4. 前端模块划分

1. `AppShell`：导航、全局筛选、自动刷新。
2. `StrategyCatalog`：策略目录和分组统计。
3. `InstanceMonitor`：实例状态表 + 心跳/状态老化。
4. `InstanceHistory`：`pnl/equity/usdc` 时序曲线。
5. `AccountCenter`：账户聚合与实例映射。
6. `ResearchPanel`：research 市场与动作回显（同步模式）。
7. `BacktestPanel`：回测结果表、场景详情、运维状态。
8. `ApiClient`：统一 `/api/v1` 请求层与错误体解析。

## 5. API 契约（前端消费）

统一基于 `/api/v1`：

1. `GET /api/v1/strategies`
2. `GET /api/v1/instances?status=&strategy_key=&execution_mode=&account_id=&wallet_address=&limit=&offset=&stale_after_sec=`
3. `GET /api/v1/instances/{instance_id}`
4. `GET /api/v1/instances/{instance_id}/history?limit=`
5. `GET /api/v1/accounts?limit=&offset=`
6. `GET /api/v1/research/markets?page=&page_size=`
7. `GET /api/v1/research/markets/{market_id}`
8. `POST /api/v1/research/actions/filter`
9. `POST /api/v1/research/actions/parse`
10. `POST /api/v1/research/actions/prompt`
11. `POST /api/v1/research/actions/run_all`
12. `GET /api/v1/backtests/runs`
13. `GET /api/v1/backtests/table?run=&q=`
14. `GET /api/v1/backtests/scenario?run=&scenario_run_id=`
15. `GET /api/v1/ops/status`
16. `GET /api/v1/ops/logs?name=&lines=`
17. `GET /api/v1/supervisor/sessions?limit=`

## 6. 数据口径约束

1. 时间字段统一用 UTC ISO8601 展示与存储。
2. 账户聚合键优先级：`account_id > wallet_address > unknown_account`。
3. 前端需统一处理错误体：`{code,message,details,request_id,timestamp_utc}`。
4. 分页统一按 `limit/offset/total` 协议处理。

## 7. 与后端同步的清理项（前端相关）

1. 下线旧独立 UI：`web_ui/research/*`、`web_ui/backtest/*`。
2. 前端不再依赖旧 `/api/*` 路由，仅使用 `/api/v1/*`。
3. 前端代码中移除对 `runtime/pmm_instances.db` 或 `pmm_*` 表名的任何假设。

## 8. 实施顺序（前端侧）

1. 先搭建 `frontend/strategy_dashboard/` 与基础路由。
2. 按 `/api/v1` 契约接入策略/实例/账户页。
3. 接入 research 页（同步 action 模式）。
4. 接入 backtest + ops + supervisor 页面。
5. 全链路联调并替换旧入口。
6. 清理旧页面与旧 API 引用。

## 9. 验收标准

1. 前端可按策略分组浏览全策略，并进入独立策略页。
2. 可查看实例状态和历史曲线（来自 `strategy_instance_state/snapshots`）。
3. 可查看账户聚合并正确处理 `unknown_account`。
4. research 与 backtest 能力均可在统一前端使用。
5. 全部接口调用使用 `/api/v1`，无旧路由残留。
6. 旧 `web_ui/*` 页面下线后，前端主流程不受影响。

## 10. 风险与默认假设

1. 后端会按计划完成 `runtime/strategy_runtime.db` 与新表结构。
2. `research_actions` 第一版为同步接口，前端需处理超时失败回显。
3. 第一版不做鉴权，仅面向内部使用。

## 11. 执行状态

1. 文档已更新（当前）。
2. 代码尚未执行（按“先文档、后执行”要求）。
