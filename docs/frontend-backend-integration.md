# 前端 web/ ↔ Python 后端对接方案

> 状态：**M1 已接通**；**M1.5 LLM 真流式 + Skill 事件已实施**（`answer_delta` / `answer_done` / `skill_started` / `skill_completed`）。推荐默认：1B `/api` + 2A Vite 反代 + 5A 单 Agent 服务层合成 + 6A `webapi/` :8000。
> 启动：项目根 `python -m uvicorn webapi.app:app --host 127.0.0.1 --port 8000`；再在 `web/` 执行 `npm run dev`。`VITE_USE_MOCK=true` 可回退 mock。
> 短期记忆默认走本机 Redis（非云端，`127.0.0.1:6379`）。不同会话按 `session:{session_id}` 分 key，TTL 7 天；连不上则降级内存，重启后同一 `session_id` 会丢最近几轮。工作台打开会话时走 `GET /api/sessions/{id}/messages` 回填聊天框。
> 原则：薄服务层；不改 Swarm 路由算法 / CLI；前端从 mock 切到真实 SSE。

---

## 1. 目标与原则

| 原则 | 含义 |
|------|------|
| 薄服务层 | 新建 `webapi/`（或 `api/`），只做 HTTP + SSE 适配；`await coordinator.process(...)` |
| 不改核心逻辑 | 不把 FastAPI 塞进 `main.py` 交互循环；不重写路由/协作/记忆 |
| 前端切流 | `web/src/mock/simulate.ts` 换成 `api/client.ts` 的 `fetch` + SSE 解析 |
| 演示优先 | **M1 只接通对话工作台**；仪表盘 / 记忆 / 知识库 / 安全页继续 mock |

---

## 2. 推荐架构：FastAPI + SSE

```
浏览器 React (Vite :5173)
    │  POST /chat  { message, session_id }
    │  ← SSE: event + data 帧
    ▼
webapi/  FastAPI（新建，:8000）
    │  CORS / session 校验 / 错误帧
    │  EventBus：订阅 SharedContext.events → SSE
    │  Coordinator 单例（lifespan）
    ▼
现有核心（复用）
    SwarmCoordinator.process
    LeadAgent / Workers / AgentLoop / Skills
    SharedContext + events.py
    AutoFixer / 记忆 / Milvus
```

**为什么不先上 WebSocket**

- 本场景是服务端→浏览器**单向推送**（过程事件 + 答案）。
- SSE 走普通 HTTP、代理友好、实现量小，与 `client.ts` / `IMPROVEMENT_PLAN` P1-1 / 设计稿「架构与数据流」一致。
- WebSocket 留到需要「中途打断 / 改写 Agent」时再升级。

**依赖现状**：`requirements.txt` **没有** `fastapi` / `uvicorn` / `sse-starlette`。实施时只加这三项（及 CORS 中间件，FastAPI 自带）。

---

## 3. 现状对齐：前端已假设 vs 后端实际

### 3.1 前端已预留（`web/src/api/client.ts`）

```
POST {API_BASE}/chat
Content-Type: application/json
Accept: text/event-stream
Body: { message: string, session_id: string }
```

约定事件名：`swarm_started | task_decomposed | subtask_started | subtask_completed | swarm_completed | timeout_occurred`  
前端扩展：`answer_delta | answer_done`

工作台消费方式：`Workbench.tsx` → `simulateConsultation()`（setTimeout 假流），**从未 fetch**。  
`SESSION_ID` 在 mock 里写死，工作台**未使用**。  
Vite **无 proxy**；`VITE_API_BASE` 已在 `vite-env.d.ts` 声明。

### 3.2 后端实际能提供

| 能力 | 现状 | 缺口 |
|------|------|------|
| 入口 | `process_with_swarm()` / `SwarmCoordinator.process` | 每次调用**新建** Coordinator（记忆无法跨 HTTP 请求） |
| session | 未传则生成 `{YYYYMMDD-HHMMSS}-{uuid8}`；CLI 整场共用一个 | HTTP 需由前端传入并复用 |
| 事件 | `events.py` 8 种；发布到 `SharedContext.events` **list，无订阅者** | 无法实时推 SSE |
| 单 Agent 路径 | **不创建** SharedContext，**零事件** | 工作台时间线无数据源 |
| 超时 | Swarm 90s → 返回字段 `timeout_occurred` | **不是** EventType |
| 答案 | `answer` + `suggestions` + `disclaimer` | 无结构化 `alert` / `sources` |
| 流式 LLM | `LLMClient.chat` 整段返回 | 无 `answer_delta` |
| Skill | `AgentLoop` 写入 `intermediate_results` | **不发事件**，时间线 Skill 标签无法亮起 |
| AutoFixer | 把免责/就医警告**拼进字符串** | 无独立修复记录给安全页 / 答案卡陶土条 |
| 依赖 | openai / mem0 / milvus / loguru | **无 FastAPI** |

