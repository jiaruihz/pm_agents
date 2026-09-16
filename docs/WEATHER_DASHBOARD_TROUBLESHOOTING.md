# Weather Dashboard Troubleshooting

Status: current-reference
Updated: 2026-08-06 Mac controller architecture
Source of truth: no; production facts come from manifest/controller

## 快速检查

```bash
.venv/bin/python scripts/ops/weather_production_manifest.py --strict
.venv/bin/python scripts/ops/weather_production_ctl.py health
.venv/bin/python scripts/ops/weather_production_ctl.py plan
.venv/bin/python scripts/ops/weather_storage_identity_audit.py
curl -fsS http://127.0.0.1:8000/health
curl -fsSI http://127.0.0.1:5174/
```

页面：`http://127.0.0.1:5174/weather/runs`、`/weather/live`；API 文档：`http://127.0.0.1:8000/docs`。

## API 不健康

1. 先看 manifest 的 `weather_dashboard_api` checkout、PID/session、health 与 DB handles；
2. 看 controller plan 的依赖和建议动作；
3. 确认 DB route healthy、兼容入口与 physical canonical 同 inode；
4. 需要恢复时只用 controller 对登记实例的 restart/reconcile 合同。

不要用 direct uvicorn、nohup、default tmux、旧 API LaunchAgent 或 `run_stack.sh` 启动 API。

## FE 不健康

FE 是 `com.pm-agents.weather-fe` LaunchAgent，不属于 JRS tmux。检查：

- LaunchAgent 是否 loaded、实际 PID/命令和 checkout；
- `frontend/strategy_dashboard/dist` 是否来自目标提交的最新构建；
- Vite/API target 是否为 `http://127.0.0.1:8000`；
- 浏览器 Network/Console 中是否是 bundle 404、CORS、API schema 或 React runtime error。

开发 Vite 进程存在不能证明生产 FE 健康。重新构建后必须重载既有 FE LaunchAgent并验证页面实际 bundle。

## 页面有数据但陈旧

先区分三层：

1. Mac raw producer 是否 fresh；
2. canonical refresh 是否成功结束；
3. API 是否读取 canonical DB、目标表的 max timestamp 是否覆盖目标窗口。

```bash
scripts/ops/weather_dashboard_refresh.sh --status
```

需要增量刷新时无参数请求唯一 bounded one-shot。查看
`runtime/weather_edge_v1/canonical_refresh/{last_exit_status,tmux.log}`；仍在运行时不要重复触发。缺 raw 时先修 producer，
不能靠重复 rebuild 伪造新鲜度。

## 页面空白或局部崩溃

先看浏览器 Console 和失败 API payload。常见根因：

- nullable 数值直接调用 `.toFixed()`；
- API schema 与 TypeScript 类型漂移；
- 空数组/缺字段被当成已有结算、持仓或盘口；
- FE bundle 与 API checkout 不同版本。

修复应在类型与渲染边界处理 `null/undefined`，并为真实空数据 fixture 加 regression test；不要只在单页硬编码默认数字。

## CORS 或端口错误

当前本机标准是 FE `127.0.0.1:5174`、API `127.0.0.1:8000`。同时核对 `localhost` 与 `127.0.0.1` origin；
前端环境变量变化需要重新构建/重载。WSL portproxy、Windows mirrored networking、N100 18080 和历史 8765 端口不属于
当前故障路径。

## 不能做的恢复

- 不恢复 N100 systemd/dashboard tunnel；
- 不 `rsync` dist 到远端；
- 不用 `pkill` PID 文件批量停服务；
- 不恢复 `run_stack.sh --api-only/--fe-only` 等退役参数；
- 不在 canonical refresh 运行中重复启动 ingest/rebuild。

部署与发布见 [`WEATHER_DASHBOARD_DEPLOY.md`](WEATHER_DASHBOARD_DEPLOY.md)，全链路入口见
[`OPS_RUNBOOK.md`](OPS_RUNBOOK.md)。
