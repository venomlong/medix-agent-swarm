"""pytest 共享夹具：FakeLLMClient 不打真实 API、不读 API key。"""
from __future__ import annotations

import copy
import json
from typing import Any, Dict, List, Optional, Sequence, Union

import pytest

from core.llm_client import LLMResponse, ToolCall

Reply = Union[LLMResponse, str, BaseException]


class FakeLLMClient:
    """预设响应队列。chat / chat_with_tools 依次弹出，并记录每次收到的 messages。"""

    def __init__(self, responses: Optional[Sequence[Reply]] = None):
        self._queue: List[Reply] = list(responses or [])
        self.calls: List[Dict[str, Any]] = []

    def enqueue(self, *items: Reply) -> None:
        self._queue.extend(items)

    def remaining(self) -> int:
        return len(self._queue)

    def _snapshot_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        # deepcopy：AgentLoop 会原地追加，断言必须用调用瞬间的快照
        return copy.deepcopy(list(messages))

    def _pop(self) -> Reply:
        if not self._queue:
            raise RuntimeError("FakeLLMClient has no remaining replies")
        return self._queue.pop(0)

    def _record(self, method: str, messages: List[Dict[str, Any]], **extra: Any) -> None:
        snap = self._snapshot_messages(messages)
        self.calls.append(
            {
                "method": method,
                "messages": snap,
                "message_count": len(messages),
                **extra,
            }
        )

    def _as_text(self, item: Reply) -> str:
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, LLMResponse):
            return item.content or ""
        return str(item)

    def _as_tools_response(self, item: Reply) -> LLMResponse:
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, LLMResponse):
            return item
        return LLMResponse(content=str(item), tool_calls=[], finish_reason="stop")

    async def chat(self, messages, **kwargs) -> str:
        self._record("chat", messages, kwargs=dict(kwargs))
        return self._as_text(self._pop())

    async def chat_with_tools(
        self,
        messages,
        tools=None,
        tool_choice="auto",
        temperature=None,
        max_tokens=None,
        stream=False,
        on_delta=None,
        **kwargs,
    ) -> LLMResponse:
        self._record(
            "chat_with_tools",
            messages,
            tools=tools,
            tool_choice=tool_choice,
            temperature=temperature,
            stream=bool(stream),
            kwargs=dict(kwargs),
        )
        return self._as_tools_response(self._pop())

    def create_tool_message(self, tool_call_id, tool_name, result):
        if isinstance(result, str):
            content = result
        else:
            content = json.dumps(result, ensure_ascii=False)
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": tool_name,
            "content": content,
        }

    @staticmethod
    def text(content: str) -> LLMResponse:
        return LLMResponse(content=content, tool_calls=[], finish_reason="stop")

    @staticmethod
    def tool_calls(
        name: str = "search_knowledge",
        call_id: str = "call-1",
        **arguments: Any,
    ) -> LLMResponse:
        if not arguments:
            arguments = {"query": "感冒"}
        return LLMResponse(
            content=None,
            tool_calls=[ToolCall(id=call_id, name=name, arguments=arguments)],
            finish_reason="tool_calls",
        )


@pytest.fixture
def fake_llm_client() -> FakeLLMClient:
    return FakeLLMClient()