### 3.3 `process()` 返回字段

**单 Agent**（`swarm_enabled=False`）：`answer`, `iterations`, `agent_id`, `suggestions`, `disclaimer`, `session_id`, `route_reason`

**Swarm**（`swarm_enabled=True`）：`answer`, `session_id`, `agents_involved`, `subtasks_completed`, `total_time`, `swarm_metadata`, `timeout_occurred`, `suggestions`, `disclaimer`

前端 `AnswerPayload` 还要：`body`, `alert?`, `alertNote?`, `sources[]`, `elapsed`, `agentCount`, `timedOut?`

---

## 4. API 契约草案

统一 SSE 帧（W3C）：

```
event: <EventName>
data: <JSON>

```

`data` 公共字段：`ts`（ISO8601）、`session_id`、其余见下表。

### 4.1 M1 必做：`POST /chat`（SSE）

**请求**

```json
{
  "message": "用户问题",
  "session_id": "20260818-121100-a1b2c3d4"
}
```

| 字段 | 规则 |
|------|------|
| `message` | 必填，非空 |
| `session_id` | 建议必填；空则服务端生成并在首帧带回 |
| 可选后续 | `context`（年龄/既往史）— M1 可不做 |

**响应**：`Content-Type: text/event-stream`，`Cache-Control: no-cache`。

**SSE event 类型（对齐 `events.py` + 前端 simulate）**

| event | data 关键字段 | 来源 | M1 |
|-------|---------------|------|----|
| `session` | `session_id` | 服务层首帧 | 必做 |
| `routing` | `mode`: `swarm` \| `single`；`subtask_count`；`reason?` | Coordinator 分解结果 | 必做（前端扩展，events.py 无） |
| `swarm_started` | `question`, `num_subtasks` | `EventType.SWARM_STARTED` | 必做；**单 Agent 由服务层合成** |
| `task_decomposed` | `subtask_id`, `type`, `assigned_agent`, `description?` | `SharedContext.add_subtask`（每个子任务一帧） | 必做 |
| `subtask_started` | `subtask_id`, `assigned_agent` | `start_subtask` | 必做 |
| `subtask_completed` | `subtask_id`, `assigned_agent`, `duration_s?`, `result_summary?` | `complete_subtask` | 必做 |
| `timeout_occurred` | `completed_agents[]`, `pending_agents[]` | 返回值，**非 EventType** | 必做（超时才发） |
| `answer_done` | 见下方 payload | `process()` 返回值映射 | 必做 |
| `swarm_completed` | `duration`, `agents_count`, `timeout_occurred` | `SWARM_COMPLETED` 或服务层合成 | 必做 |
| `error` | `code`, `message` | 服务层 | 必做 |
| `answer_delta` | `text`（累计或增量，见决策点） | LLM stream | **M1 可选** |
| `skill_called` | `agent_id`, `skill_name`, `subtask_id?` | AgentLoop | **需微改，M1 可选** |
| `context_updated` | `key` | events.py 已有 | 不做（UI 不用） |
| `agent_question` / `agent_answer` | — | 枚举有、**从未发布** | 不做 |

**`answer_done` data（对齐工作台答案卡）**

```json
{
  "body": "……",
  "suggestions": ["……"],
  "disclaimer": "……",
  "elapsed": "24.8s",
  "agent_count": 3,
  "timed_out": false,
  "alert": null,
  "alert_note": null,
  "sources": [],
  "swarm_enabled": true,
  "session_id": "…"
}
```

M1 允许：`alert` / `sources` 为空；若答案正文含「重要提醒」「就医」，前端可先从 `body` 启发式拆条（或暂不拆）。结构化拆分标为 M1.5。

**错误帧**

```
event: error
data: {"code":"timeout"|"llm_error"|"bad_request"|"internal","message":"……"}
```

HTTP 层：非法 JSON → 400（非 SSE）；处理中异常 → SSE `error` 后结束流（避免半开连接）。

### 4.2 CORS / 前缀 / session

| 项 | 建议（待确认） |
|----|----------------|
| CORS | 允许 `http://127.0.0.1:5173`、`http://localhost:5173` |
| 路径前缀 | 决策点：裸 `/chat` 或 `/api/chat` |
| session | 前端首次生成 UUID 或沿用后端格式；后续请求原样回传 |

### 4.3 后续接口（非 M1）

