# Weather 看板部署流程（Deploy Runbook）

Status: `current-reference`
Updated: 2026-06-27
关联：[WEATHER_DASHBOARD.md](WEATHER_DASHBOARD.md)（口径）· [OPS_RUNBOOK.md](OPS_RUNBOOK.md)

> 怎么把看板部署到公网域名，并持久化这套流程。
> **安全前提（必读）**：看板暴露真实交易数据（持仓 / PnL / 策略参数 / 城市池）。
> **绝不允许无鉴权公开暴露**。任何公网入口必须前置鉴权（Cloudflare Access 或反代 Basic Auth）。

## 0.1 当前实际部署（N100，权威）

公网 **https://dashboard.weekendleague.party** 已上线，落在 N100：

- **主机**：N100（`jiarui@192.168.0.200`），repo `~/projects/pm_agent`，分支 `develop`。
- **进程**：systemd **user** service `pm-agent-weather-dashboard.service`，
  `uvicorn weather_dashboard.api.app:app --host 127.0.0.1 --port 18080`（含 ExecStartPre 刷新探针注册表）。
  装/改服务：`scripts/ops/install_weather_dashboard_user_services.sh`。
- **公网入口**：`cloudflared tunnel run`（token 模式，ingress 在 Cloudflare 面板配 `dashboard.weekendleague.party → 127.0.0.1:18080`）。
- **node/npm**：在 `~/.local/bin`（非交互 ssh 的 PATH 里没有，要 `PATH=$HOME/.local/bin:$PATH`）。

### 实际发布步骤（git-first 代码 + dist 制品）
```bash
# 1) 本机：提交后推到 N100 裸库
git push n100 develop
# 2) N100：拉代码
ssh jiarui@192.168.0.200 'cd ~/projects/pm_agent && git checkout develop && git pull --ff-only origin develop'
# 3) 前端 dist：本机构建后 rsync（dist 是 gitignore 的构建产物）
cd frontend/strategy_dashboard && npm run build && cd ../..
rsync -az --delete frontend/strategy_dashboard/dist/ jiarui@192.168.0.200:/home/jiarui/projects/pm_agent/frontend/strategy_dashboard/dist/
#    （或在 N100 上 PATH=$HOME/.local/bin:$PATH npm ci && npm run build）
# 4) 重启服务（加载新 API 路由）
ssh jiarui@192.168.0.200 'export XDG_RUNTIME_DIR=/run/user/$(id -u); systemctl --user restart pm-agent-weather-dashboard.service'
# 5) 验证
curl -s https://dashboard.weekendleague.party/ | grep -o 'index-[A-Za-z0-9_-]*\.js'
```

### 鉴权现状：已加 HTTP Basic Auth（2026-06-27）
之前无鉴权、公开可读真实持仓。现已开启**应用层 Basic Auth**（`weather_dashboard/api/auth.py`，
env-gated）。N100 配置：systemd drop-in `~/.config/systemd/user/pm-agent-weather-dashboard.service.d/auth.conf`：
```ini
[Service]
Environment=DASHBOARD_AUTH=admin:<password>
```
- 未登录 → 401；`/health` 仍开放（liveness）。本机 dev/测试不设此 env → 无鉴权，不受影响。
- **改密码**：编辑该 drop-in 的 `DASHBOARD_AUTH`，`systemctl --user daemon-reload && systemctl --user restart pm-agent-weather-dashboard.service`。
- **生成强密码**：`openssl rand -hex 16`。

> Basic Auth 是即时止血。若要 SSO/邮箱登录，按 §4 上 Cloudflare Access（隧道层鉴权），
> 之后可把 `DASHBOARD_AUTH` 去掉只留 Access，或两者叠加。
> 隧道：token 模式，account `314dbf17…`，tunnel `b76927af-16d0-432e-a5eb-a79451dd208a`，
> ingress/Access 在 Cloudflare Zero Trust 面板配。

---

## 0. 架构：单制品

FastAPI (`weather_dashboard/api/app.py`) 会把构建后的前端 `frontend/strategy_dashboard/dist`
挂在同一个端口（默认 8000）一起服务。所以**一个进程 = 整个看板**：API + 静态前端。
公网只需要把这一个端口经鉴权隧道暴露出去。

数据来源（部署主机上必须可读）：
- `runtime/weather.db`（canonical 库）
- `runtime/weather_edge_v1/*`（探针脉搏 / 盘口快照镜像）
- `docs/analysis/**/generated/*/summary.json`（研究证据）

