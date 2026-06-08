# Weather Dashboard 故障排查手册

Status: current-reference
Updated: 2026-06-09 metadata pass; preserve content dates below
Source of truth: no
Superseded by / Used by: WEATHER_DOCS_INDEX.md; reference only, not production source of truth

> 本文记录 2026-05 调试过程中遇到的所有问题及根因分析，供后续运维参考。

---

## 一、快速启动（TL;DR）

```bash
cd /home/rui/projects/pm_agent
scripts/weather_dashboard/run_stack.sh
```

脚本自动处理：DB 重建、API 启动、前端启动、Windows 端口转发（含 mirrored/NAT 两种模式自适应）。

| 参数 | 效果 |
|---|---|
| _(无参数)_ | 全量 rebuild DB + 启动 API + FE |
| `--no-rebuild` | 跳过 DB 重建，仅启动 API + FE |
| `--api-only` | 仅启动 API |
| `--fe-only` | 仅启动 FE |
| `--status` | 查看当前 DB / 进程状态，不做任何操作 |

启动后访问：

- Dashboard：`http://localhost:5174/weather/runs`
- 实时监控：`http://localhost:5174/weather/live`
- API 文档：`http://localhost:8000/docs`

停止服务：

```bash
pkill -F runtime/_dashboard_logs/api.pid 2>/dev/null
pkill -F runtime/_dashboard_logs/fe.pid 2>/dev/null
```

---

## 二、遇到过的问题及根因

### 问题 1：API 启动失败 —— WSL2 mirrored 网络 + netsh portproxy 冲突

**现象**

- WSL 内 `ss -tlnp` 显示端口 8000 空闲
- 但 `uvicorn` 绑定时报 `EADDRINUSE`（端口被占用）
- Windows 侧 `netstat -ano | findstr :8000` 显示 `svchost.exe` 在监听 `0.0.0.0:8000`

**根因**

`.wslconfig` 中启用了 `networkingMode=mirrored`。在 mirrored 模式下，Windows 和 WSL 共享同一个 loopback 网卡，WSL 端口直接在 Windows 侧可见，**不需要** portproxy。但如果之前在 NAT 模式下创建了 portproxy，切换到 mirrored 后这些 proxy 仍然存在，`svchost` 会占用 Windows 侧端口，反而阻止 WSL 重新绑定同一端口。

**修复**

删除 portproxy 即可：

```powershell
# 在管理员 PowerShell 中运行
netsh interface portproxy delete v4tov4 listenport=8000 listenaddress=0.0.0.0
netsh interface portproxy delete v4tov4 listenport=5174 listenaddress=0.0.0.0
```

`run_stack.sh` 已自动化：脚本检测 `.wslconfig` 的 `networkingMode`，若为 `mirrored` 则删除旧 proxy；若为 `nat` 则照常添加 proxy。

**判断当前模式**

```bash
# WSL 内执行
powershell.exe -Command "(Get-Content \"\$env:USERPROFILE\.wslconfig\") -match 'networkingMode'"
```

---

### 问题 2：前端请求打到错误端口

**现象**

浏览器 DevTools Network 显示请求到 `http://localhost:8765/api/...`，服务器无响应。

**根因**

`frontend/strategy_dashboard/.env.local` 中 `VITE_WEATHER_API` 被设置为 `http://localhost:8765`（历史调试遗留）。这个文件不被 git 追踪（在 `.gitignore` 中），跨会话/机器时容易丢失或错误。

**修复**

```bash
# frontend/strategy_dashboard/.env.local
VITE_WEATHER_API=http://localhost:8000
```

注意：`.env.local` 的变更需要完全重启 Vite 才能生效（`--reload` 热重载不会读取 env 文件变更）：

```bash
pkill -F runtime/_dashboard_logs/fe.pid
scripts/weather_dashboard/run_stack.sh --fe-only
```

---

### 问题 3：CORS 报错 —— 浏览器用 `127.0.0.1` 但 CORS 只允许 `localhost`

**现象**

DevTools 显示 API 返回 HTTP 200，但请求列带红色 ×，Console 报：

```
Access to fetch at 'http://localhost:8000/api/...' from origin 'http://127.0.0.1:5174'
has been blocked by CORS policy: No 'Access-Control-Allow-Origin' header
```

**根因**

Chrome 有时将 `localhost` 解析为 `::1`（IPv6），但 WSL uvicorn 只绑定了 IPv4。此时浏览器实际通过 `127.0.0.1` 访问前端，Origin 为 `http://127.0.0.1:5174`，而 API 的 CORS 正则只匹配 `https?://localhost(:\d+)?`，导致 `127.0.0.1` 来源被拒绝。

