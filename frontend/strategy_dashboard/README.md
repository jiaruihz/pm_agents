# Strategy Dashboard Frontend

统一策略大盘前端（React + TypeScript + Vite）。

## 运行

```bash
cd frontend/strategy_dashboard
npm install
npm run dev
```

默认开发地址：`http://127.0.0.1:5174`

## 数据模式

通过 `VITE_DASHBOARD_DATA_MODE` 切换：

- `mock`（默认）：前端本地假数据，后端改造中也可开发
- `http`：调用统一 BFF `/api/v1/*`

可选环境变量：

- `VITE_BFF_ORIGIN`：Vite 代理目标（默认 `http://127.0.0.1:8011`）

## 对接后端

后端服务入口：`src/interfaces/web/strategy_dashboard_server.py`