> 数据是 N100 的镜像，靠 `scripts/ops/sync_weather_remote.sh` 同步。部署主机要么是本机 Mac（定时 sync），
> 要么直接放 N100（数据在本地，最新）。**看板新鲜度 = 部署主机镜像新鲜度**，仍遵守「镜像≠生产」口径。

## 1. 构建 + 本地验证

```bash
cd frontend/strategy_dashboard && npm ci && npm run build      # 产出 dist/
cd ../.. && .venv/bin/python -m uvicorn weather_dashboard.api.app:app --host 127.0.0.1 --port 8000
# 打开 http://127.0.0.1:8000 应看到完整看板（API+FE 同端口）
```

环境变量（按需）：
- `WEATHER_DB_PATH`（默认 `runtime/weather.db`）
- `WEATHER_RUNTIME_ROOT`（默认 `runtime/weather_edge_v1`）
- `WEATHER_ANALYSIS_ROOT`（默认 `docs/analysis`）
- `WEATHER_SNAPSHOTS_DIR`、`CORS_ORIGINS`、`PORT`

## 2. 生产进程

用 systemd（Linux/N100）或 launchd/pm2 常驻：

```ini
# /etc/systemd/system/weather-dashboard.service
[Unit]
Description=Weather Dashboard (FastAPI + built FE)
After=network.target
[Service]
WorkingDirectory=/home/<user>/projects/pm_agent
ExecStart=/home/<user>/projects/pm_agent/.venv/bin/python -m uvicorn weather_dashboard.api.app:app --host 127.0.0.1 --port 8000
Restart=always
Environment=WEATHER_DB_PATH=runtime/weather.db
[Install]
WantedBy=multi-user.target
```

> 绑 `127.0.0.1` 不绑 `0.0.0.0`：只让本机隧道访问，不直接开公网端口。

数据新鲜：部署主机加一条 sync cron（若不是 N100 本机）：
```cron
*/30 * * * * cd /path/to/pm_agent && scripts/ops/sync_weather_remote.sh >> runtime/_dashboard_logs/sync.cron.log 2>&1
```

## 3. 公网入口：Cloudflare Tunnel（推荐）

无需开放入站端口、自带 TLS、可叠加 Cloudflare Access 鉴权。

```bash
# 安装 cloudflared（mac: brew install cloudflared / linux: 官方包）
cloudflared tunnel login                          # 浏览器授权到你的 Cloudflare 账号+域名
cloudflared tunnel create weather-dashboard       # 生成 tunnel + 凭证
# ~/.cloudflared/config.yml:
#   tunnel: <TUNNEL_ID>
#   credentials-file: /home/<user>/.cloudflared/<TUNNEL_ID>.json
#   ingress:
#     - hostname: dash.example.com
#       service: http://127.0.0.1:8000
#     - service: http_status:404
cloudflared tunnel route dns weather-dashboard dash.example.com
cloudflared tunnel run weather-dashboard          # 或装成 systemd 服务常驻
```

**快速临时 URL（仅自测，勿放敏感数据长期暴露）**：
`cloudflared tunnel --url http://127.0.0.1:8000` → 给一个 `*.trycloudflare.com`。

## 4. 鉴权（强制）

二选一，**上线前必须有**：

- **Cloudflare Access**（推荐）：Zero Trust → Access → 给 `dash.example.com` 建 Application，
  Policy 限定你的邮箱/Google 登录。隧道层就挡住未授权访问，看板本身不用改。
- **反代 Basic Auth**（无 Cloudflare 账号时）：nginx/Caddy 前置 `auth_basic` + TLS，再反代到 `127.0.0.1:8000`。

> 看板目前**没有内置登录**。鉴权放在入口层（隧道/反代）。若以后要细粒度权限，再在 API 加鉴权中间件。

## 5. 上线安全清单

- [ ] 进程绑 `127.0.0.1`，未直接开公网端口
- [ ] 公网入口前置鉴权（Access / Basic Auth）已生效，匿名访问被挡
- [ ] 只读确认：看板无任何写/下单接口（设计即只读）
- [ ] DB 里无私钥/助记词（`runtime/weather.db` 只有成交/结算/配置，不含钱包密钥——上线前核一遍）
- [ ] sync cron 正常，新鲜度口径仍显示「镜像≠生产」
- [ ] 回滚：停 `cloudflared` 即下线公网；停 systemd 即停服务

## 6. 更新发布

```bash
git pull && cd frontend/strategy_dashboard && npm ci && npm run build && cd ../..
sudo systemctl restart weather-dashboard      # FE 是静态 dist，重启即生效
```
