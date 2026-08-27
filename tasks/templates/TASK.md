# <TASK_ID> — <TITLE>

TASK_ID: <PROJECT>-<WORKSTREAM>-<NN>
PROJECT_ID: <PROJECT>
WORKSTREAM: <WORKSTREAM>
STATUS: DRAFT
ROLE: EXECUTOR
REPO_ROOT: /Users/deepsleep/projects/pm_agents
BASE_COMMIT: <FULL_COMMIT>
OWNER: owner
CREATED_AT: <ISO8601>
HANDOFF_PATH: tasks/handoffs/<TASK_ID>.md

## Objective

一个主要目标，写清要得到的结果。

## Decision question

完成后要回答的主要问题。

## Write scope

- 精确目录或文件族；未列出的默认只读。

## Material inputs

- canonical inputs、evidence，以及需要用 `pack --attachment` 加入外部 review bundle 的 artifact。

## Constraints

- 必须保持的 PIT、schema、资金、runtime、性能或兼容边界。

## Non-goals

- 本轮明确不做什么。

## Acceptance

- 可验证的完成条件、数字和 evidence。

## Validation

- 必须真实执行的测试、回放或检查命令。

## Allowed dispositions

- `READY_FOR_REVIEW`
- `BLOCKED`

## Required outputs

- 代码、报告、artifact、hash、handoff。

## Stop condition

达到 acceptance 后生成 handoff 并停止，不自动开始下一 task/stage。
