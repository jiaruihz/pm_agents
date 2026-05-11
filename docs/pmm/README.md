# PMM 文档导航

> **最近更新**：2026-05-11

## 快速开始

1. **[ARCHITECTURE.md](ARCHITECTURE.md)**：系统概览、目录结构、依赖规则
2. **[CODE_IMPLEMENTATION.md](CODE_IMPLEMENTATION.md)**：模块代码走读与实现说明
3. **[MAIN_FLOW.md](MAIN_FLOW.md)**：tick 主循环生命周期与时序图

## 策略与回测

- **[STRATEGY_PLAYBOOK.md](STRATEGY_PLAYBOOK.md)**：策略设计与信号逻辑
- **[BACKTEST_SCENARIO_METHOD.md](BACKTEST_SCENARIO_METHOD.md)**：回测方法、场景生成、撮合模型
- **[PAPER_RUNBOOK.md](PAPER_RUNBOOK.md)**：paper 实盘演练的运行、监控、日志与告警
- **[../../../strategies/README.md](../../../strategies/README.md)**：全局策略目录（manifest/runbook/params）

## 参考

- **[METRICS_FORMAT.md](METRICS_FORMAT.md)**：指标日志字段规范
- **[TODO_IMPROVEMENTS.md](TODO_IMPROVEMENTS.md)**：后续演进路线

## 历史文档

已完成阶段的历史计划文档统一放在 `../archive/`：

- `../archive/CODE_REVIEW.md`
- `../archive/REFACTOR_PLAN.md`
- `../archive/SKILL_ARCHITECTURE_PLAN.md`
- `../archive/EXECUTION_PLAN_REALDATA_MULTI_LEVEL.md`
- `../archive/STRATEGY_KEY_ROUTING_PLAN.md`
- `../archive/WEATHER_THETA_NO_PROGRESS.md`

## 运行示例

```bash
# 环境变量（完整列表见 ARCHITECTURE.md）
export PMM_TOKEN_IDS="token1,token2"
export PMM_EXEC_MODE="paper"
export PMM_MARKET_DATA_SOURCE="ws"
export PMM_STRATEGY_KEY="multi_level_v1"

# 启动
python -m src.strategies.pmm.main
```
