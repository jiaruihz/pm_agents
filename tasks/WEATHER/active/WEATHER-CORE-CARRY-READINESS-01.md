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

## 用户追问后的时钟/日期补审

Write scope追加同run/clock_data_audit及本研究入口解释校正，生产只读。恢复1043测试raw的完整request/response/parsed链，全部区间相容；旧strict删546条导致重选票，不能再以旧−2.4194推断去异常后模型亏损。主O38票+$4.9188不变；9/14目标日9赢+$2.5761，之前仍+$2.3427；依赖9/14的是相对B领先。48票独立算术/去重/预算通过，producer clock现有tests通过。当前解释以clock_data_audit/CLOCK_AND_DATE_CORRECTION.md为准；原sealed表保留，P0 NOT_READY不变。
