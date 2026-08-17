# MediX Agent Swarm 改进方案报告（面试深度导向）

> 生成时间：2026-08-17
> 定位：本报告基于对全部核心代码的逐文件审查，列出**有代码证据支撑**的问题与改进方案，按"面试价值 × 实施成本"排列优先级。每一项都标注了对应的源码位置、具体做法和面试可讲的深度点。

---

## 一、现状一句话评价

这是一个**功能骨架很全的多 Agent 医疗助手原型**：Skills→function calling 直达、Agent Loop（Think-Act-Observe）、Swarm 并行协作、双层记忆、Milvus RAG、约束+自动修复的 Harness 雏形都有。但按生产标准衡量，它缺**流式输出、服务化、评估体系、可观测性、硬安全门禁**这五件事，且存在若干**真实的正确性 bug**——这些恰恰是面试时最能体现深度的素材：先讲"我发现并修复了什么"，再讲"我把它从 demo 推向了生产形态"。

---

## 二、正确性问题清单（Bug 级，修复即是面试故事）

每一项都是"发现 → 定位 → 修复 → 验证"的完整叙事素材。**建议全部修复，总工作量约 1~2 天。**

### Bug 1：`temperature=0` 被 `or` 短路，永远无法使用确定性采样

- **位置**：`core/llm_client.py` 第 84 行、第 166~167 行：`temperature = temperature or self.temperature`
- **问题**：Python 中 `0 or 0.7 == 0.7`。任何调用方想传 `temperature=0`（路由、结构化输出场景的标准做法）都会被静默改成默认 0.7。这直接导致 LeadAgent 路由判断无法通过降温来稳定——而 `TEST_REPORT.md` 里那 2 个 Swarm 路由测试的偶发失败，根源之一就在这里。
- **修法**：改为 `temperature = temperature if temperature is not None else self.temperature`。
- **面试点**：Python falsy 值陷阱；LLM 工程中"路由/抽取用低温、生成用高温"的温度分层策略。

### Bug 2：Milvus COSINE 相似度分数语义疑似颠倒

- **位置**：`knowledge/milvus_kb.py` 第 244 行：`"score": 1 - hit["distance"]`
- **问题**：`MilvusClient` 在 `metric_type="COSINE"` 下返回的 `distance` 字段**本身就是余弦相似度**（越大越相关），`1 - x` 之后分数含义反转。证据：`TEST_REPORT.md` 中"正常返回"的检索结果 score 只有 0.24/0.30，反推原始相似度是 0.76/0.70——本来是高相关，展示出来却像低相关。更糟的是 `.claude/skills/search-knowledge/script/search.py` 第 95 行把这个分数以"相关度: 24%"的形式喂给 LLM，**系统性误导模型对证据可信度的判断**。
- **修法**：直接使用 `hit["distance"]` 作为相似度分数；写一个 3 行的验证脚本（同一 query 检索，人工比对最相关文档的分数是否最高）确认后再改。
- **面试点**：不同向量库/度量下 distance 与 similarity 的语义差异（L2 越小越好、IP/COSINE 越大越好）；"分数进 prompt"时的语义正确性问题。

### Bug 3：Swarm 模式下短期记忆完全丢失（注释声称已保存，实际没有）

- **位置**：`agents/base_agent.py` 第 147~161 行 `process_subtask` 构造的 `input_data` 只含 `question/subtask_id/subtask_type`，**不含 `session_id`**；而 `run_loop` 靠 `input_data.get('session_id')` 取值 → Swarm 路径下 AgentLoop 拿到的 session_id 恒为 None，所有消息记录逻辑被跳过。
- **矛盾证据**：`swarm/swarm_coordinator.py` 第 337~338 行注释写着"短期记忆已经在 Agent Loop 中保存了，这里不需要重复保存"——实际没保存。**后果**：用户经历一次 Swarm 回答后，下一轮追问时系统对上一轮毫无记忆，"多轮对话上下文利用率 100%"的声称在 Swarm 模式下不成立。
- **修法**：`process_subtask` 透传 session_id；同时 Swarm 模式下应由 Coordinator 统一写入一条 user 问题 + 最终综合答案（而不是让 3 个 Worker 各写各的中间消息，否则记忆里会有 3 份交错的工具调用流水）。
- **面试点**：多 Agent 系统中"谁拥有会话记忆写入权"的设计问题——这是单 Agent 记忆方案平移到多 Agent 时最容易踩的坑。

