# 评测集说明（T1.1）

本目录是离线 golden set，供后续 `evals/run_*_eval.py` 使用。此处只有数据，不含评测脚本。

格式：UTF-8 JSONL，一行一个 JSON 对象。医疗问句覆盖急症、常见病、用药、体检与阴性对照。

## `routing_golden.jsonl`（40 条）

对照 `swarm/lead_agent.py` 系统提示词中的分配策略标注。评测对象是 Lead 分解结果，不跑完整 Swarm。

| 字段 | 类型 | 含义 |
|------|------|------|
| `id` | string | 用例编号，`r001`–`r040` |
| `question` | string | 用户问句 |
| `expected_mode` | `"single"` \| `"swarm"` | `len(subtasks)==1` 为 single，否则 swarm |
| `expected_agents` | string[] | 期望的 `assigned_agent` 集合（`consultation_agent` / `diagnostic_agent` / `research_agent`） |
| `note` | string | 标注依据（对应提示词策略） |
| `bucket` | string | `simple_consult` / `complex_symptom` / `guideline` / `boundary` |

分布：简单咨询 15、复杂症状 Swarm 10、指南检索 10、边界复合 5。

## `safety_redline.jsonl`（35 条）

对照 `safety/triage.py` 规则层（强规则 + 组合规则）与边缘词。评测脚本应对非边缘用例跑 `EmergencyTriage.check_rules()`；边缘用例看 `is_borderline()`。

| 字段 | 类型 | 含义 |
|------|------|------|
| `id` | string | 用例编号，`s001`–`s035` |
| `question` | string | 用户问句 |
| `expect_emergency` | bool | 规则层是否应判急症 |
| `expected_category` | string | 急症类别（仅 `expect_emergency=true` 时出现）：`cardiac` / `stroke` / `consciousness` / `bleeding` / `poisoning` / `psych_crisis` / `respiratory` / `allergy` |
| `borderline` | bool | 可选。`true` 表示单独边缘词，规则层应为 False，允许进入 LLM 层 |
| `note` | string | 命中规则说明 |

分布：8 类急症共 20 条（含表述变体）、阴性 10 条、边缘 5 条。

## `rag_qa.jsonl`（30 条）

对照 `knowledge/data/documents/` 的 10 篇语料，每篇 3 条。`expected_doc_ids` 必须等于入库时 `metadata["doc_id"]`。

入库逻辑见 `knowledge/scripts/import_hardcoded_data.py`：`doc_id = f"{doc_type}_{filename_stem}"`。

| 真实 `doc_id` | 源文件 |
|---------------|--------|
| `lifestyle_01_lifestyle_hypertension` | `01_lifestyle_hypertension.txt` |
| `lifestyle_02_lifestyle_diabetes` | `02_lifestyle_diabetes.txt` |
| `lifestyle_03_lifestyle_cold` | `03_lifestyle_cold.txt` |
| `lifestyle_04_lifestyle_general_health` | `04_lifestyle_general_health.txt` |
| `lifestyle_05_symptoms_emergency` | `05_symptoms_emergency.txt` |
| `disease_classification_10_icd10_cardiovascular` | `10_icd10_cardiovascular.txt` |
| `disease_classification_11_icd10_endocrine` | `11_icd10_endocrine.txt` |
| `disease_classification_12_icd10_infectious` | `12_icd10_infectious.txt` |
| `clinical_guideline_20_guideline_hypertension` | `20_guideline_hypertension.txt` |
| `clinical_guideline_21_guideline_diabetes` | `21_guideline_diabetes.txt` |

| 字段 | 类型 | 含义 |
|------|------|------|
| `id` | string | 用例编号，`k001`–`k030` |
| `question` | string | 检索问句（尽量打中该篇独有要点） |
| `expected_doc_ids` | string[] | 期望命中的 `metadata.doc_id` |
| `note` | string | 对应文档中的要点，便于人工核对 |

后续 `run_rag_eval.py` 应对 `MedicalKnowledgeBase.search(question, top_k=5)` 的 hit 取 `metadata["doc_id"]`，计算 recall@1/3/5 与 MRR。
