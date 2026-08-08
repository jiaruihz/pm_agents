# 项目常驻上下文重构提案（历史 tombstone）

Status: `superseded / applied-2026-06-19`
Source of truth: no
Current authority: repository `AGENTS.md` and `CLAUDE.md`

这份提案已在 2026-06-19 应用，不再作为待执行计划保留全文。耐久结论只有三条：

- 常驻提示词只保留系统主轴、不可逆安全边界、skill 路由和高频入口；细节指向权威文档。
- 所有研究与执行挂到统一血缘，避免每个 agent 另建脚本、表和“当前结论”。
- `AGENTS.md` 与 `CLAUDE.md` 的核心规范保持一致，运行事实不能硬编码进系统提示词。

旧提案中的 N100 主机职责、WSL、非 canonical DB、`weather-predict` 生产路径和当时的行数目标均已过期，
不得复制回当前提示词。需要审计当前上下文时直接比较 `AGENTS.md`、`CLAUDE.md` 与
`docs/WEATHER_DOCS_INDEX.md`；完整历史设计可从本文件的 git history 恢复。