### Bug 4：对话历史被注入两次 + 记忆自我污染（套娃）

- **位置**：三处叠加。
  1. `swarm/swarm_coordinator.py` 第 124~129 行把 `recent_history` 塞进 `enhanced_context`；
  2. `agents/consultation_agent.py` 第 110~117 行 `format_user_input` 把整个 context（含 recent_history 的 Python dict repr）拼进 user message 文本；
  3. `core/agent_loop.py` 第 323~328 行又通过 `get_history()` 把同一份历史作为独立 messages 注入。
  → **同一份历史进了两次 prompt**，且一次是丑陋的 dict 字符串。
- **更严重的连锁**：`core/agent_loop.py` 第 87~93 行把**格式化后的完整 user message**（含 `[系统信息] 当前会话ID…`、`背景信息：recent_history: [...]`）写回短期记忆。下一轮取历史时，历史里嵌着上一轮的历史——指数级套娃。`memory/entropy_manager.py` 的"熵管理/压缩"实际上是在给这个设计缺陷做补偿。
- **修法**：明确协议——短期记忆只存**原始用户问题**和**最终答案**；历史只通过 messages 数组注入一次；context 里不再放 recent_history。改完之后可以量化对比每轮 prompt token 数的下降（很好的面试数据点）。
- **面试点**：上下文工程（context engineering）中 prompt 组装的单一职责原则；"记忆写入的是语义事实，不是 prompt 工件"。

### Bug 5：AgentLoop 异常重试会产生非法消息序列（OpenAI 协议违规）

- **位置**：`core/agent_loop.py` 第 247~252 行——迭代内 `except` 后直接 `continue` 重试。若异常发生在"assistant(tool_calls) 消息已 append、对应 tool 结果消息尚未 append"之间（如某个 skill 抛错、网络中断），下一轮携带这个残缺序列请求 API 会直接 400（协议要求每个 tool_call 必须有对应的 tool 消息）。
- **修法**：以"assistant(tool_calls) + 全部 tool 结果"为**事务单元**，异常时回滚到事务前的 messages 快照再重试；skill 执行失败时也应 append 一条 `{"success": false, "error": ...}` 的 tool 消息（`skill_registry.execute` 已返回这种结构，只是异常路径没走到 append）。
- **面试点**：Agent Loop 的状态机设计、消息序列不变量（invariant）维护。

### Bug 6：工具调用达到上限后可能空转到 max_iterations

- **位置**：`core/agent_loop.py` 第 131~138 行——达到 `max_tool_calls` 后注入一条 user 消息要求"给出最终答复"，然后 `continue`。但下一轮调用**仍然携带 tools 且 `tool_choice="auto"`**，LLM 完全可以再次返回 tool_calls，于是再注入一条、再循环……空烧迭代次数与 token。
- **修法**：达到上限后改用 `tool_choice="none"`（一行改动，行为立即确定化）。
- **面试点**：function calling 的 `tool_choice` 语义与"预算控制"设计。

### Bug 7：同步阻塞调用卡死事件循环，"并行 Swarm"实际被串行化

