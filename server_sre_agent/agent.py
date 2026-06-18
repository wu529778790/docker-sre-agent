"""ReAct agent loop."""

from __future__ import annotations

import copy
import json
import logging
from typing import Any, Callable

from server_sre_agent.llm import LLMClient
from server_sre_agent.tools.base import Tool
from server_sre_agent.prompts import SYSTEM_PROMPT

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 10
MAX_MESSAGE_CHARS = 80000


class Agent:
    def __init__(self, llm: LLMClient, tools: list[Tool],
                 system_prompt: str = SYSTEM_PROMPT, max_rounds: int = MAX_TOOL_ROUNDS) -> None:
        self.llm = llm
        self.tools = tools
        self.system_prompt = system_prompt
        self.max_rounds = max_rounds
        self._tool_map = {t.name: t for t in tools}

    def _get_tool_schemas(self) -> list[dict]:
        return [t.to_schema() for t in self.tools]

    def _truncate_tools_if_needed(self, messages: list[dict]) -> list[dict]:
        """Truncate large tool results in-place on a deep copy."""
        total = sum(len(json.dumps(m, ensure_ascii=False)) for m in messages)
        if total <= MAX_MESSAGE_CHARS:
            return messages
        for msg in messages:
            content = msg.get("content")
            if msg.get("role") in ("user", "tool") and isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        c = block.get("content", "")
                        if isinstance(c, str) and len(c) > 2000:
                            block["content"] = c[:2000] + "\n... [truncated]"
            elif isinstance(content, str) and len(content) > 4000:
                msg["content"] = content[:4000] + "\n... [truncated]"
        return messages

    def _execute_tool(self, name: str, input_args: dict) -> tuple[str, bool]:
        tool = self._tool_map.get(name)
        if not tool:
            return json.dumps({"error": f"未知工具: {name}"}), True
        # Warn on destructive tools
        if tool.is_destructive:
            logger.warning(f"Destructive tool called: {name}")
        try:
            return tool.execute(**input_args), False
        except Exception as e:
            logger.exception(f"Tool '{name}' failed")
            return json.dumps({"error": str(e)}), True

    def _run_loop(self, messages: list[dict], system_prompt: str | None,
                  on_tool_call: Callable | None, on_tool_result: Callable | None) -> list[dict]:
        # Deep copy to avoid mutating caller's data
        messages = copy.deepcopy(messages)
        prompt = system_prompt or self.system_prompt

        for round_num in range(self.max_rounds):
            messages = self._truncate_tools_if_needed(messages)
            response = self.llm.chat(messages=messages, tools=self._get_tool_schemas(), system=prompt)

            if response.stop_reason == "error":
                # Don't inject error into conversation history
                return messages

            if not response.tool_calls:
                messages.append({"role": "assistant", "content": response.text or ""})
                return messages

            # Build assistant message with tool calls
            assistant_content = []
            if response.text:
                assistant_content.append({"type": "text", "text": response.text})
            for tc in response.tool_calls:
                assistant_content.append({"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.input})
            messages.append({"role": "assistant", "content": assistant_content})

            # Execute tools
            for tc in response.tool_calls:
                if on_tool_call:
                    on_tool_call(tc.name, tc.input)
                result, is_error = self._execute_tool(tc.name, tc.input)
                if on_tool_result:
                    on_tool_result(tc.name, result)
                messages.append(self.llm.make_tool_result(tc.id, result, is_error=is_error))

        logger.warning(f"Agent reached max rounds ({self.max_rounds})")
        return messages

    def chat(self, messages: list[dict], system_prompt: str | None = None) -> list[dict]:
        return self._run_loop(messages, system_prompt, None, None)

    def chat_streaming(self, messages: list[dict], system_prompt: str | None = None,
                       on_tool_call: Callable | None = None, on_tool_result: Callable | None = None) -> list[dict]:
        return self._run_loop(messages, system_prompt, on_tool_call, on_tool_result)