| 方法 | 路径 | 用途 | 阶段 |
|------|------|------|------|
| GET | `/sessions` | 会话列表（SessionSummary 文件） | M2 记忆页 |
| GET | `/sessions/{id}` | 单会话 + 事件回放 | M2 |
| GET | `/sessions/{id}/similar` | Mem0 `search_similar_sessions` | M2 |
| GET | `/stats` | 今日/本周会话、Swarm 占比、耗时 | M3 仪表盘 |
| GET | `/kb/search?q=&type=` | Milvus `search` | M4 知识库 |
| GET | `/safety/fixes` | AutoFixer 日志（需先有记录） | M4 安全页 |

---

## 5. 事件 → UI 映射

| 后端事件 / 字段 | 工作台 UI | 能否直接用 |
|-----------------|-----------|------------|
| `routing.mode=swarm\|single` | 对话流「智能路由」提示条 | 需服务层根据 `len(subtasks)` 发 |
| `task_decomposed` | 时间线「任务分解」节点灰→绿 | 可；每任务一帧，前端聚合 count |
| `subtask_started/completed` + `assigned_agent` | 咨询/诊断/研究节点状态 + 耗时 | 可；需 agent_id → 中文标题映射 |
| `swarm_completed.duration` | 侧栏「已完成」、总耗时 | 可 |
| `answer_done.body` | 答案卡正文 | 可；无流式则整段出现 |
| `answer_done.suggestions/disclaimer` | 建议列表、脚注 | 可（正则抽取，可能空） |
| `timeout_occurred` + `timed_out` | `TimeoutFallback`、节点 `timeout` 态 | 可；工作台目前 **从不设 timedOut** |
| `answer_done.alert` | 陶土色警示条 | **缺口**：AutoFixer 只改字符串 |
| `answer_done.sources` | 来源标签 | **缺口**：检索结果留在 skill 内部 |
| Skill 标签逐个亮起 | 时间线 `skills[].active` | **缺口**：无事件 |
| `answer_delta` | 逐字打出 | **缺口**：LLM 非流式 |
| Mem0 相似案例 | 记忆页 | M2；`historical_cases` 仅进 prompt |
| Milvus hits | 知识库页 | M4；现有 `id/content/metadata/score` |
| AutoFixer 次数 | 仪表盘 / 安全页 | M4；无持久化日志 |

### 需后端微改（不动核心算法，行数级）

| 改动 | 约行数 | 说明 | M1 是否必须 |
|------|--------|------|-------------|
| `SharedContext.publish_event` 增加可选 callback 列表 | ~15 | EventBus 订阅；原 list 仍保留 | **必须**（否则 SSE 只能等 process 结束再回放，失去过程动画） |
| 单 Agent 路径：服务层合成 `swarm_started` / `task_decomposed` / `subtask_*` / `swarm_completed` | ~40（仅 webapi） | **不改** Coordinator 算法 | 必须（否则简单问题时间线空白） |
| `timeout_occurred` 转 SSE 帧 | ~10（webapi） | 读返回值即可 | 必须 |
| AgentLoop 在执行 skill 后 `publish` `skill_called` | ~15 | 只多发事件，不改 tool 事务 | 可选（无则 Skill 标签保持灰） |
| `LLMClient` `stream=True` 生成器 | ~40–80 | 仅最终答案那次调用；function calling 仍非流 | 可选（无则 `answer_done` 一次出全文，前端仍可假打字） |
| AutoFixer 返回 `{text, fixes[]}` | ~20 | 不改检测规则 | M1.5 / M4 |
| 答案附带 RAG sources | ~20 | 从 skill 结果或 context 抽出 | M1.5 |

**明确不做**：改 Lead 路由、Swarm 调度、记忆写入协议、约束规则、新建第二套 FastAPI 业务。

---

## 6. 前端改动范围（M1）

| 文件 | 改动 |
|------|------|
| `web/src/api/client.ts` | 实现 `sendChat()`：fetch + 解析 `event:`/`data:`，回调对齐 `SimulateHandlers` |
| `web/src/pages/Workbench.tsx` | `simulateConsultation` → `sendChat`；持有 `session_id`；`timeout_occurred` 设 `timedOut` |
| `web/src/mock/*` | **保留**；用 `VITE_USE_MOCK=true` 或无后端时回退，便于离线演示 |
| `web/vite.config.ts` | 开发 proxy：`/api` → `http://127.0.0.1:8000`（若选反代） |
| `.env.development` | `VITE_API_BASE=/api` 或 `http://127.0.0.1:8000` |
| 仪表盘/记忆/知识库/安全 | **M1 不动**，继续 mock |

`types.ts` 的 `StreamEvent.name` 建议改为与契约 event 名一致（去掉 `×3` 这种展示拼接，展示层格式化）。