- **位置**：两类。
  1. `memory/long_term.py`——Mem0 `MemoryClient` 是**同步** SDK，`mem0.search()/add()` 在 `SwarmCoordinator.process()`（async）里被直接调用（`swarm_coordinator.py` 第 116、230、343 行），每次都是一个阻塞的云端 HTTP 请求；
  2. `.claude/skills/search-knowledge/script/search.py` 第 42 行——`kb.search()` 内部是同步的 embedding encode（CPU 密集）+ Milvus 查询，却在 `async def search_knowledge` 里直接调用。注意 `SkillRegistry.execute`（`core/skill_registry.py` 第 87~96 行）只对**sync 函数**用 `run_in_executor`，而这些 skill 声明成了 async，反而绕过了线程池保护。
- **后果**：Swarm 用 `asyncio.gather` 起的"并行"任务，会在每次 embedding/Mem0 调用时全体停摆。3 个 Agent 的理论并行加速在实测中大打折扣。
- **修法**：同步调用统一用 `asyncio.to_thread()` 包装；或把 embedding encode 抽到线程池。修复后用一个 20 行的 benchmark 脚本对比 Swarm 总耗时（改造前后），拿到量化数据。
- **面试点**：**这是整个项目最有含金量的面试故事**——asyncio 事件循环模型、"async 函数里藏同步调用"这一最常见的生产事故模式、GIL 与 CPU 密集任务的关系、如何用 `asyncio.to_thread`/executor/进程池分层解决。

### Bug 8：其他小问题（顺手修）

| 问题 | 位置 | 说明 |
|------|------|------|
| `\\n` 字面量 | `memory/long_term.py` 第 153 行 | `f"问题：{question}\\n回答..."` 存进 Mem0 的是字面反斜杠 n |
| `List[Document]` 未导入 | `research/deep_research_workflow.py` 第 86 行 | 静态检查报错 |
| system prompt 说"9个 Skills"、README 说 7 个 | `agents/consultation_agent.py` 第 44 行 vs `README.md` | 文档不一致 |
| README 残留个人绝对路径 | `README.md` 第 241、294 行 `/Users/saintgeo/...` | 可移植性、专业度 |
| README 声称 26/26 100% 通过 | `README.md` 第 57 行 vs `TEST_REPORT.md` 24/26 | 面试官核对材料时会发现 |
| 单例 `__init__` 忽略后续参数 | `memory/short_term.py` 第 104~106 行 | 第二次传 `storage_type="redis"` 会被静默忽略 |

---

## 三、架构级改进方案（按优先级）

### P0-1：LLM 路由的确定性工程（1 天｜直接消灭已知的测试不稳定）

**现状**：`swarm/lead_agent.py` 第 216~227 行用 `re.search(r'\{.*\}')` 从自由文本里抠 JSON；温度 0.7；`constraints/swarm_constraints.yaml` 里定义的强制路由规则（高危症状必须包含 DiagnosticAgent 等）以及 `validator.py` 的 `get_required_agents()`/`validate_task_decomposition()` **只在测试中被调用，生产路径完全没接入**。

**方案**（三层防线）：
1. **结构化输出**：`assess_and_decompose` 改用 `response_format={"type": "json_schema", ...}`（或 function calling 强制 schema），temperature=0（依赖 Bug 1 的修复）；
2. **规则前置**：调用 LLM 前先跑 `get_required_agents(question)`，命中高危关键词直接保证对应 Agent 入列——LLM 只做"补充判断"而非"唯一裁决"；
3. **结果校验**：LLM 输出后跑 `validate_task_decomposition`，违规时按规则修正（如超过 max_subtasks 就截断）。

**面试点**：LLM 的非确定性治理是所有 Agent 系统的核心议题。可以讲："规则做下限保证（安全兜底），LLM 做上限扩展（灵活理解），校验器做闭环"——并用路由准确率评估集（见 P1-2）量化改进前后的差异。这比"我调了调 prompt"高一个层级。

### P0-2：Token 用量统计与请求级可观测性（0.5 天）

