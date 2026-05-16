# Task Protocol

## 文件约定

| 文件 | 写入方 | 用途 |
|---|---|---|
| `tasks/current_task.md` | Claude（Orchestrator） | 当前任务指令 |
| `tasks/task_result.md` | MiniMax（Executor） | 执行结果汇报 |
| `tasks/progress.md` | 双方 | 总体进度记录 |

## MiniMax 收到任务后的工作流

1. 读 `tasks/current_task.md`
2. 按指令执行（写代码、跑测试、跑命令）
3. 把结果写入 `tasks/task_result.md`（格式见下）
4. **不要**修改 `tasks/current_task.md`，等 Claude 来更新

## task_result.md 格式

```markdown
# Result: <Task 名称>

## Status
DONE / PARTIAL / FAILED

## Files Changed
- Created: path/to/file.py
- Modified: path/to/other.py

## Test Output
```
pytest ... 的输出贴这里
```

## Notes
遇到的问题、偏离任务的地方、需要 Claude review 的点
```

## 注意事项
- 每次只执行 current_task.md 里的任务，不要超前
- 遇到不确定的设计决策，写在 Notes 里，不要自己猜
- 命令都在 `/home/rui/projects/pm_agent` 下运行，记得 `PYTHONPATH=.`
