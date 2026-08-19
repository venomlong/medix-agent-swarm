"""
LLM客户端
支持调用 OpenAI 兼容的 API（如字节跳动豆包、OpenAI、Deepseek 等）
支持 function calling
"""
import os
import sys
import asyncio
import json
from typing import List, Dict, Any, Optional, Callable, Tuple
from dataclasses import dataclass
from openai import AsyncOpenAI
from loguru import logger

# 加载项目根目录的上层目录（medix-agent-swarm 的父目录）的 config.py
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from config import LLM_CONFIG


def _usage_token_pair(usage: Any) -> Optional[Tuple[int, int]]:
    """从 response.usage / chunk.usage 取出 prompt/completion；缺失则 None。"""
    if usage is None:
        return None
    if isinstance(usage, dict):
        prompt = usage.get("prompt_tokens")
        completion = usage.get("completion_tokens")
    else:
        prompt = getattr(usage, "prompt_tokens", None)
        completion = getattr(usage, "completion_tokens", None)
    if prompt is None and completion is None:
        return None
    try:
        return int(prompt or 0), int(completion or 0)
    except (TypeError, ValueError):
        return None


def _record_response_usage(source: Any) -> None:
    """写当前 Trace + GLOBAL_USAGE。usage 为 None 时跳过，记账失败不影响主流程。"""
    if source is None:
        return
    usage = getattr(source, "usage", source)
    pair = _usage_token_pair(usage)
    if pair is None:
        return
    try:
        from core.tracing import record_llm_usage

        record_llm_usage(pair[0], pair[1])
    except Exception as exc:
        logger.debug(f"Skip llm usage accounting: {exc}")


@dataclass
class ToolCall:
    """Function call 数据结构"""
    id: str
    name: str
    arguments: Dict[str, Any]


@dataclass
class LLMResponse:
    """LLM 响应数据结构（支持 function calling）"""
    content: Optional[str]
    tool_calls: List[ToolCall]
    finish_reason: str  # "stop", "tool_calls", "length", "content_filter"

    def has_tool_calls(self) -> bool:
        """是否包含 function calls"""
        return len(self.tool_calls) > 0


