# Unified Strategy Platform Refactor - Execution Log (2026-03-05)

## 1. 变更背景与目标

本次执行基于已确认方案：

1. 去掉 `research_tasks`（本期不实现异步任务表/任务接口）。
2. 建立平台级运行库 `runtime/strategy_runtime.db` 与通用表模型。
3. 引入统一策略目录 `src/strategies/*` 作为元信息唯一来源。
4. 建立统一 BFF：`src/interfaces/web/strategy_dashboard_server.py`，统一 `/api/v1/*`。
5. 删除旧 PMM 专用运行注册、旧 web server 和旧静态 `web_ui`。

## 2. 执行时间线（按步骤）

1. 新建 `src/strategies/`（schema/registry + 5 个策略 manifest/readme/params/run.sh）。
2. 新建 `src/platform/strategy_runtime/store.py`（策略/实例/状态/快照四表 + 索引 + 快照清理）。
3. PMM 引擎切换到新 store，并改为从 `src/strategies` 加载策略目录。
4. 配置切换到 `STRATEGY_RUNTIME_DB_PATH`，保留 `PMM_INSTANCE_DB_PATH` 兼容读取并标注 deprecated。
5. 新建统一 BFF `src/interfaces/web/strategy_dashboard_server.py`，提供 `/api/v1/*`。
6. 删除旧入口与旧实现：`pmm_backtest.py web`、`research cli web`、旧 `ops`、旧 `strategy_packs`、旧 web servers、旧 `web_ui`。
7. 更新 README/PMM 文档/前端计划文档与后端设计文档。
8. 跑验证（compileall + 指定测试 + API 烟测 + 全量 PMM 测试基线记录）。
9. 运行产物治理：归档旧 DB、更新 `.gitignore`（`runtime/*.pid|*.db|*.json|logs/*.log`）避免发布噪音。

## 3. 关键命令与结果摘要

1. `PYTHONPATH=. .venv/bin/python -m compileall src scripts -q`
   - 结果：通过。
2. `PYTHONPATH=. .venv/bin/pytest tests/pmm_tests/test_strategy_runtime_store.py -q`
   - 结果：`1 passed`。
3. `PYTHONPATH=. .venv/bin/pytest tests/pmm_tests/test_strategy_packs.py -q`
   - 结果：`2 passed`。
4. `PYTHONPATH=. .venv/bin/pytest tests/pmm_tests -q`
   - 结果：`2 failed, 71 passed`（见“已知问题”）。
5. BFF 启动命令（临时端口 `18011`）：
   - `PYTHONPATH=. .venv/bin/python -m src.interfaces.web.strategy_dashboard_server --host 127.0.0.1 --port 18011 --runtime-dir runtime --artifacts-dir src/domains/pmm/backtest/.artifacts`
6. BFF 路由烟测：
   - `GET /api/v1/health` -> `200`
   - `GET /api/v1/strategies` -> `200`
   - `GET /api/v1/instances` -> `200`
   - `GET /api/v1/accounts` -> `200`
   - `GET /api/v1/backtests/runs` -> `200`

## 4. 结构迁移映射（旧 -> 新）

1. 策略元信息：
   - `src/domains/pmm/strategy_packs/*` -> `src/strategies/*`
2. 运行存储：
   - `src/domains/pmm/ops/instance_store.py` -> `src/platform/strategy_runtime/store.py`
3. Web 服务：
   - `src/domains/pmm/backtest/web_server.py` + `src/domains/research/web_server.py`
   -> `src/interfaces/web/strategy_dashboard_server.py`
4. 脚本入口：
   - `scripts/python/pmm_strategy_packs.py` -> `scripts/python/strategy_catalog.py`

## 5. DB 迁移策略与实际执行结果

1. 策略：不迁移旧 `runtime/pmm_instances.db` 数据，直接切新库。
2. 新库：`runtime/strategy_runtime.db`（运行期自动建表）。
3. 旧库处理：由于运行环境策略限制删除命令，采用归档重命名：
   - `runtime/pmm_instances.db` -> `runtime/pmm_instances.db.deprecated_20260305`
4. 兼容变量：
   - 主变量：`STRATEGY_RUNTIME_DB_PATH`
   - 兼容读取：`PMM_INSTANCE_DB_PATH`（deprecated）

## 6. API 变更清单（旧 -> 新）

统一迁移到 `/api/v1/*`：

1. `GET /api/strategies` -> `GET /api/v1/strategies`
2. `GET /api/instances` -> `GET /api/v1/instances`
3. `GET /api/instance/history` -> `GET /api/v1/instances/{instance_id}/history`
4. `GET /api/runs` -> `GET /api/v1/backtests/runs`
5. `GET /api/table` -> `GET /api/v1/backtests/table`
6. `GET /api/scenario` -> `GET /api/v1/backtests/scenario`
7. `GET /api/ops/status` -> `GET /api/v1/ops/status`
8. `GET /api/ops/logs` -> `GET /api/v1/ops/logs`
9. `GET /api/supervisor` -> `GET /api/v1/supervisor/sessions`
10. research 动作统一至：`POST /api/v1/research/actions/*`（同步）

统一错误体：`{code,message,details,request_id,timestamp_utc}`。

## 7. 删除清单（文件/目录）

1. `src/domains/pmm/ops/*`
2. `src/domains/pmm/strategy_packs/*`
3. `src/domains/pmm/backtest/web_server.py`
4. `src/domains/research/web_server.py`
5. `web_ui/backtest/*`
6. `web_ui/research/*`
7. `scripts/python/pmm_strategy_packs.py`
8. `pmm_backtest.py` 的 `web` 子命令
9. `research cli` 的 `web` 命令
10. 运行产物纳入本地工件管理，不作为发布变更的一部分（`.gitignore` 生效）。

## 8. 验证结果（通过/失败/已知问题）

通过：

1. 编译检查通过。
2. 新增 store 测试通过。
3. 策略目录测试通过。
4. `/api/v1` 路由烟测通过。

已知问题（本次不修）：

1. `tests/pmm_tests/test_telegram_notifier.py::test_build_live_report_message_contains_pnl_and_positions`
2. `tests/pmm_tests/test_telegram_notifier.py::test_periodic_report_interval_and_alert_cooldown`
3. 非预期现象：尝试用 `uvicorn` 烟测失败（环境缺少 `uvicorn`），已改为直接运行 `strategy_dashboard_server` 模块完成烟测。

原因：测试调用签名与 notifier 当前实现参数不一致（缺失 fills/placed/canceled 等参数）。

## 9. 风险与回滚方案

风险：

1. 旧运行数据不迁移，切换后历史连续性中断。
2. 旧变量 `PMM_INSTANCE_DB_PATH` 仍可读但已弃用，若未切换会持续技术债。
3. `telegram_notifier` 测试失败属于现有基线问题，需后续专项修复。

回滚：

1. 回滚代码到切换前版本并恢复旧入口引用。
2. 旧库归档文件 `runtime/pmm_instances.db.deprecated_20260305` 可改名恢复使用。
3. 回滚后需要接受新库期间数据不可自动并回旧链路。

## 10. 后续待办（下一阶段）

1. 修复 `telegram_notifier` 签名与测试一致性。
2. 为 `src/interfaces/web/strategy_dashboard_server.py` 增加 API 单测。
3. 前端按 `docs/frontend/STRATEGY_DASHBOARD_PLAN.md` 接入 `/api/v1`。
4. 评估并补充认证/鉴权（当前内部场景默认无鉴权）。
