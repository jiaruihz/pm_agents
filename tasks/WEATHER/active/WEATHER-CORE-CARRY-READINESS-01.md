# Core Carry 5+5 readiness research

Status: READY_FOR_REVIEW
Repo: /Users/deepsleep/projects/pm_agents
Worktree: /Users/deepsleep/projects/pm_agents-worktrees/core-carry-live-readiness-20260915
Base: ae3b848213e8e39875e24238e3402a051a79c0b3

目标：评估用户提示词并推进冻结 O 的5股经济、选型偏差、生产隔离与风险就绪研究。
授权：离线调查、研究代码和测试；不拟合、不调参、不部署、不下单、不改变现有collector或资金。
Write scope: existing family runner plus core_carry_readiness helper, tests/test_core_carry_readiness.py; family context/index/registry; this task/handoff; new external run=20260915_live_readiness. All production paths read-only.
验收：封存身份与既有预测 parity；真实保存盘口5股重算/缺口显式；费用压力重新选票；日期尾部/选模准入；B当前执行差异；P0 gate有证据；针对性与相关回归、独立只读review；可复跑报告。
决定：主edge buffer=0（沿生产taker规则），每策略25美元/BJ日，合计50；这些是预注册研究规格并非批准资金。14日为可提议的执行检查点，原30日效力协议不被覆盖。
非目标：完整生产多策略接入、当下账户PnL/余额审计、model promotion、外部reviewer发送。
产物：/Volumes/jrs-archive/pm_agents/research/artifact_store/core_carry/run=20260915_live_readiness
Handoff: tasks/WEATHER/handoffs/WEATHER-CORE-CARRY-READINESS-01.md

研究已完成；live gate NOT_READY。详见handoff。owner尚未接受或授权下一步。