class LLMClient:
    """统一的LLM客户端，支持多种模型"""

    def __init__(self, model_type: str = "openai_compatible"):
        """
        初始化LLM客户端

        Args:
            model_type: 模型类型，默认 "openai_compatible"（支持 OpenAI 兼容的 API）
        """
        self.model_type = model_type

        if model_type == "openai_compatible":
            # 使用 OpenAI 兼容的 API（通过 config.py 配置）
            self.config = LLM_CONFIG
            self.client = AsyncOpenAI(
                api_key=self.config["api_key"],
                base_url=self.config["base_url"]
            )
            self.model_name = self.config["model_name"]
            self.temperature = self.config.get("temperature", 0.7)
            self.max_tokens = self.config.get("max_tokens", 8192)
        else:
            raise ValueError(f"Unknown model type: {model_type}")

    async def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        stream: bool = False,
        on_delta: Optional[Callable[[str], None]] = None,
        **kwargs
    ) -> str:
        """
        异步聊天接口

        Args:
            messages: 消息列表，格式为 [{"role": "user", "content": "..."}]
            temperature: 温度参数（可选）
            max_tokens: 最大token数（可选）
            stream: 是否流式；失败时自动回退为非流式
            on_delta: 每个文本增量的回调（仅 stream=True 时使用）

        Returns:
            模型返回的文本
        """
        stream = bool(stream or kwargs.pop("stream", False))
        on_delta = on_delta or kwargs.pop("on_delta", None)
        # 注意：不能用 `or`，否则 temperature=0 / max_tokens=0 会被短路成默认值
        temperature = temperature if temperature is not None else self.temperature
        max_tokens = max_tokens if max_tokens is not None else self.max_tokens

        logger.debug(f"Calling LLM ({self.model_type}) with {len(messages)} messages")

        if stream:
            try:
                streamed = await self._stream_completion(
                    {
                        "model": self.model_name,
                        "messages": messages,
                        "temperature": temperature,
                        "max_tokens": max_tokens,
                        **kwargs,
                    },
                    on_delta=on_delta,
                )
                content = streamed.content or ""
                logger.debug(f"LLM stream response length: {len(content)} chars")
                return content
            except Exception as e:
                logger.warning(f"Streaming chat failed, falling back to non-stream: {e}")

        try:
            response = await self.client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                **kwargs
            )
            _record_response_usage(response)

            content = response.choices[0].message.content
            logger.debug(f"LLM response length: {len(content) if content else 0} chars")
            return content

        except Exception as e:
            logger.error(f"LLM call failed: {e}")
            raise

    async def _stream_completion(
        self,
        request_params: Dict[str, Any],
        on_delta: Optional[Callable[[str], None]] = None,
    ) -> LLMResponse:
        """消费 chat.completions 流，拼出完整 LLMResponse；content 增量可回调。"""
        # 拷贝后再加 stream_options，避免流式失败回退非流式时把该字段带进 create()
        params = dict(request_params)
        params["stream_options"] = {"include_usage": True}
        stream = await self.client.chat.completions.create(
            **params,
            stream=True,
        )
        content_parts: List[str] = []
        tool_acc: Dict[int, Dict[str, str]] = {}
        finish_reason = "stop"
        suppress_delta = False
        last_usage = None

        async for chunk in stream:
            # OpenAI：usage 在最后一个 choices 为空的 chunk 里；须在 continue 之前读
            usage = getattr(chunk, "usage", None)
            if usage is not None:
                last_usage = usage
            if not getattr(chunk, "choices", None):
                continue
            choice = chunk.choices[0]
            if getattr(choice, "finish_reason", None):
                finish_reason = choice.finish_reason
            delta = choice.delta
            tool_calls = getattr(delta, "tool_calls", None)
            if tool_calls:
                suppress_delta = True
                for tc in tool_calls:
                    idx = int(getattr(tc, "index", 0) or 0)
                    slot = tool_acc.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                    if getattr(tc, "id", None):
                        slot["id"] = tc.id
                    fn = getattr(tc, "function", None)
                    if fn is not None:
                        if getattr(fn, "name", None):
                            slot["name"] += fn.name
                        if getattr(fn, "arguments", None):
                            slot["arguments"] += fn.arguments
            piece = getattr(delta, "content", None) or ""
            if piece:
                content_parts.append(piece)
                if on_delta and not suppress_delta:
                    try:
                        on_delta(piece)
                    except Exception as cb_err:
                        logger.debug(f"on_delta error: {cb_err}")

        tool_calls_out: List[ToolCall] = []
        for idx in sorted(tool_acc):
            slot = tool_acc[idx]
            raw = slot["arguments"] or "{}"
            try:
                args = json.loads(raw)
                if not isinstance(args, dict):
                    args = {"_raw": args}
            except json.JSONDecodeError:
                args = {"_raw": raw}
            tool_calls_out.append(ToolCall(
                id=slot["id"] or f"call_{idx}",
                name=slot["name"] or "unknown",
                arguments=args,
            ))

        if tool_calls_out and finish_reason == "stop":
            finish_reason = "tool_calls"

        _record_response_usage(last_usage)
        return LLMResponse(
            content="".join(content_parts) or None,
            tool_calls=tool_calls_out,
            finish_reason=finish_reason,
        )

    async def chat_with_retry(
        self,
        messages: List[Dict[str, str]],
        max_retries: int = 3,
        **kwargs
    ) -> str:
        """
        带重试的聊天接口

        Args:
            messages: 消息列表
            max_retries: 最大重试次数

        Returns:
            模型返回的文本
        """
        for attempt in range(max_retries):
            try:
                return await self.chat(messages, **kwargs)
            except Exception as e:
                if attempt == max_retries - 1:
                    raise
                logger.warning(f"Retry {attempt + 1}/{max_retries} after error: {e}")
                await asyncio.sleep(2 ** attempt)  # 指数退避

    def create_message(self, role: str, content: str) -> Dict[str, str]:
        """
        创建消息对象

        Args:
            role: 角色，"user" 或 "assistant" 或 "system"
            content: 消息内容

        Returns:
            消息字典
        """
        return {"role": role, "content": content}

    async def chat_with_tools(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: str = "auto",
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        stream: bool = False,
        on_delta: Optional[Callable[[str], None]] = None,
        **kwargs
    ) -> LLMResponse:
        """
        带工具支持的聊天接口

        Args:
            messages: 消息列表
            tools: 工具定义列表（OpenAI format）
            tool_choice: 工具选择策略 ("auto"/"required"/"none")
            temperature: 温度参数
            max_tokens: 最大token数
            stream: 是否流式；若出现 tool_calls 则不把增量推给 on_delta
            on_delta: 文本增量回调（最终答案路径使用）

        Returns:
            LLMResponse 对象
        """
        stream = bool(stream or kwargs.pop("stream", False))
        on_delta = on_delta or kwargs.pop("on_delta", None)
        # 注意：不能用 `or`，否则 temperature=0 / max_tokens=0 会被短路成默认值
        temperature = temperature if temperature is not None else self.temperature
        max_tokens = max_tokens if max_tokens is not None else self.max_tokens

        logger.debug(f"Calling LLM with {len(tools) if tools else 0} tools")

        request_params = {
            "model": self.model_name,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            **kwargs
        }

        if tools:
            request_params["tools"] = tools
            if tool_choice != "auto":
                request_params["tool_choice"] = tool_choice

        if stream:
            try:
                streamed = await self._stream_completion(request_params, on_delta=on_delta)
                if streamed.has_tool_calls():
                    logger.debug(f"LLM streamed {len(streamed.tool_calls)} tool calls")
                return streamed
            except Exception as e:
                logger.warning(f"Streaming chat_with_tools failed, falling back: {e}")

        try:
            response = await self.client.chat.completions.create(**request_params)

            # 解析响应
            message = response.choices[0].message
            finish_reason = response.choices[0].finish_reason

            # 提取工具调用
            tool_calls = []
            if hasattr(message, 'tool_calls') and message.tool_calls:
                for tc in message.tool_calls:
                    raw_args = tc.function.arguments or "{}"
                    try:
                        parsed_args = json.loads(raw_args)
                    except json.JSONDecodeError:
                        parsed_args = {"_raw": raw_args}
                    tool_calls.append(ToolCall(
                        id=tc.id,
                        name=tc.function.name,
                        arguments=parsed_args if isinstance(parsed_args, dict) else {"_raw": parsed_args}
                    ))
                logger.debug(f"LLM requested {len(tool_calls)} tool calls")

            _record_response_usage(response)
            return LLMResponse(
                content=message.content,
                tool_calls=tool_calls,
                finish_reason=finish_reason
            )

        except Exception as e:
            logger.error(f"LLM call with tools failed: {e}")
            raise

    def create_tool_message(
        self,
        tool_call_id: str,
        tool_name: str,
        result: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        创建工具执行结果消息

        Args:
            tool_call_id: 工具调用ID
            tool_name: 工具名称
            result: 工具执行结果

        Returns:
            工具消息字典
        """
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": tool_name,
            "content": json.dumps(result, ensure_ascii=False)
        }
