# PMM 文档导航

> **最近更新**：2026-08-30
>
> **当前定位**：这里保存 legacy PMM 的可运行代码与历史设计 baseline，当前主线仍是
> weather。新的 MM 路线不直接恢复旧 PMM live，而是先走
> **[weather-first MM 第一性原理评审包](../design/weather_market_making/WEATHER_FIRST_MM_GPT_PRO_REVIEW_PACKET_V1.md)**：
> 共享 truth/execution/accounting substrate，分开验证 weather maker acquisition、
> inventory lifecycle、selective weather MM 与 generic two-sided MM。
>
> **安全边界**：PMM 默认是 `paper`；只有精确设置
> `PMM_EXECUTION_MODE=live` 才会进入 legacy live broker。这里的命令不属于 weather
> 生产控制面，也不授权真实下单。更早的方案与执行记录统一从
> [历史资产索引](../archive/README.md) 进入。

## 快速开始

1. **[ARCHITECTURE.md](ARCHITECTURE.md)**：系统概览、目录结构、依赖规则
2. **[CODE_IMPLEMENTATION.md](CODE_IMPLEMENTATION.md)**：模块代码走读与实现说明
3. **[MAIN_FLOW.md](MAIN_FLOW.md)**：tick 主循环生命周期与时序图

## 策略与回测

- **[STRATEGY_PLAYBOOK.md](STRATEGY_PLAYBOOK.md)**：策略设计与信号逻辑
- **[BACKTEST_SCENARIO_METHOD.md](BACKTEST_SCENARIO_METHOD.md)**：回测方法、场景生成、撮合模型
- **[PAPER_RUNBOOK.md](PAPER_RUNBOOK.md)**：paper 实盘演练的运行、监控、日志与告警
- **[../../src/strategies/README.md](../../src/strategies/README.md)**：全局策略目录（manifest/runbook/params）

## 参考

- **[WEATHER_FIRST_MM_GPT_PRO_REVIEW_PACKET_V1.md](../design/weather_market_making/WEATHER_FIRST_MM_GPT_PRO_REVIEW_PACKET_V1.md)**：当前 MM 研究与阶段设计；可直接交给 GPT Pro 审阅
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
export PMM_EXECUTION_MODE="paper"
export PMM_MARKET_DATA_SOURCE="ws"
export PMM_STRATEGY_KEY="multi_level_v1"

# 启动
python -m src.strategies.pmm.main
```