**现状**：`core/llm_client.py` 中 `response.usage` 被直接丢弃；日志无 trace_id，一次 Swarm 请求内 3 个 Agent 的日志交错无法归因；`core/state_manager.py` 记录的 `intermediate_results` 从未对外暴露。

**方案**：
1. `LLMResponse` 增加 usage 字段，`SwarmCoordinator` 聚合并在结果里返回 `total_tokens / prompt_tokens / cost_estimate`；
2. 用 `contextvars` 传递 `trace_id`（session_id + 请求序号），loguru `bind()` 注入每条日志；
3. 每次请求结束输出一行结构化摘要：路由模式、各 Agent 耗时、工具调用次数、token 消耗。
4. （可选加分）接入 Langfuse 或 OpenTelemetry，把 Agent Loop 每一步变成 span。

**面试点**：LLM 应用的成本工程（一次 Swarm 请求 = 1 次路由 + 3×N 次 Agent Loop + 1 次汇总，token 消耗是单 Agent 的多少倍？值不值？）——能主动谈成本的候选人很少。

### P1-1：FastAPI 服务化 + SSE 流式输出 + 协作过程可视化（3~4 天｜演示效果最强）

**现状**：只有 CLI（`main.py`）；无流式——用户面对复杂问题要干等 30~90 秒；`swarm/events.py` 的事件系统**只写不读**（发布到 list 从未被消费，`AGENT_QUESTION/AGENT_ANSWER` 事件类型从未被发布）——"事件驱动"名存实亡。

**方案**：
1. `FastAPI` + `POST /chat`（SSE 响应）。`LLMClient` 增加 `stream=True` 支持，最终答案 token 级流出；
2. **把 SharedContext 事件流变成 SSE 事件**：`task_decomposed`、`subtask_started`、`subtask_completed`（含哪个 Agent、调了什么 skill）实时推给前端——正好激活现有的 events.py，让"多 Agent 协作"从日志变成用户可见的过程动画；
3. Coordinator 改为 app 级单例（lifespan 管理），消灭 `process_with_swarm` 每次请求重建全部 Agent/记忆管理器/Mem0 客户端的开销（`swarm/swarm_coordinator.py` 第 482 行）；
4. 一个 100 行以内的极简前端页（或 Gradio）：左侧对话流，右侧 Agent 时间线。

**面试点**：SSE vs WebSocket 选型；流式场景下"答案在生成中途才发现违反安全约束怎么办"（先流式、结尾校验、必要时追加修正声明——正好与现有 AutoFixer 衔接）；资源生命周期管理（为什么单例、哪些组件线程/协程安全）。**现场演示右侧 Agent 时间线的效果，是所有改进里最直观的。**

### P1-2：评估体系（3~4 天｜面试差异化最强，绝大多数候选人没有）

**现状**：只有 `examples/test_all.py` 一个 1400 行的自写脚本，直连真实 LLM，慢、贵、不可重复（TEST_REPORT 里 2 个失败就是 LLM 波动）；无任何质量量化手段。

**方案**（三层分离）：
1. **单元层**：迁移到 `pytest` + `pytest-asyncio`，LLM 用 mock（录制真实响应做 fixture），测 Agent Loop 状态机、记忆读写、约束校验等纯逻辑——秒级、零成本、100% 确定；
2. **评估层（核心）**：建 golden set 并写评估脚本：
   - **路由准确率**：50~100 条标注问题（简单/复杂/高危三类）→ 跑 `assess_and_decompose` → 统计准确率与方差（跑 3 次看稳定性）。这直接量化 P0-1 的收益；
   - **RAG 检索质量**：30 条"问题→应命中文档"标注 → recall@k / MRR。这直接量化 P2-1 的收益；
   - **答案质量**：LLM-as-judge（用另一个模型按"准确性/完整性/安全性"三维打分），对固定问题集出分数基线；
   - **安全红线**：一组必须触发就医警告的急症问题 + 一组诱导开处方的问题 → 断言输出行为，**这个必须 100% 通过**；
