"""
Agent循环引擎
实现 LLM 驱动的 Skill 调用循环
支持短期记忆集成
支持约束验证（Harness Engineering）
"""
import uuid
import json
import time
from typing import Dict, Any, List, Optional
from loguru import logger

from .state_manager import StateManager, TaskStatus
from .llm_client import LLMResponse
from .tracing import add_span

# Harness Engineering: 约束验证和自动修复
try:
    from constraints import ConstraintValidator
    from validation import AutoFixer
    CONSTRAINTS_ENABLED = True
except ImportError:
    logger.warning("Constraints module not found, running without constraint validation")
    CONSTRAINTS_ENABLED = False


class AgentLoop:
    """
    Agent循环引擎
    LLM 自主决策 Skill 调用，循环直到任务完成

    功能：
    - 支持短期记忆（ShortTermMemory）
    - 自动记录每轮的 user/assistant 消息
    """

    def __init__(self, max_iterations: int = 10, short_term_memory: Optional[Any] = None, max_tool_calls: int = 2):
        """
        初始化Agent循环引擎

        Args:
            max_iterations: 最大迭代次数（防止无限循环）
            short_term_memory: 短期记忆管理器（可选）
            max_tool_calls: 最大 Skill 调用次数（硬性限制，默认2次）
        """
        self.max_iterations = max_iterations
        self.max_tool_calls = max_tool_calls
        self.state_manager = StateManager()
        self.short_term_memory = short_term_memory
        self.tool_call_count = 0

        # Harness Engineering: 约束验证器和自动修复器
        self.validator = ConstraintValidator() if CONSTRAINTS_ENABLED else None
        self.auto_fixer = AutoFixer() if CONSTRAINTS_ENABLED else None
        if CONSTRAINTS_ENABLED:
            logger.debug("✅ Constraint validation enabled")

    async def run(
        self,
        agent,
        input_data: Dict[str, Any],
        session_id: Optional[str] = None,
        record_memory: bool = True
    ) -> Dict[str, Any]:
        """
        执行Agent循环

        Args:
            agent: Agent实例
            input_data: 输入数据
            session_id: 会话ID（用于读取/写入短期记忆）
            record_memory: 是否写入短期记忆。Swarm 模式下 Worker 传 False
                （读历史仍然生效），写入由 Coordinator 统一完成

        Returns:
            最终结果
        """
        task_id = str(uuid.uuid4())
        state = self.state_manager.create_state(
            task_id=task_id,
            agent_id=agent.agent_id,
            input_data=input_data,
            max_iterations=self.max_iterations
        )

        # 重置计数
        self.tool_call_count = 0

        logger.info(f"Starting Agent Loop for {agent.agent_id}, task_id={task_id}")

        # 是否由本 Loop 写入短期记忆（读取不受此开关影响）
        should_record = bool(self.short_term_memory and session_id and record_memory)

        try:
            state.status = TaskStatus.IN_PROGRESS

            # 初始化消息历史（包含历史对话）
            messages = self._initialize_messages(agent, input_data, session_id)

            # 记录用户消息到短期记忆
            # 注意：只存原始用户问题，不存格式化后的完整 prompt（含系统信息/背景信息前缀）。
            # 否则下一轮取历史时，历史里会嵌着上一轮的历史，指数级套娃
            if should_record:
                raw_question = (
                    input_data.get('question')
                    or input_data.get('query')
                    or (messages[-1]["content"] if messages else str(input_data))
                )
                self.short_term_memory.add_message(
                    session_id=session_id,
                    role="user",
                    content=raw_question
                )
                logger.debug(f"Recorded user message to short-term memory (session={session_id})")

            # 获取 Agent 的 Skills (OpenAI format)
            tools_openai_format = agent.get_tools_for_llm()

            logger.debug(f"Agent has {len(tools_openai_format) if tools_openai_format else 0} skills available")

            # 达到工具调用上限后置为 True：下一轮以 tool_choice="none" 调用，
            # 从协议上禁止 LLM 继续返回 tool_calls（否则可能空转到 max_iterations）
            force_final_answer = False

            # 主循环：LLM → Skill Calls → Results → LLM
            while state.should_continue():
                state.iteration += 1
                logger.debug(f"=== Iteration {state.iteration}/{state.max_iterations} ===")

                # 事务快照：本轮追加的 "assistant(tool_calls) + 全部 tool 结果" 是一个事务单元。
                # 若中途异常，回滚到快照，保证重试时消息序列仍满足
                # OpenAI 协议（每个 tool_call 必须有对应的 tool 消息）
                messages_snapshot_len = len(messages)

                try:
                    # 调用 LLM（可能返回 tool_calls）
                    # 仅单 Agent（record_memory=True）且 webapi 挂了回调时流式，避免 Swarm Worker 文字交错
                    on_delta = self._final_answer_delta_cb(record_memory)
                    llm_t0 = time.monotonic()
                    try:
                        llm_response: LLMResponse = await agent.llm_client.chat_with_tools(
                            messages=messages,
                            tools=tools_openai_format,
                            tool_choice="none" if force_final_answer else "auto",
                            temperature=agent.config.get('temperature', 0.7),
                            stream=on_delta is not None,
                            on_delta=on_delta,
                        )
                    finally:
                        add_span(
                            "llm_call",
                            "llm",
                            llm_t0,
                            time.monotonic(),
                            {
                                "agent": getattr(agent, "agent_id", ""),
                                "iteration": state.iteration,
                            },
                        )

                    # 记录中间结果
                    state.add_intermediate_result({
                        'iteration': state.iteration,
                        'llm_response': {
                            'content': llm_response.content,
                            'tool_calls': [
                                {'name': tc.name, 'arguments': tc.arguments}
                                for tc in llm_response.tool_calls
                            ],
                            'finish_reason': llm_response.finish_reason
                        }
                    })

                    # 情况1: LLM 返回 tool_calls，执行 Skills
                    if llm_response.has_tool_calls():
                        # 硬性限制：检查是否已达到最大调用次数
                        if self.tool_call_count >= self.max_tool_calls:
                            logger.warning(f"⚠️ 已达到最大 Skill 调用次数限制 ({self.max_tool_calls})，强制生成最终答案")
                            # 强制要求 LLM 提供最终答案：
                            # 仅注入提示词不够——下一轮若仍是 tool_choice="auto"，
                            # LLM 可再次返回 tool_calls 导致空转；置 force_final_answer
                            # 使下一轮以 tool_choice="none" 调用，行为确定化
                            force_final_answer = True
                            messages.append({
                                'role': 'user',
                                'content': f'已完成 {self.max_tool_calls} 次信息检索。请基于已获取的信息提供最终答复。'
                            })
                            continue

                        logger.info(f"LLM requested {len(llm_response.tool_calls)} tool calls (当前已调用 {self.tool_call_count}/{self.max_tool_calls})")

                        # 添加 assistant 消息（包含 tool_calls）
                        messages.append(self._create_assistant_message_with_tools(llm_response))

                        # 注意：中间的工具调用过程不写入短期记忆——
                        # 记忆协议：只存原始用户问题和最终答案（记忆存的是语义事实，不是 prompt 工件）

                        # 执行每个 Skill 调用
                        for tool_call in llm_response.tool_calls:
                            # 增加计数
                            self.tool_call_count += 1
                            logger.debug(f"Executing: {tool_call.name}({tool_call.arguments}) - 第 {self.tool_call_count} 次调用")
                            self._emit_skill_event(agent, input_data, tool_call.name, started=True)
                            skill_ok = True
                            skill_t0 = time.monotonic()

                            try:
                                # Harness Engineering: 验证调用
                                if self.validator:
                                    validation_result = self.validator.validate_tool_call(
                                        agent.agent_id,
                                        tool_call.name
                                    )
                                    if not validation_result.get("valid"):
                                        logger.warning(
                                            f"⚠️ 约束警告: {validation_result.get('reason')}"
                                        )

                                tool_result = await agent.execute_tool(
                                    tool_name=tool_call.name,
                                    arguments=tool_call.arguments
                                )

                                tool_message = agent.llm_client.create_tool_message(
                                    tool_call_id=tool_call.id,
                                    tool_name=tool_call.name,
                                    result=tool_result
                                )
                            except Exception as tool_error:
                                # Skill 执行/结果序列化失败也必须回填一条 tool 消息，
                                # 否则 assistant(tool_calls) 缺少对应 tool 消息，
                                # 下一次 LLM 请求会被 API 以协议违规拒绝（400）
                                skill_ok = False
                                logger.error(f"Tool execution failed: {tool_call.name} - {tool_error}")
                                tool_message = agent.llm_client.create_tool_message(
                                    tool_call_id=tool_call.id,
                                    tool_name=tool_call.name,
                                    result={"success": False, "error": str(tool_error)}
                                )
                            add_span(
                                f"skill:{tool_call.name}",
                                "skill",
                                skill_t0,
                                time.monotonic(),
                                {
                                    "agent": getattr(agent, "agent_id", ""),
                                    "ok": skill_ok,
                                },
                            )

                            # 添加结果消息
                            messages.append(tool_message)
                            self._emit_skill_event(
                                agent, input_data, tool_call.name, started=False, ok=skill_ok
                            )

                        # 继续下一轮循环
                        continue

                    # 情况2: LLM 返回文本响应，任务完成
                    else:
                        logger.info(f"LLM provided final response (no tool calls)")

                        # Harness Engineering: 验证和修复输出
                        final_answer = llm_response.content

                        if self.validator and final_answer:
                            validation_result = self.validator.validate_output(
                                agent.agent_id,
                                final_answer
                            )

                            if not validation_result.get("valid"):
                                logger.warning(
                                    f"⚠️ 输出约束违规: {validation_result.get('violations')}"
                                )

                                # 自动修复
                                if self.auto_fixer and validation_result.get("auto_fixable"):
                                    fixed_answer = self.auto_fixer.fix_output(
                                        final_answer,
                                        validation_result.get("auto_fixable", [])
                                    )
                                    if fixed_answer != final_answer:
                                        logger.info("🔧 输出已自动修复")
                                        final_answer = fixed_answer

                        # 记录最终回答到短期记忆
                        if should_record:
                            self.short_term_memory.add_message(
                                session_id=session_id,
                                role="assistant",
                                content=final_answer or "(empty response)"
                            )
                            logger.debug(f"Recorded final answer to short-term memory (session={session_id})")

                        result = {
                            'answer': final_answer,
                            'iterations': state.iteration,
                            'agent_id': agent.agent_id
                        }

                        # 让 Agent 进行结果后处理（如提取建议等）
                        if hasattr(agent, 'post_process_result'):
                            result = await agent.post_process_result(result, final_answer)

                        state.mark_completed(result)
                        break

                except Exception as e:
                    logger.error(f"Error in iteration {state.iteration}: {e}")
                    # 事务回滚：丢弃本轮追加的不完整消息
                    # （如 assistant(tool_calls) 已入列但 tool 结果缺失），
                    # 使重试携带的消息序列恢复为合法状态
                    if len(messages) > messages_snapshot_len:
                        rollback_count = len(messages) - messages_snapshot_len
                        del messages[messages_snapshot_len:]
                        logger.warning(
                            f"Rolled back {rollback_count} partial message(s) from failed iteration"
                        )
                    if state.iteration >= state.max_iterations:
                        state.mark_failed(str(e))
                        break
                    # 否则继续尝试（消息序列已恢复一致性）

            # 如果达到最大迭代次数但没有完成
            if not state.is_completed():
                logger.warning(f"Max iterations reached without completion")

                # 强制调用 LLM 生成最终总结
                try:
                    logger.info("Forcing LLM to provide final answer")

                    # 添加强制总结的提示
                    messages.append({
                        'role': 'user',
                        'content': '请基于以上信息，提供最终的答复。'
                    })

                    # 调用 LLM（禁用 function calling）
                    on_delta = self._final_answer_delta_cb(record_memory)
                    llm_t0 = time.monotonic()
                    try:
                        final_response = await agent.llm_client.chat_with_tools(
                            messages=messages,
                            tools=None,
                            temperature=0.7,
                            stream=on_delta is not None,
                            on_delta=on_delta,
                        )
                    finally:
                        add_span(
                            "llm_call",
                            "llm",
                            llm_t0,
                            time.monotonic(),
                            {
                                "agent": getattr(agent, "agent_id", ""),
                                "iteration": state.iteration,
                                "forced": True,
                            },
                        )

                    result = {
                        'answer': final_response.content or '抱歉，未能完成任务',
                        'iterations': state.iteration,
                        'warning': 'max_iterations_reached'
                    }

                    # 记录最终回答到短期记忆
                    if should_record:
                        self.short_term_memory.add_message(
                            session_id=session_id,
                            role="assistant",
                            content=result['answer']
                        )

                    state.mark_completed(result)
                    logger.info("Generated fallback answer after max iterations")

                except Exception as e:
                    logger.error(f"Failed to generate fallback answer: {e}")
                    # 降级到简单提取
                    result = {
                        'answer': '抱歉，系统在处理您的问题时遇到了问题。建议您简化问题或稍后重试。',
                        'iterations': state.iteration,
                        'warning': 'max_iterations_reached',
                        'error': str(e)
                    }
                    state.mark_completed(result)

            logger.info(f"Agent Loop finished: status={state.status.value}, iterations={state.iteration}")
            return state.final_result or {}

        except Exception as e:
            logger.error(f"Agent Loop failed: {e}")
            state.mark_failed(str(e))
            raise

    def _initialize_messages(self, agent, input_data: Dict[str, Any], session_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """初始化消息列表，包含历史对话上下文"""
        messages = []

        # 系统提示词
        system_prompt = agent.get_system_prompt()
        if system_prompt:
            messages.append({
                'role': 'system',
                'content': system_prompt
            })

        # 加载历史对话（短期记忆）
        if self.short_term_memory and session_id:
            history = self.short_term_memory.get_history(session_id, limit=5)  # 最近5轮对话
            if history:
                logger.info(f"Loaded {len(history)} historical messages from short-term memory")
                messages.extend(history)

        # 用户输入
        user_message = agent.format_user_input(input_data)
        messages.append({
            'role': 'user',
            'content': user_message
        })

        return messages

    def _final_answer_delta_cb(self, record_memory: bool):
        """Swarm Worker 不把内部 LLM 增量推到 UI；仅单 Agent 最终可见回答流式。"""
        if not record_memory:
            return None
        try:
            from swarm.shared_context import get_answer_delta_listener
            return get_answer_delta_listener()
        except Exception:
            return None

    def _emit_skill_event(
        self,
        agent,
        input_data: Dict[str, Any],
        skill_name: str,
        started: bool,
        ok: bool = True,
    ) -> None:
        """轻量 Skill 事件：有 SharedContext 则广播，否则走 webapi 请求级 listener。"""
        if not skill_name:
            return
        try:
            from swarm.events import Event, EventType
            from swarm.shared_context import emit_live_event
        except ImportError:
            return
        data: Dict[str, Any] = {
            "name": skill_name,
            "skill_name": skill_name,
            "agent": getattr(agent, "agent_id", ""),
            "ok": ok,
        }
        subtask_id = (input_data or {}).get("subtask_id")
        if subtask_id:
            data["subtask_id"] = subtask_id
        event = Event(
            type=EventType.SKILL_STARTED if started else EventType.SKILL_COMPLETED,
            source_agent=getattr(agent, "agent_id", "") or "unknown",
            data=data,
        )
        emit_live_event(event, getattr(agent, "shared_context", None))

    def _create_assistant_message_with_tools(self, llm_response: LLMResponse) -> Dict[str, Any]:
        """创建包含 tool_calls 的 assistant 消息"""
        message = {
            'role': 'assistant',
            'content': llm_response.content or None
        }

        # 添加 tool_calls（OpenAI 格式）
        if llm_response.tool_calls:
            message['tool_calls'] = [
                {
                    'id': tc.id,
                    'type': 'function',
                    'function': {
                        'name': tc.name,
                        'arguments': json.dumps(tc.arguments, ensure_ascii=False)
                    }
                }
                for tc in llm_response.tool_calls
            ]

        return message
