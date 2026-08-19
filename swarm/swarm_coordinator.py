"""
SwarmCoordinator：Swarm 入口和智能路由

注意：这不是编排器！
- 只负责路由决策：简单问题 → 单 Agent，复杂问题 → Swarm
- 不控制 Agent 执行
- 不编排任务顺序

类比：交通信号灯，决定车辆走哪条路，但不控制车辆如何行驶
"""
import asyncio
import time
import uuid
from datetime import datetime
from typing import Dict, Any, Optional, List
from loguru import logger

from core import LLMClient
from .shared_context import SharedContext, emit_live_event
from .lead_agent import LeadAgent
from .events import Event, EventType
from agents import ConsultationAgent, DiagnosticAgent, ResearchAgent
from memory import SessionSummaryManager, SessionSummary, ShortTermMemory, LongTermMemory
from memory.short_term import get_redis_config
from core.source_collector import get_sources, start_collect, stop_collect
from core.tracing import add_span, end_trace, get_trace, save_trace, start_trace
from safety import EmergencyTriage, build_emergency_result
from validation.guardrail import OutputGuardrail, apply_guardrail_to_result


class SwarmCoordinator:
    """
    Swarm 协调器

    职责：
    1. 智能路由（简单 → 单 Agent，复杂 → Swarm）
    2. 初始化 SharedContext
    3. 启动和监控 Swarm
    4. 生成 SessionSummary

    不做：
    - 不编排 Worker 执行顺序
    - 不直接调用 Worker
    - 不控制任务分配
    """

    def __init__(
        self,
        llm_client: Optional[LLMClient] = None,
        enable_swarm: bool = True
    ):
        self.llm_client = llm_client or LLMClient()
        self.enable_swarm = enable_swarm

        # 初始化 Agent
        self.lead_agent = LeadAgent(llm_client=self.llm_client)
        self.consultation_agent = ConsultationAgent()
        self.diagnostic_agent = DiagnosticAgent()
        self.research_agent = ResearchAgent()

        # Worker 池
        self.worker_pool: List[Any] = [
            self.consultation_agent,
            self.diagnostic_agent,
            self.research_agent
        ]

        # 急症分诊器（输入侧 fail-fast）
        self.triage = EmergencyTriage(llm_client=self.llm_client)
        # 输出侧护栏（规则常开；LLM 只在违规时重写）。急症短路路径不调用。
        self.guardrail = OutputGuardrail(llm_client=self.llm_client)

        # 记忆管理器
        self.session_manager = SessionSummaryManager()
        # 必须由 Coordinator 先以 redis 初始化单例；连不上时 ShortTermMemory 内部降级内存
        self.short_term_memory = ShortTermMemory(
            storage_type="redis",
            redis_config=get_redis_config(),
        )
        self.long_term_memory = LongTermMemory()

        # 将短期记忆注入到所有 Worker Agent 的 Loop
        # 注意：LeadAgent 不继承 BaseAgent，没有 loop 属性，不需要注入
        for worker in self.worker_pool:
            if hasattr(worker, 'loop'):
                worker.loop.short_term_memory = self.short_term_memory

        logger.info(f"SwarmCoordinator initialized with {len(self.worker_pool)} workers")
        logger.info(f"Memory system: short_term={self.short_term_memory.storage_type}, long_term={'enabled' if self.long_term_memory.enabled else 'disabled'}")

    async def _apply_output_guardrail(
        self,
        question: str,
        result: Dict[str, Any],
        session_id: str,
        shared_context: Any = None,
        overwrite_memory: bool = True,
    ) -> None:
        """最终答案护栏。失败时放行原文并记一条错误，不让主流程崩溃。"""
        guardrail_t0 = time.monotonic()
        try:
            try:
                await apply_guardrail_to_result(
                    self.guardrail,
                    question,
                    result,
                    session_id=session_id,
                    shared_context=shared_context,
                )
            except Exception as e:
                logger.error(f"Output guardrail failed, keeping original answer: {e}")
                try:
                    from validation.safety_log import record as persist_record

                    persist_record(
                        kind="guardrail_error",
                        detail=str(e)[:200],
                        session_id=session_id,
                        source="guardrail",
                    )
                except Exception:
                    pass
                return

            if not overwrite_memory:
                return
            if not (result.get("guardrail") or {}).get("rewritten"):
                return
            # 单 Agent 路径的短期记忆已由 AgentLoop 写入原文，这里覆盖成护栏后的终稿。
            # Swarm 路径此时还没写入本轮 assistant，绝不能覆盖上一轮。
            try:
                self.short_term_memory.update_last_assistant(
                    session_id, result.get("answer") or ""
                )
            except Exception as e:
                logger.warning(f"Failed to overwrite short-term memory after guardrail: {e}")
        finally:
            add_span(
                "guardrail",
                "phase",
                guardrail_t0,
                time.monotonic(),
                {
                    "rewritten": bool((result.get("guardrail") or {}).get("rewritten")),
                },
            )

    def _get_agent_by_id(self, agent_id: str):
        """根据 agent_id 返回对应的 Agent 实例"""
        mapping = {
            "consultation_agent": self.consultation_agent,
            "diagnostic_agent": self.diagnostic_agent,
            "research_agent": self.research_agent
        }
        return mapping.get(agent_id)

    async def process(
        self,
        question: str,
        context: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None,
        trace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        处理用户问题

        Args:
            question: 用户问题
            context: 额外上下文（年龄、既往史等）
            session_id: 会话ID（如果不提供，将自动生成）
            trace_id: 请求级追踪 ID（webapi 入口生成；缺省则本地生成）

        Returns:
            处理结果
        """
        start_time = datetime.now()
        if session_id is None:
            session_id = f"{start_time.strftime('%Y%m%d-%H%M%S')}-{str(uuid.uuid4())[:8]}"

        collect_token = start_collect()
        # 分诊之前就 start，急症短路也能留下一条可对比的 trace；
        # 且 Processing 日志能带上 extra.trace（patcher 读 ContextVar）
        trace_token = start_trace(session_id, trace_id=trace_id)
        logger.info(f"Processing question (session={session_id}): {question[:50]}...")
        try:
            result = await self._process_inner(
                question=question,
                context=context,
                session_id=session_id,
                start_time=start_time,
            )
            self._attach_trace(result)
            return result
        finally:
            save_trace()
            end_trace(trace_token)
            stop_collect(collect_token)

    async def _process_inner(
        self,
        question: str,
        context: Optional[Dict[str, Any]],
        session_id: str,
        start_time: datetime,
    ) -> Dict[str, Any]:
        # ===== Step 0: 急症分诊（fail-fast）=====
        # 命中急症时跳过记忆检索、任务分解和 Swarm，秒级返回结构化急救指引
        triage_t0 = time.monotonic()
        triage_result = await self.triage.triage(question)
        add_span(
            "triage",
            "phase",
            triage_t0,
            time.monotonic(),
            {
                "emergency": bool(triage_result.is_emergency),
                "category": getattr(triage_result, "category", "") or "",
                "method": getattr(triage_result, "method", "") or "",
            },
        )
        if triage_result.is_emergency:
            result = await self._handle_emergency(
                question=question,
                triage_result=triage_result,
                session_id=session_id,
                start_time=start_time,
            )
            self._attach_sources(result)
            return result

        # ===== 统一的记忆检索（所有模式都使用）=====
        # 1. 检索长期记忆（相似历史会话）
        # 注意：短期记忆（当前会话历史）不放进 context——
        # AgentLoop 会通过 get_history() 把历史作为独立 messages 注入一次，
        # 若再塞进 context 会导致同一份历史进两次 prompt（且是 dict 字符串形式）
        # Mem0 SDK 是同步 HTTP 客户端；在 async 路径直接调用会卡住事件循环，
        # Swarm 的 asyncio.gather 并行也会被串行化。放到线程池执行。
        mem0_t0 = time.monotonic()
        similar_memories = await asyncio.to_thread(
            self.long_term_memory.search_similar_sessions,
            query=question,
            limit=3,
        )
        add_span(
            "mem0_search",
            "phase",
            mem0_t0,
            time.monotonic(),
            {"hits": len(similar_memories or [])},
        )

        # 2. 构建增强上下文
        enhanced_context = context or {}

        # 添加长期记忆
        if similar_memories:
            enhanced_context["historical_cases"] = [
                {
                    "summary": mem["content"],
                    "score": mem["score"]
                }
                for mem in similar_memories
            ]
            logger.info(f"Found {len(similar_memories)} similar historical cases from long-term memory")

        # Step 1: LeadAgent 分解任务
        decompose_t0 = time.monotonic()
        assessment = await self.lead_agent.assess_and_decompose(question, enhanced_context)
        subtasks = assessment.get("subtasks", [])
        add_span(
            "decompose",
            "phase",
            decompose_t0,
            time.monotonic(),
            {"subtasks": len(subtasks)},
        )

        logger.info(f"LeadAgent 分解任务：{len(subtasks)} 个")

        # Step 2: 根据任务数量路由
        final_answer = None
        mode = None
        routed_agent_id = "consultation_agent"

        if len(subtasks) == 1:
            # 单任务 → 直接调用对应 Agent
            task = subtasks[0]
            agent_id = task.get("assigned_agent")
            agent = self._get_agent_by_id(agent_id)

            if agent is None:
                # 如果找不到 Agent，降级到 ConsultationAgent
                logger.warning(f"Unknown agent_id: {agent_id}, fallback to ConsultationAgent")
                agent = self.consultation_agent

            logger.info(f"Route: Single Agent ({agent_id})")
            mode = "single_agent"
            routed_agent_id = getattr(agent, "agent_id", None) or agent_id or "consultation_agent"
            result = await agent.process({
                'question': question,
                'context': enhanced_context,
                'session_id': session_id
            })
            final_answer = result.get('answer', '')

            result.update({
                'swarm_enabled': False,
                'session_id': session_id,
                'route_reason': f'单任务路由到 {agent_id}'
            })

            # 确保单Agent模式下也有 disclaimer 字段
            if 'disclaimer' not in result:
                result['disclaimer'] = "⚠️ 以上信息仅供参考，不能替代专业医生的诊断和治疗。如有疑虑，请及时就医。"

            # 确保单Agent模式下也有 suggestions 字段
            if 'suggestions' not in result:
                result['suggestions'] = []

        elif len(subtasks) >= 2 and self.enable_swarm:
            # 多任务 → 启动 Swarm
            logger.info(f"Route: Swarm (Multi-Agent Collaboration) - {len(subtasks)} tasks")
            mode = "swarm"
            result = await self._process_with_swarm(
                question=question,
                context=enhanced_context,
                assessment=assessment,
                session_id=session_id,
                start_time=start_time
            )
            final_answer = result.get('answer', '')

            # Swarm 模式已经在 _process_with_swarm 中保存了长期记忆，直接返回
            self._attach_sources(result)
            return result

        else:
            # 0个任务或Swarm关闭 → 降级到 ConsultationAgent
            if len(subtasks) == 0:
                logger.warning("No subtasks generated, fallback to ConsultationAgent")
                mode = "fallback"
            else:
                logger.info("Swarm disabled, fallback to ConsultationAgent")
                mode = "disabled_swarm"

            result = await self.consultation_agent.process({
                'question': question,
                'context': enhanced_context,
                'session_id': session_id
            })
            final_answer = result.get('answer', '')
            routed_agent_id = getattr(self.consultation_agent, "agent_id", "consultation_agent")
            result.update({
                'swarm_enabled': False,
                'session_id': session_id
            })

        await self._apply_output_guardrail(question, result, session_id)
        final_answer = result.get("answer") or ""

        # ===== 统一的记忆保存（非 Swarm 模式）=====
        end_time = datetime.now()

        # 注意：短期记忆已经在 Agent Loop 中保存了，这里不需要重复保存

        try:
            summary = SessionSummary.from_single_agent(
                session_id=session_id,
                question=question,
                final_answer=final_answer or "",
                agent_id=str(result.get("agent_id") or routed_agent_id),
                start_time=start_time,
                end_time=end_time,
                mode=mode or "single_agent",
            )
            self.session_manager.save_summary(summary)
        except Exception as e:
            logger.error(f"Failed to generate session summary: {e}")

        # 保存到长期记忆
        try:
            await asyncio.to_thread(
                self.long_term_memory.add_session_summary,
                session_id=session_id,
                question=question,
                answer=final_answer,
                metadata={
                    "mode": mode,
                    "subtasks_count": len(subtasks),
                    "total_time": (end_time - start_time).total_seconds(),
                },
            )
            logger.info(f"Saved to long-term memory (session={session_id}, mode={mode})")
        except Exception as e:
            logger.error(f"Failed to save to long-term memory: {e}")

        self._attach_sources(result)
        return result

    @staticmethod
    def _attach_sources(result: Dict[str, Any]) -> None:
        """写入去重后的 RAG 引用来源。护栏只改 answer，不会丢掉本字段。"""
        result["sources"] = get_sources()

    @staticmethod
    def _attach_trace(result: Dict[str, Any]) -> None:
        """answer_done 需要 trace_id；usage 由 LLMClient → record_llm_usage 累加。"""
        trace = get_trace()
        if trace is not None:
            result["trace"] = trace.summary()

    async def _handle_emergency(
        self,
        question: str,
        triage_result,
        session_id: str,
        start_time: datetime,
    ) -> Dict[str, Any]:
        """
        急症短路路径：发布事件 → 生成急救指引 → 写短期记忆和会话总结。

        刻意不写 Mem0 长期记忆（急症指引是模板化应急输出，
        不是有复用价值的"相似案例"，且要保证秒级返回）。
        """
        emit_live_event(Event(
            type=EventType.EMERGENCY_TRIGGERED,
            source_agent="emergency_triage",
            data=triage_result.to_dict(),
        ))

        result = build_emergency_result(question, triage_result, session_id)
        end_time = datetime.now()
        result["total_time"] = (end_time - start_time).total_seconds()

        try:
            self.short_term_memory.add_message(
                session_id=session_id, role="user", content=question
            )
            self.short_term_memory.add_message(
                session_id=session_id, role="assistant", content=result["answer"]
            )
        except Exception as e:
            logger.error(f"Failed to save emergency conversation to short-term memory: {e}")

        try:
            summary = SessionSummary.from_single_agent(
                session_id=session_id,
                question=question,
                final_answer=result["answer"],
                agent_id="emergency_triage",
                start_time=start_time,
                end_time=end_time,
                mode="emergency",
            )
            self.session_manager.save_summary(summary)
        except Exception as e:
            logger.error(f"Failed to generate emergency session summary: {e}")

        logger.warning(
            f"🚨 Emergency fail-fast completed in {result['total_time']:.2f}s "
            f"(category={triage_result.category}, session={session_id})"
        )
        return result

    async def _process_with_swarm(
        self,
        question: str,
        context: Optional[Dict[str, Any]],
        assessment: Dict[str, Any],
        session_id: str,
        start_time: datetime
    ) -> Dict[str, Any]:
        """
        使用 Swarm 处理复杂问题

        这是群体智能的核心流程

        注意：context 已经包含了长期记忆 historical_cases（在 process() 中注入）；
        当前会话历史由 AgentLoop 通过 get_history() 注入 messages，不放进 context
        """

        # 创建 SharedContext
        shared_context = SharedContext(session_id=session_id)

        # 附加 SharedContext 到所有 Worker
        for worker in self.worker_pool:
            worker.attach_shared_context(shared_context)

        # 发布 Swarm 启动事件
        shared_context.publish_event(Event(
            type=EventType.SWARM_STARTED,
            source_agent="swarm_coordinator",
            data={
                "question": question,
                "num_subtasks": len(assessment.get("subtasks", []))
            }
        ))

        # Step 1: LeadAgent 分解任务
        subtasks = self.lead_agent.create_subtasks(assessment, shared_context)
        logger.info(f"Created {len(subtasks)} subtasks")

        # Step 2: Worker 执行分配的任务（并行）
        tasks = []
        for worker in self.worker_pool:
            task = asyncio.create_task(
                self._worker_execute_assigned_tasks(worker, shared_context)
            )
            tasks.append(task)

        # 等待所有 Worker 完成（或超时）
        timeout_occurred = False
        try:
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=90.0  # 增加超时时间到 90 秒，应对复杂案例
            )
        except asyncio.TimeoutError:
            timeout_occurred = True
            logger.warning("Swarm execution timeout (90s)")
            # 记录哪些 Agent 已完成，哪些未完成
            completed_agents = list(shared_context.agent_contributions.keys())
            claimed_tasks = [
                (subtask.assigned_to, subtask.type)
                for subtask in shared_context.task_decomposition.values()
                if subtask.status.value == "claimed"
            ]
            logger.info(f"Completed agents: {completed_agents}")
            logger.info(f"Timed out tasks: {claimed_tasks}")

        # Step 3: LeadAgent 汇总结果
        # 即使超时，也尝试汇总已完成的部分结果
        synth_t0 = time.monotonic()
        final_answer = await self.lead_agent.synthesize_results(
            question=question,
            shared_context=shared_context,
            timeout_occurred=timeout_occurred
        )
        add_span("synthesize", "phase", synth_t0, time.monotonic())

        # Lead 汇总目前没有 AutoFixer；护栏补上这一层。急症路径不会走到这里。
        swarm_result = {"answer": final_answer}
        await self._apply_output_guardrail(
            question,
            swarm_result,
            session_id,
            shared_context=shared_context,
            overwrite_memory=False,
        )
        final_answer = swarm_result.get("answer") or ""

        end_time = datetime.now()

        # Step 4: 生成 SessionSummary
        try:
            summary = SessionSummary.from_shared_context(
                session_id=session_id,
                question=question,
                shared_context=shared_context,
                final_answer=final_answer,
                start_time=start_time,
                end_time=end_time
            )
            self.session_manager.save_summary(summary)
        except Exception as e:
            logger.error(f"Failed to generate session summary: {e}")

        # Swarm 模式由 Coordinator 统一写入短期记忆：一条原始 user 问题 + 一条最终综合答案。
        # Worker 的 AgentLoop 以 record_memory=False 运行（只读历史不写入），
        # 避免 3 个 Worker 各自写入交错的中间工具调用流水
        try:
            self.short_term_memory.add_message(
                session_id=session_id,
                role="user",
                content=question
            )
            self.short_term_memory.add_message(
                session_id=session_id,
                role="assistant",
                content=final_answer
            )
            logger.info(f"Saved swarm conversation to short-term memory (session={session_id})")
        except Exception as e:
            logger.error(f"Failed to save to short-term memory: {e}")

        # 保存到 Mem0 长期记忆
        try:
            # 保存会话总结
            await asyncio.to_thread(
                self.long_term_memory.add_session_summary,
                session_id=session_id,
                question=question,
                answer=final_answer,
                metadata={
                    "mode": "swarm",
                    "agents_count": len(shared_context.agent_contributions),
                    "total_time": (end_time - start_time).total_seconds(),
                    "timeout_occurred": timeout_occurred
                },
            )

            logger.info(f"Saved to Mem0 long-term memory (session={session_id})")

        except Exception as e:
            logger.error(f"Failed to save to Mem0: {e}")

        # 发布 Swarm 完成事件
        shared_context.publish_event(Event(
            type=EventType.SWARM_COMPLETED,
            source_agent="swarm_coordinator",
            data={
                "duration": (end_time - start_time).total_seconds(),
                "agents_count": len(shared_context.agent_contributions)
            }
        ))

        # 返回结果
        completed_agents = list(shared_context.agent_contributions.keys())
        result = {
            'answer': final_answer,
            'swarm_enabled': True,
            'session_id': session_id,
            'agents_involved': completed_agents,
            'subtasks_completed': len(shared_context.get_all_completed_subtasks()),
            'total_time': (end_time - start_time).total_seconds(),
            'swarm_metadata': shared_context.get_summary(),
            'timeout_occurred': timeout_occurred
        }
        if swarm_result.get("guardrail"):
            result["guardrail"] = swarm_result["guardrail"]

        # 提取建议和免责声明（简化实现）
        result['suggestions'] = self._extract_suggestions(final_answer)

        # 根据是否超时调整免责声明
        if timeout_occurred and not completed_agents:
            result['disclaimer'] = "由于系统超时，未能提供完整分析。建议简化问题重试，或在紧急情况下立即就医。"
        elif timeout_occurred:
            result['disclaimer'] = f"以上分析基于 {len(completed_agents)} 个 Agent 的部分协作结果（部分分析模块超时未完成），仅供参考，不能替代医生诊断。"
        else:
            result['disclaimer'] = "以上分析基于多个专业 Agent 的协作，仅供参考，不能替代医生诊断。"

        return result

    async def _worker_execute_assigned_tasks(
        self,
        worker: Any,
        shared_context: SharedContext
    ):
        """
        Worker 执行分配给它的任务

        简化后的流程：
        - 查找分配给自己的任务
        - 执行任务
        - 记录结果
        """
        try:
            # 获取分配给该 Agent 的任务
            assigned_tasks = shared_context.get_subtasks_for_agent(worker.agent_id)

            if not assigned_tasks:
                logger.debug(f"{worker.agent_id}: No assigned tasks")
                return

            # 并行执行所有分配的任务
            tasks = []
            for subtask in assigned_tasks:
                logger.info(f"{worker.agent_id}: Starting {subtask.type}")
                shared_context.start_subtask(subtask.id)

                task = asyncio.create_task(
                    self._execute_single_subtask(worker, subtask, shared_context)
                )
                tasks.append(task)

            # 等待所有任务完成
            await asyncio.gather(*tasks, return_exceptions=True)

        except Exception as e:
            logger.error(f"{worker.agent_id}: Error processing subtask: {e}")

    async def _execute_single_subtask(self, worker, subtask, shared_context):
        """执行单个子任务"""
        try:
            result = await worker.process_subtask(subtask)
            shared_context.complete_subtask(subtask.id, worker.agent_id, result)
            logger.info(f"{worker.agent_id}: Completed {subtask.type}")
        except Exception as e:
            logger.error(f"{worker.agent_id}: Error in {subtask.type}: {e}")

    def _extract_suggestions(self, final_answer: str) -> List[str]:
        """从最终答案中提取建议（简化实现）"""
        suggestions = []

        # 简单的文本匹配
        if "【核心建议】" in final_answer:
            # 提取核心建议部分
            start_idx = final_answer.find("【核心建议】")
            end_idx = final_answer.find("【", start_idx + 1)
            if end_idx == -1:
                end_idx = len(final_answer)

            suggestions_text = final_answer[start_idx:end_idx]

            # 提取编号列表
            import re
            matches = re.findall(r'\d+\.\s*([^\n]+)', suggestions_text)
            suggestions = matches[:5]  # 最多5条

        return suggestions or ["请遵循医嘱，注意休息和营养"]

async def process_with_swarm(
    question: str,
    context: Optional[Dict[str, Any]] = None,
    enable_swarm: bool = True,
    session_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    便捷函数：使用 Swarm 处理问题

    Args:
        question: 用户问题
        context: 额外上下文
        enable_swarm: 是否启用 Swarm（False 则总是用单 Agent）
        session_id: 会话ID（如果提供，将使用该ID而不是生成新的）

    Returns:
        处理结果
    """
    coordinator = SwarmCoordinator(enable_swarm=enable_swarm)
    return await coordinator.process(question, context, session_id=session_id)