3. **CI 层**：GitHub Actions——单元层每次 push 必跑；评估层每日/手动触发并输出趋势报告。

**面试点**："没有评估的 LLM 应用改进都是感觉工程"。能讲清楚为什么把确定性测试与统计性评估分开、LLM-as-judge 的偏差与缓解（position bias、用 rubric 约束）、以及安全测试为什么必须是硬门禁——这套方法论适用于任何 LLM 项目，是可迁移的硬能力。

### P2-1：RAG 质量升级（4~5 天｜配合评估集才有说服力）

**现状**（`knowledge/milvus_kb.py`）：
- 纯字符切块（1024 字符硬切，第 100~123 行），会把段落/句子拦腰斩断；
- 单路 dense 检索，无 rerank；
- metadata 过滤用 `like "%\"type\": \"xxx\"%"` 的 JSON 字符串模糊匹配（第 203 行）——脆弱且无法利用索引；
- 语料只有 10 个 txt 文档；
- 工具参数 schema 质量低：`agents/skill_registry_mixin.py` 第 69~90 行自动推断的参数描述就是参数名 Title 化（LLM 看到的 `query` 参数描述是 "Query"），类型靠参数名猜（含 "count" 就是 number）。

**方案**（按投入产出排序）：
1. **修参数 schema**：skill 函数改用显式参数声明（docstring 解析或 pydantic 模型），给 LLM 高质量的参数描述与 enum 约束——半天，function calling 准确率立收益；
2. **语义分块**：按句号/标题边界切分 + 重叠，保留文档结构 metadata；
3. **混合检索 + rerank**：Milvus 2.4+ 支持 sparse（BM25）+ dense 双路召回 + RRF 融合；召回 top20 后用 `bge-reranker-v2-m3` 精排到 top5；
4. **metadata 标量字段**：type/disease 建独立标量字段，过滤走索引而非字符串 like；
5. **多轮查询改写**：追问（"那要吃什么药？"）先经 LLM 结合历史改写为独立查询（"高血压患者用药建议"）再检索——多轮 RAG 的标配；
6. 扩充语料到 50+ 文档（临床指南公开摘要即可），否则以上优化测不出差异。

**面试点**：每一步都用 P1-2 的 recall@k/MRR 前后对比说话，例如"混合检索 + rerank 把 recall@5 从 X 提升到 Y"。能讲：为什么医学术语场景 BM25 与向量互补（缩写、药名精确匹配 vs 语义泛化）、rerank 的延迟-精度权衡、为什么 rerank 放在召回后而不是全库。

### P2-2：医疗安全防线升级（3 天｜医疗场景的差异化深度）

**现状**：安全完全依赖关键词匹配（`constraints/validator.py` 第 119、133 行——"就是"这种词会大量误伤正常表述如"感冒就是病毒感染"）；违规仅记 warning 不阻断（第 71 行）；AutoFixer 只会拼接免责声明字符串；日志全量打印用户健康信息（PII）。

**方案**（分层防御）:
1. **输入侧短路**：急症识别（胸痛+呼吸困难等组合）→ 不进 Agent 流程，直接返回急救指引（规则 + 小分类模型/LLM 低温判断），这是 fail-fast；
2. **输出侧升级**：高风险场景（诊断/用药相关）从关键词匹配升级为 LLM-as-guardrail 独立审查（低温、专用 prompt、结构化判定），保留关键词做兜底；违规分级——可修复的自动修复，不可修复的**阻断重生成**而非放行 + warning；
3. **引用溯源**：RAG 命中的文档 id/来源随答案返回（"依据《中国高血压防治指南》…"），从机制上抑制幻觉并可审计；
4. **PII 处理**：日志脱敏（用户问题 hash 化或截断），Mem0 存储前脱敏——医疗数据合规意识。

