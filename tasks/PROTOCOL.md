# PM Agents Task Protocol

本协议用于 Codex、GLM 和网页端 GPT Pro 在同一仓库内跨线程协作。它补充根 `AGENTS.md`；天气、生产、资金和 skill 安全边界仍以根规则为准。

## 指令优先级

1. 用户当前会话的明确指令
2. 当前 `ACTIVE` task
3. `tasks/projects/<PROJECT_ID>.md` 的 owner decision 与 accepted state
4. 已接受的 review/evidence
5. 历史报告、旧 prompt、代码注释

历史材料只是 evidence，不会自动成为执行指令。未审阅结果不得写成 accepted state。

## 什么时候建 task

多步实现、研究、跨线程工作或需要 GLM/GPT Pro 交接时建 task。简单问答、一次性只读查询和无需交接的小修不强制建。

Task ID：`<PROJECT>-<WORKSTREAM>-<NN>`，例如 `WCIR-ORACLE-01`。

状态仅使用：`DRAFT`、`ACTIVE`、`BLOCKED`、`READY_FOR_REVIEW`、`ACCEPTED`、`PAUSED`。

## 线程与角色

- 一个线程只负责一个 task；并发写 task 必须 write scope 正交并使用独立 worktree。
- Codex 默认是 coordinator/integrator：准备 task、核对实现与 review、维护 accepted state。
- GLM 默认是 executor：只按 task 实施、验证并写 handoff，不能接受自己的工作。
- GPT Pro 默认是 reviewer：只读审阅 task、handoff、diff/tests/artifact，不能改代码或更新 state。
- 只有用户/owner 明确决定，才能把 `READY_FOR_REVIEW` 改成 `ACCEPTED` 或开放下一 stage/capability。

## 文件流转

```text
tasks/<PROJECT_ID>/queue/<TASK_ID>.md
  -> tasks/<PROJECT_ID>/active/<TASK_ID>.md
  -> tasks/<PROJECT_ID>/handoffs/<TASK_ID>.md
  -> tasks/<PROJECT_ID>/archive/<TASK_ID>.md
```

外部模型 packet 生成到 `tasks/<PROJECT_ID>/packets/`。每个项目独立保存 task、handoff 和 packet；`tasks/projects/` 只保存项目 current state。外部原始回复与 packet 一起保留，但 reviewer opinion 不等于 owner decision。

## 安全边界

- 未写进 task 的文件默认只读。
- 不跨项目顺手修改，不覆盖历史 evidence，不自行扩大 task。
- live、下单、部署、转账、债务、还款、删除数据或其他不可逆动作必须在 task 中获得明确授权。
- 执行者只能交付 `READY_FOR_REVIEW` 或 `BLOCKED`；完成后生成 handoff 并停止。
