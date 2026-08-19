# MediX 评测报告（2026-08-19）

本报告由 `evals/report.py` 根据 `evals/results/` 下各任务最新 JSON 汇总。
安全与 RAG 默认为离线；路由若 `mode=offline_heuristic` 则不是真实 LLM 数字。

## 总表

| 任务 | 模式 | 样本量 | 关键指标 |
| --- | --- | --- | --- |
| 安全红线 | rules_only | 35 | 急症召回 100.0%，漏报 0，误伤率 0.0%，非边缘准确率 100.0%，红线通过 是 |
| 路由 | offline_heuristic | 40 | 模式准确率 87.5%，Agent 全匹配 87.5%，Agent 部分匹配 12.5% |
| RAG | local_milvus | 30 | recall@1 83.3%，recall@3 100.0%，recall@5 100.0%，MRR 0.9056 |

## 数据来源

- `safety`: `safety_20260819_142229.json`（generated_at=2026-08-19T14:22:29+08:00）
- `routing`: `routing_20260819_142230.json`（generated_at=2026-08-19T14:22:30+08:00）
- `rag`: `rag_20260819_142351.json`（generated_at=2026-08-19T14:23:51+08:00）

## 安全红线明细

- 模式: `rules_only`
- 非边缘准确率: 100.0%
- 急症召回: 100.0%（漏报 0）
- 误伤率: 0.0%（误报 0）
- 类别准确率: 100.0%
- 边缘词 LLM 交接准确率: 100.0%
- 红线通过（漏报=0）: 是

### 失败用例

无失败用例。

## 路由明细

- 模式: `offline_heuristic`
- 说明: offline_heuristic 按 LeadAgent 提示词策略做规则分解，不调用 LLM；正式数字请去掉 --offline 跑真实分解。
- 模式准确率: 87.5%
- Agent 完全匹配: 87.5%
- Agent 部分匹配: 12.5%
- 全对（模式+Agent）: 87.5%
- 错误条数: 0

### 失败用例

| id | verdict | expected_mode | pred_mode | expected_agents | pred_agents | question |
| --- | --- | --- | --- | --- | --- | --- |
| r026 | partial | single | swarm | research_agent | consultation_agent, research_agent | 糖尿病最新诊疗指南是什么？ |
| r028 | partial | single | swarm | research_agent | consultation_agent, research_agent | 中国高血压防治指南的诊断标准是什么？ |
| r029 | partial | swarm | single | consultation_agent, research_agent | research_agent | 2型糖尿病一线降糖药怎么按指南选？ |
| r030 | partial | single | swarm | research_agent | consultation_agent, research_agent | 冠心病的临床诊疗规范是什么？ |
| r035 | partial | single | swarm | research_agent | consultation_agent, research_agent | 痛风急性发作的指南推荐治疗是什么？ |

## RAG 明细

- 模式: `local_milvus`
- recall@1: 83.3%
- recall@3: 100.0%
- recall@5: 100.0%
- MRR: 0.9056

### recall@5 未全中

无失败用例。

## 复现命令

```powershell
.venv\Scripts\python.exe evals\run_safety_eval.py
.venv\Scripts\python.exe evals\run_routing_eval.py --offline
.venv\Scripts\python.exe evals\run_rag_eval.py
.venv\Scripts\python.exe evals\report.py
```

真实 LLM 路由（需父目录 `config.py` 中的 API key，不要把 key 写进仓库）:

```powershell
.venv\Scripts\python.exe evals\run_routing_eval.py
```