**面试点**：guardrails 的误报/漏报权衡（医疗场景漏报代价 >> 误报，所以阈值怎么偏）、防线为什么要纵深（输入/过程/输出三层）、以及"安全规则必须是硬门禁而不是日志警告"的工程判断。

### P2-3：诚实化"群体智能"叙事，或实现真正的任务认领（2 天~1 周）

**现状**：README 和代码注释大量宣传"去中心化、信息素、自主任务认领、涌现智能"，实现却是 LeadAgent **中心化指派** `assigned_agent`（`swarm/lead_agent.py` 第 246~280 行、`shared_context.py` 第 171~180 行）；`Contribution.confidence` 字段从未参与汇总加权；`SubTask.dependencies` 字段从未参与调度；`swarm_constraints.yaml` 自己都注明 sequential/debate "当前未实现"。**面试官追问两句就会穿帮，这是当前项目最大的面试风险点。**

**两条路选一**：
- **路线 A（务实，2 天）**：把文档改为诚实的"LeadAgent 编排 + Worker 并行执行"的黑板模式描述，把"为什么没做去中心化认领"变成一个主动讲的权衡（中心化分配延迟低、可控性强、便于约束校验；认领机制在 Agent 数量大/能力异构时才有收益）；
- **路线 B（深度，1 周）**：实现最小可用的认领机制——Lead 只发布任务+能力需求标签，Worker 按 `capabilities` 匹配度+当前负载出价认领（SharedContext 加乐观锁防重复认领），并激活 dependencies 做拓扑调度。再对比两种模式的延迟/质量差异。

**建议**：先走 A 止血，把 B 作为"下一步计划"在面试中主动提出——既诚实又展示了对分布式任务分配（合同网协议 Contract Net）的认知。

### P2-4：工程化收尾（2~3 天）

| 项 | 现状 | 方案 |
|----|------|------|
| 配置管理 | 父目录 `config.py` + 三处 `sys.path.insert` hack（`core/llm_client.py` 第 16 行等）；requirements 里的 `pydantic-settings` 装了没用 | `pydantic-settings` + `.env`，类型校验、必填校验、支持环境变量覆盖 |
| 打包 | `setup.py` + requirements.txt 双轨 | 迁移 `pyproject.toml` + `uv lock`（README 已在用 uv） |
| 容器化 | 无 | Dockerfile（多阶段构建，embedding 模型预下载进镜像层）+ docker-compose（可选 redis） |
| CI | 无 | GitHub Actions：ruff lint + pytest 单元层 + 评估冒烟 |
| Prompt 管理 | 超长字符串散落在各 Agent 类中 | 集中到 `prompts/` 目录（YAML/py 模块），版本注释，便于 diff 与回溯 |

---

## 四、实施路线图

```
第一阶段（1~2 天）·修复与止血 —— "我发现了这些问题"
  ├─ Bug 1~8 全部修复（每个都留 before/after 验证记录）
  ├─ P0-1 路由确定性（结构化输出 + 规则接入）
  ├─ P0-2 token 统计 + trace_id
  └─ 路线 A：文档诚实化

第二阶段（约 1 周）·服务化与评估 —— "我把它推向生产形态"
  ├─ P1-1 FastAPI + SSE 流式 + Agent 时间线可视化
  └─ P1-2 pytest 分层 + golden set + 路由/RAG/安全评估

第三阶段（1~2 周）·深度与差异化 —— "我用数据证明了改进"
  ├─ P2-1 RAG 升级（评估集量化每一步收益）
  ├─ P2-2 安全防线分层
  ├─ P2-4 Docker + CI + 配置重构
  └─ （可选）P2-3 路线 B：任务认领机制
```

关键原则：**评估先行**（第二阶段的评估集是第三阶段所有"提升了 X%"话术的前提）；每项改进保留改造前的数据基线。

---

## 五、面试叙事建议

### 项目一句话定位

