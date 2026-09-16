# <TASK_ID> Handoff

TASK_ID: <TASK_ID>
PROJECT_ID: <PROJECT>
DISPOSITION: READY_FOR_REVIEW
ROLE: EXECUTOR
BASE_COMMIT: <FULL_COMMIT>
FINAL_COMMIT: <FULL_COMMIT_OR_NONE>
COMPLETED_AT: <ISO8601>

## What was done

- 实际完成的动作。

## What was not done

- 未做事项及原因；没有则写 `NONE`。

## Claims

- 编号列出结论。

## Evidence for each claim

- 对应 claim 编号，给命令、数字、路径、commit、hash 或可复现 evidence。

## Files changed

- 文件路径及用途。

## Tests and results

- 真实执行的命令和 pass/fail/skip 数字。

## Artifacts and hashes

- 路径、size、SHA256；没有则写 `NONE`。

## Schema / config / runtime impact

- 是否变化；没有则写 `NONE`。

## Impact radius

- 受影响窗口、数据、decision、order 或外部状态。

## Deviations and blockers

- 与 task 假设/范围的偏差和 blocker。

## Explicitly not authorized

- 不授权的下一阶段、live 或不可逆动作。
