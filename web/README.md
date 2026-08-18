# MediX 智愈 · 可视化界面

对齐设计方案 `docs/ui-design-proposal.html` 的 React 前端（Vite + TypeScript）。默认连接 `webapi` 真实 SSE；`VITE_USE_MOCK=true` 可回退 mock。

## 启动（先后端，再前端）

在**项目根** `medix-agent-swarm/` 启动 FastAPI（端口 **8000**）：

```bash
python -m uvicorn webapi.app:app --host 127.0.0.1 --port 8000
```

再在 `web/` 目录：

```bash
npm install
npm run dev
```

开发服务器默认端口 **5173**。Vite 把 `/api` 反代到 `http://127.0.0.1:8000`，浏览器打开 `http://localhost:5173`。

短期记忆默认走本机 Redis（非云端），按 `session:{session_id}` 分 key（TTL 7 天）。请先启动 Redis，例如 `docker run -d -p 6379:6379 redis`；连不上时后端降级内存，重启 uvicorn 后同一 `session_id` 读不到上一轮。打开工作台或从会话记忆点进某个 `session_id` 时，会请求 `GET /api/sessions/{id}/messages` 把短期记忆填进聊天框。

`VITE_API_BASE` 默认空字符串，请求走相对路径 `/api/chat`。

预览生产构建：

```bash
npm run build
npm run preview
```

预览端口 **4173**。预览不带开发代理：请让后端开着，或把 `VITE_API_BASE` 设为 `http://127.0.0.1:8000` 后重新 build。

## Mock 开关

默认走真实后端。离线演示：

```bash
# PowerShell
$env:VITE_USE_MOCK="true"; npm run dev
```

或在 `web/.env.development` 写入 `VITE_USE_MOCK=true`。

后端未启动时，工作台会显示中文连接失败提示，不会白屏。

## 页面

| 路径 | 页面 |
|------|------|
| `/` | 对话工作台（真实 `POST /api/chat` SSE；可用 mock 回退） |
| `/dashboard` | 仪表盘（mock） |
| `/memory` | 会话记忆（mock） |
| `/knowledge` | 知识库（mock） |
| `/safety` | 安全质量（mock） |

本目录不要提交 `node_modules/` 与 `dist/`。