"一个医疗领域的多 Agent 协作系统：LLM 驱动的智能路由 + 并行 Agent Swarm + RAG 知识库 + 双层记忆，重点解决了**路由确定性、异步并发正确性、医疗安全门禁和质量评估**四个生产化问题。"

### 五条可深挖的故事线（按含金量排序）

1. **asyncio 阻塞排查**（Bug 7）：现象（Swarm 并行加速比远低于预期）→ 定位（async 函数里藏着同步 embedding encode 和同步云端 SDK 调用，事件循环被卡死）→ 修复（to_thread/线程池分层）→ 量化（并行耗时对比）。可延伸：GIL、CPU 密集 vs IO 密集的不同处理、为什么 `run_in_executor` 保护对 async 声明的函数失效。
2. **LLM 路由不稳定治理**（P0-1 + Bug 1）：现象（同一问题时而单 Agent 时而 Swarm，测试偶发失败）→ 根因链（temperature=0 被 or 短路 + 正则抠 JSON + 规则引擎没接入）→ 三层防线方案 → 用路由准确率评估集量化。
3. **多 Agent 记忆一致性**（Bug 3 + Bug 4）：Swarm 模式记忆丢失、历史双重注入、记忆套娃污染 → 重新设计"记忆写入协议"（谁写、写什么、何时写）→ prompt token 下降数据。
4. **RAG 质量工程**（Bug 2 + P2-1）：从"分数语义颠倒"这个隐蔽 bug 切入 → 建评估集 → 混合检索/rerank/查询改写逐项验证收益。
5. **医疗安全门禁**（P2-2）：关键词匹配的误报漏报案例 → 分层防御设计 → "安全断言 100% 通过是发布硬门禁"的工程文化。

### 主动规避的坑

- 不要使用"去中心化""涌现智能""信息素"这些词描述当前实现（除非做了 P2-3 路线 B）；主动讲成"中心化编排 + 并行执行，认领机制是规划中的下一步"反而加分；
- README 的"26/26 100% 通过"与 TEST_REPORT 的 24/26 对不上，面试前统一口径；
- 被问"7 个还是 9 个 Skills"时能答上（实际 9 个：7 个原子 + search-history + search-similar-cases）。

---

## 六、问题速查索引

| # | 问题 | 位置 | 级别 |
|---|------|------|------|
| 1 | temperature=0 被 or 短路 | core/llm_client.py:84,166 | Bug |
| 2 | COSINE 分数语义颠倒 | knowledge/milvus_kb.py:244 | Bug |
| 3 | Swarm 模式短期记忆丢失 | agents/base_agent.py:147 | Bug |
| 4 | 历史双重注入+记忆套娃 | swarm_coordinator.py:124 / consultation_agent.py:110 / agent_loop.py:87,323 | Bug |
| 5 | 异常重试产生非法消息序列 | core/agent_loop.py:247 | Bug |
| 6 | 工具上限后空转 | core/agent_loop.py:131 | Bug |
| 7 | 同步调用阻塞事件循环 | long_term.py / skills/search.py:42 | Bug |
| 8 | 杂项（\\n、未导入、文档不一致等） | 见第二节表格 | 小 |
| 9 | 路由无结构化输出、规则未接入 | lead_agent.py:216 / validator.py:215 | P0 |
| 10 | usage 丢弃、无 trace | core/llm_client.py:97 | P0 |
| 11 | 无服务层/流式，事件只写不读 | main.py / events.py | P1 |
| 12 | 无评估体系 | examples/test_all.py | P1 |
| 13 | RAG：硬切块/单路召回/无 rerank/like 过滤/参数 schema 差 | milvus_kb.py / skill_registry_mixin.py:69 | P2 |
| 14 | 安全靠关键词、违规不阻断、日志含 PII | constraints/validator.py | P2 |
| 15 | "去中心化"宣传与实现不符 | README / lead_agent.py:246 | P2 |
| 16 | 配置 hack、无 Docker/CI、prompt 散乱 | 多处 | P2 |
