# WEATHER-ENGINEERING-AUDIT-01

TASK_ID: WEATHER-ENGINEERING-AUDIT-01
PROJECT_ID: WEATHER
WORKSTREAM: ENGINEERING-QUALITY
STATUS: ACTIVE
ROLE: COORDINATOR
REPO_ROOT: /Users/deepsleep/projects/pm_agents
BASE_COMMIT: b13876719d96dc54db9933ea42da7c22f468d134
OWNER: owner
CREATED_AT: 2026-08-30T00:00:00+08:00
HANDOFF_PATH: tasks/WEATHER/handoffs/WEATHER-ENGINEERING-AUDIT-01.md

## Goal

对当前 `pm_agents` 工程做一次可复现的全局工程审阅，并直接完善能够明确证明、不会改变策略/资金/生产行为的工程缺陷：测试与静态检查入口、CI/本地一致性、仓库结构债务检测、失败可诊断性和开发者接手路径。

## Frozen input state

- HEAD: `b13876719d96dc54db9933ea42da7c22f468d134`
- branch: `codex/market-ladder-kink-v1`
- pre-existing dirty state: `128` modified paths and `177` untracked paths (`305` total)
- snapshot: `/private/tmp/pm-agents-engineering-review-pre.txt`

## Write scope

- this task and matching handoff
- `tasks/projects/WEATHER.md`
- clean repository-wide engineering configuration and CI files
- a new, isolated engineering audit/check entrypoint and focused tests/docs
- small fixes in otherwise clean files only when a failing check proves the defect

## Non-goals

- Do not overwrite, format, stage or commit any of the 305 pre-existing dirty paths.
- Do not change strategy eligibility, model math, entry/sizing/execution policy, live/shadow mode, production release pins, runtime roots, order/fill state or canonical data.
- Do not deploy, restart production, access N100, rebuild facts or delete research/runtime evidence.
- Do not turn broad historical/research debt into mass refactors in this task.

## Acceptance

1. Inventory current validation surfaces and run the strongest safe reproducible checks available.
2. Separate baseline/user-change failures from defects introduced or fixed by this task.
3. Implement at least one material engineering improvement with focused tests and a scoped commit, without touching pre-existing dirty paths.
4. Preserve every frozen dirty path; any concurrent state transition is explicitly reconciled.
5. Complete one independent read-only review of task-owned code/config and address findings.
6. Produce a concise prioritized findings list with evidence, fixed/not-fixed status and follow-up boundaries.

## Allowed disposition

- READY_FOR_REVIEW
- BLOCKED