**修复**（`weather_dashboard/api/app.py`）

```python
# 修改前
allow_origin_regex=r"https?://localhost(:\d+)?",

# 修改后
allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
```

---

### 问题 4：API 进程在调用 shell 退出后被 kill

**现象**

Claude 在 shell 中用 `nohup ... &` 启动 uvicorn，shell 退出后 API 进程随之消失。

**根因**

`nohup` 只屏蔽 SIGHUP，但某些 shell/会话管理方式（如 claude-code 的 task executor）在退出时直接杀整个进程组（SIGKILL 或 SIGTERM），`nohup` 无法防御。

**修复**

用 `setsid` 将进程放入独立进程组：

```bash
setsid nohup uvicorn ... >/dev/null 2>&1 &
```

`run_stack.sh` 同样适用于前端：

```bash
setsid nohup npm run dev ... >/dev/null 2>&1 &
```

---

### 问题 5：React 页面空白 —— `null.toFixed()` 导致整棵组件树 crash

**现象**

API 返回正常数据（DevTools Preview 可见），但页面完全空白，Console 报：

```
TypeError: Cannot read properties of null (reading 'toFixed')
    at WeatherStrategyDetailPage.tsx:1474:76
    at Array.map
```

**根因**

`analytics` 端点在策略没有任何已结算交易时，返回 `capital: null`（以及 `pnl: null` 等）。前端对这些字段直接调用 `.toFixed()` 而没有 null 检查，导致 `Array.map` 中途抛出异常，React 整棵树 unmount，页面空白。

源码行号对不上（报 1474 但文件只有 1167 行）是因为 source map 过期/未刷新，实际问题分布在多处。

**受影响的位置**（`WeatherStrategyDetailPage.tsx`）

| 表格 | 字段 | 触发场景 |
|---|---|---|
| Analytics 分析表 | `row.capital` | 无结算时为 null |
| Open/Closed Positions | `row.filled_price` | 未成交订单 |
| Open/Closed Positions | `row.filled_shares` | 未成交订单 |
| OrderMiniTable | `row.order_cost_usd` | 某些订单可能为 null |
| OrderMiniTable | `row.filled_shares ?? row.order_shares` | 链式 fallback 末尾没有 `?? 0` |

**修复模式**

```typescript
// 修复前
row.capital.toFixed(2)
row.filled_price.toFixed(3)
(row.filled_shares ?? row.order_shares).toFixed(2)

// 修复后
(row.capital ?? 0).toFixed(2)
(row.filled_price ?? 0).toFixed(3)
(row.filled_shares ?? row.order_shares ?? 0).toFixed(2)
```

**预防原则**

凡是来自 API 的数值字段，在调用 `.toFixed()` 前必须加 `?? 0` 兜底，或者加 `if (x == null) return "—"` 短路。不要依赖 TypeScript 类型标注来保证非 null——API 实际返回和类型定义可能不一致。

---

## 三、排查流程图

```
页面没数据或空白？
│
├─ API 健康检查：curl http://localhost:8000/health
│    └─ 失败 → 看 runtime/_dashboard_logs/api.log
│         ├─ EADDRINUSE → 问题1：检查 portproxy，执行 run_stack.sh 自动清理
│         └─ 其他错误 → 检查 Python 环境 / DB 路径
│
├─ API 健康但前端无请求 → 看 DevTools Network
│    └─ 请求打到错误端口 → 问题2：检查 .env.local
│
├─ 有请求但 CORS 报错 → 问题3：检查 Origin header vs CORS regex
│
└─ 有数据但页面空白 → 看 DevTools Console
     └─ TypeError: null.toFixed → 问题5：加 ?? 0 兜底
```

---

## 四、关键文件速查

| 文件 | 说明 |
|---|---|
| `scripts/weather_dashboard/run_stack.sh` | 一键启动脚本（含 mirrored/NAT 自适应） |
| `frontend/strategy_dashboard/.env.local` | 前端 API 地址配置（不在 git 中，需手动确认） |
| `weather_dashboard/api/app.py` | FastAPI 入口，CORS 配置在此 |
| `frontend/strategy_dashboard/src/pages/weather/WeatherStrategyDetailPage.tsx` | 策略详情页，数值渲染的 null 兜底都在此 |
| `runtime/_dashboard_logs/api.log` | API 运行日志 |
| `runtime/_dashboard_logs/fe.log` | 前端 Vite 日志 |
| `runtime/_dashboard_logs/api.pid` / `fe.pid` | 进程 PID，用于停止服务 |
