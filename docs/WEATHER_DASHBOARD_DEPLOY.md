# Weather Dashboard Deployment

Status: current-reference
Updated: 2026-08-06 Mac controller architecture
关联：[WEATHER_DASHBOARD.md](WEATHER_DASHBOARD.md) · [OPS_RUNBOOK.md](OPS_RUNBOOK.md)

## 当前架构

- API：`production.yaml:weather_dashboard_api`，由 weather production controller 在 canonical JRS context 管理，
  默认 health endpoint 为 `http://127.0.0.1:8000/health`。
- FE：独立 `com.pm-agents.weather-fe` LaunchAgent，默认 `http://127.0.0.1:5174`；它不读写 JRS，因此不并入
  canonical JRS tmux。
- DB：API 读取 production contract 解析出的 canonical DB；仓库 `runtime/weather.db` 必须与 JRS physical
  canonical 为同一 device/inode。
- refresh：只有登记的 bounded canonical refresh one-shot；`run_stack.sh` 不再启停 API/FE。

N100 systemd、`rsync dist`、WSL、旧 `com.pm-agents.weather-api` LaunchAgent、单进程 API+静态 FE 和公网 tunnel
部署均为历史形态，不是当前发布步骤。

## 发布前检查

```bash
.venv/bin/python scripts/ops/weather_production_manifest.py --strict
.venv/bin/python scripts/ops/weather_production_ctl.py health
.venv/bin/python scripts/ops/weather_production_ctl.py plan
.venv/bin/python scripts/ops/weather_storage_identity_audit.py
```

存在 manifest critical、DB split、非 canonical DB consumer、异常 checkout 或 JRS probe 失败时停止发布，先修 P0。

## API 变更

API 属于 weather production 变更：

1. 修改代码并运行 API/DB 定向测试；
2. 提交代码；
3. 用 controller 对 `weather_dashboard_api` 的登记合同预览并重启；
4. 验证 production SHA、实例健康、DB identity、`/health` 与目标 API；
5. 比较 prechange manifest，不能带走其他 JRS session。

不要直接运行 uvicorn、底层 start script、raw tmux 或恢复旧 API LaunchAgent。

## FE 变更

```bash
cd frontend/strategy_dashboard
npm ci
npm run build
```

构建和前端测试通过后提交源码。当前 FE 生命周期归 `com.pm-agents.weather-fe` LaunchAgent；发布时核对它实际加载的
checkout/命令和新 bundle，再用该 LaunchAgent 的既有合同重载并验证 `http://127.0.0.1:5174`。不得把开发 Vite
进程、临时 shell 或旧 N100 dist 当生产 FE。

若实际 LaunchAgent checkout 与目标提交不一致，先修 manifest/部署合同，不直接复制 dist 绕过 git 审计。

## Canonical refresh

```bash
scripts/ops/weather_dashboard_refresh.sh --status
scripts/ops/weather_dashboard_refresh.sh
```

无参数只请求唯一 bounded refresh。状态与证据读取
`runtime/weather_edge_v1/canonical_refresh/{last_exit_status,tmux.log}`。任务正在运行时不得重复触发；全量重建必须有
显式授权并使用 `scripts/weather_dashboard/run_stack.sh --rebuild`，它不会启停服务。

## 公网与鉴权边界

本文不声明当前存在公网部署。若新增公网入口，它属于新的安全/生产变更，必须先确认：

- API/FE 仍只绑定 loopback 或受控反代；
- Cloudflare Access、SSO 或等价鉴权在匿名访问前生效；
- 不暴露私钥、助记词、环境变量或写/下单控制面；
- tunnel/反代配置有版本审计、回滚和实际匿名拒绝测试。

历史域名、tunnel id、Basic Auth 密码位置和 N100 service 不得从旧文档恢复。
