# PM Agents Project Tasks

本目录是仓库内的轻量项目控制面：项目 current state、独立线程 task、GLM 执行包、GPT Pro 审阅包和 handoff 都在这里维护。

## 入口

- 项目索引：`tasks/PROJECTS.md`
- 长期协议：`tasks/PROTOCOL.md`
- 项目状态：`tasks/projects/<PROJECT_ID>.md`
- 当前任务索引：`tasks/current_task.md`
- 工具：`scripts/ops/ai_task_ctl.py`

## 新线程

```bash
python scripts/ops/ai_task_ctl.py new \
  --project WCIR \
  --workstream ORACLE \
  --title "一句话任务"
```

补齐生成的 `tasks/queue/<TASK_ID>.md` 后，把状态改成 `ACTIVE` 并移到 `tasks/active/`。新线程只需说：

```text
执行 tasks/active/<TASK_ID>.md；按 tasks/PROTOCOL.md 工作，完成后生成 handoff。
```

## GLM 执行包

```bash
python scripts/ops/ai_task_ctl.py pack \
  --role glm \
  --task tasks/active/<TASK_ID>.md
```

## GPT Pro 审阅包

```bash
python scripts/ops/ai_task_ctl.py handoff --task tasks/active/<TASK_ID>.md

python scripts/ops/ai_task_ctl.py pack \
  --role gptpro \
  --task tasks/active/<TASK_ID>.md \
  --handoff tasks/handoffs/<TASK_ID>.md \
  --attachment path/to/review-artifact.json
```

`--attachment` 可以重复使用；提供附件时工具会同时生成一个可直接上传的 ZIP。网页 reviewer 无法读取未打包的本机路径，因此 task/handoff 中列出的关键 artifact 应通过该参数加入。GPT Pro 回复是 review evidence；owner 接受后才更新项目页和归档 task。

## 校验

```bash
python scripts/ops/ai_task_ctl.py check
```

本结构不建设数据库、服务或自动 promotion；业务 truth 仍在代码、canonical artifact、production manifest 和原有领域文档。