---

## 7. 后端改动范围（M1）

新建包，例如：

```
webapi/
  __init__.py
  app.py          # FastAPI + CORS + lifespan
  routes_chat.py  # POST /chat SSE
  event_bridge.py # 订阅 SharedContext → asyncio.Queue → SSE
  mappers.py      # process() 返回值 → answer_done JSON
```

| 要点 | 做法 |
|------|------|
| 入口 | `uvicorn webapi.app:app --host 127.0.0.1 --port 8000`；**不改** `main.py` CLI |
| 单例 | lifespan 创建 **一个** `SwarmCoordinator`，跨请求复用短期记忆（默认 Redis；连不上则内存） |
| 桥接 | 请求内给本次 `SharedContext` 注册 callback；单 Agent 无 context 则服务层按路由结果合成事件 |
| 阻塞 | `process()` 在 async 里跑；Mem0 已 `to_thread`。Skill 内同步 embedding 仍可能卡住 SSE（见风险） |
| 依赖 | 增加 `fastapi`、`uvicorn[standard]`、`sse-starlette` |

---

## 8. 分阶段与验收

| 阶段 | 范围 | 验收 |
|------|------|------|
| **M1** | `POST /chat` SSE + 工作台真实对话 | curl 能看到 `routing` → `task_decomposed` → `subtask_*` → `answer_done`；浏览器一次 Swarm 咨询时间线动起来；追问同一 `session_id` 有上下文；超时显示兜底 |
| **M1.5** | LLM 真流式 / Skill 事件 / alert·sources 结构化 | 答案逐 token；Skill 标签亮起 |
| **M2** | `/sessions` + 记忆页去 mock | 列表与 SessionSummary 一致；可点开回看 |
| **M3** | `/stats` + 仪表盘 | KPI 与本地会话文件/日志一致 |
| **M4** | `/kb/search` + `/safety/fixes` | 知识库走 Milvus；安全页有真实修复记录 |

M1 **不要求** 仪表盘/记忆/知识库/安全接真接口。

---

## 9. 风险

| 风险 | 说明 | 缓解 |
|------|------|------|
| 事件循环阻塞（Bug 7 残留） | Mem0 已 `to_thread`；部分 **async skill 内同步** embedding/Milvus 仍会卡住 SSE 心跳 | M1 可先接受卡顿；随后 skill 内 `asyncio.to_thread` |
| 90s 超时 | 仅 Swarm `wait_for`；单 Agent 无同一超时 | SSE 层可加总超时；UI 已有 TimeoutFallback |
| 过程动画 vs 一次性回放 | 若不对 `publish_event` 加回调，只能等全部结束再吐历史事件 | M1 必做 ~15 行订阅 |
| Windows | Milvus Lite 已支持；SSE 需关代理缓冲（uvicorn 标准即可） | 开发绑 `127.0.0.1` |
| 密钥 / 模型 | `LLMClient` / Mem0 读**仓库父目录** `config.py`（`d:\medical_model_training\config.py`）及环境变量 | webapi 与 CLI 同一进程配置，无需另写一套 |
| `process_with_swarm` 每次新建 Coordinator | HTTP 多轮记忆丢失 | **禁止**在 webapi 里调该便捷函数，只用单例 |
| 答案结构松散 | `suggestions` 靠「【核心建议】」正则 | UI 允许列表为空 |
| CORS / 混合端口 | 5173 ↔ 8000 | 反代或 CORS 二选一（决策点） |

---

## 10. 请确认的决策点

1. **路径前缀**：A) `/chat`（与 `client.ts` 字面一致）　B) `/api/chat`（便于 Vite 反代）
2. **开发联调**：A) Vite proxy `/api` → `:8000`，前端 `VITE_API_BASE=""` 或 `"/api"`　B) 前端直连 `VITE_API_BASE=http://127.0.0.1:8000`
3. **M1 是否做 LLM 真流式**：A) 不做，整段 `answer_done`（可前端假打字）　B) 本阶段就改 `LLMClient.stream`
4. **M1 是否发 Skill 事件**：A) 不做，时间线 Skill 保持灰　B) AgentLoop ~15 行补 `skill_called`
5. **单 Agent 时间线**：A) 仅服务层合成事件（零改 Coordinator）　B) Coordinator 单 Agent 也建 SharedContext（改动略大）
6. **包名与端口**：A) `webapi/` + `:8000`　B) `api/` + 其他端口

**推荐默认**（可改）：1B + 2A + 3A + 4A + 5A + 6A。最快接通工作台，核心算法零改；流式与 Skill 灯放到 M1.5。
