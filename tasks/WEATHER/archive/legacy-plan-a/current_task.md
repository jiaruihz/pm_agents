# Task C: Frontend Build Verification + Polish

## 背景

项目：`/home/rui/projects/pm_agent/`
前端目录：`frontend/strategy_dashboard/`（React + TypeScript + Vite）

Claude 已预建了天气仪表盘的所有页面组件。你的任务是**验证构建通过、修复 TypeScript 错误、补全缺失配置**，以及新建两个辅助文件。

**不要修改任何现有文件**，只新建文件或在本任务说明范围内修改。

---

## 已有文件（Claude 写的，不要改）

```
src/data/weather-types.ts       — 类型定义 + METRIC_LABELS
src/data/weather-http.ts        — API client
src/pages/weather/MetricsCard.tsx
src/pages/weather/WeatherRunsPage.tsx
src/pages/weather/WeatherComparePage.tsx
src/pages/weather/WeatherHistoryPage.tsx
src/app/App.tsx                 — 已加 weather 路由
src/components/PageFrame.tsx    — 已加 sidebar 导航
```

---

## 任务 1：验证 TypeScript 构建

```bash
cd /home/rui/projects/pm_agent/frontend/strategy_dashboard
npm run typecheck
```

如果有 TypeScript 错误，逐条修复。常见原因：
- `JSX.Element` 在 React 18 里已弃用，改用 `React.ReactElement` 或 `ReactNode`
- `children: JSX.Element` 改为 `children: React.ReactNode`
- `import React from 'react'` 在某些文件缺失

修复原则：**最小改动**，只改报错的那一行，不重构逻辑。

---

## 任务 2：新建 `.env.local`

新建文件 `frontend/strategy_dashboard/.env.local`：

```
VITE_WEATHER_API=http://localhost:8000
```

（该文件已在 `.gitignore` 里，不会提交）

---

## 任务 3：新建 `vite.config.ts` proxy（如果不存在 proxy 配置）

读取现有 `vite.config.ts`，如果没有 server.proxy，则**替换**为：

```ts
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react-swc'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
      '/health': 'http://localhost:8000',
    },
  },
})
```

如果已有 proxy 配置就不改。

---

## 任务 4：验证 `npm run build` 通过

```bash
cd /home/rui/projects/pm_agent/frontend/strategy_dashboard
npm run build
```

必须 0 错误。如有错误，修复直到构建成功。

---

## 验收标准

```bash
cd /home/rui/projects/pm_agent/frontend/strategy_dashboard
npm run typecheck  # 0 errors
npm run build      # 成功，dist/ 生成文件
```

截图或粘贴最终的 build 输出（包含 dist/ 文件列表）。

---

## 完成后

结果写入 `tasks/task_result.md`（格式见 PROTOCOL.md）：
- 修复了哪些 TypeScript 错误（列举）
- build 输出摘要
- commit hash
