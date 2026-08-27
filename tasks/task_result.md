# Compatibility Pointer

旧单文件 executor result 已迁到 `tasks/archive/legacy-plan-a/task_result.md`。

新任务一律写入：

```text
tasks/handoffs/<TASK_ID>.md
```

使用 `python scripts/ops/ai_task_ctl.py handoff --task <task-path>` 生成草稿。
