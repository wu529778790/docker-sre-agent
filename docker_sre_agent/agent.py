"""ReAct agent loop with tool dispatch and conversation memory."""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

from docker_sre_agent.llm import LLMClient
from docker_sre_agent.tools.base import Tool
from docker_sre_agent.prompts import SYSTEM_PROMPT

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 10
MAX_MESSAGE_CHARS = 80000  # ~20K tokens, leave room for response


class Agent:
    """ReAct agent that uses LLM + tools to analyze and act."""

    def __init__(
        self,
        llm: LLMClient,
        tools: list[Tool],
        system_prompt: str = SYSTEM_PROMPT,
        max_rounds: int = MAX_TOOL_ROUNDS,
    ) -> None:
        self.llm = llm
        self.tools = tools
        self.system_prompt = system_prompt
        self.max_rounds = max_rounds
        self._tool_map = {t.name: t for t in tools}

    def _get_tool_schemas(self) -> list[dict]:
        return [t.to_schema() for t in self.tools]

    def _truncate_tools_if_needed(self, messages: list[dict]) -> list[dict]:
        """Truncate old tool results if messages are too large."""
        total = sum(len(json.dumps(m, ensure_ascii=False)) for m in messages)
        if total <= MAX_MESSAGE_CHARS:
            return messages

        for msg in messages:
            if msg.get("role") == "user" and isinstance(msg.get("content"), list):
                for block in msg["content"]:
                    if block.get("type") == "tool_result" and len(block.get("content", "")) > 2000:
                        block["content"] = block["content"][:2000] + "\n... [truncated]"

        logger.debug(f"Truncated tool results, estimated size: {total}")
        return messages

    def _execute_tool(self, name: str, input_args: dict) -> tuple[str, bool]:
        """Execute a tool. Returns (result, is_error)."""
        tool = self._tool_map.get(name)
        if not tool:
            return json.dumps({"error": f"未知工具: {name}"}, ensure_ascii=False), True

        try:
            return tool.execute(**input_args), False
        except Exception as e:
            logger.exception(f"Tool '{name}' failed")
            return json.dumps({"error": f"工具执行失败: {e}"}, ensure_ascii=False), True

    def _run_loop(
        self,
        messages: list[dict],
        system_prompt: str | None,
        on_tool_call: Callable | None,
        on_tool_result: Callable | None,
    ) -> list[dict]:
        """Core ReAct loop. Takes messages, returns updated messages (with assistant response appended)."""
        prompt = system_prompt or self.system_prompt
        total_input_tokens = 0
        total_output_tokens = 0

        for round_num in range(self.max_rounds):
            messages = self._truncate_tools_if_needed(messages)

            response = self.llm.chat(
                messages=messages,
                tools=self._get_tool_schemas(),
                system=prompt,
            )

            total_input_tokens += response.input_tokens
            total_output_tokens += response.output_tokens

            if not response.tool_calls:
                logger.info(
                    f"Agent done in {round_num + 1} rounds, "
                    f"tokens: in={total_input_tokens} out={total_output_tokens}"
                )
                # Append final assistant response
                messages.append({"role": "assistant", "content": response.text or ""})
                return messages

            # Build assistant message with tool calls
            assistant_content = []
            if response.text:
                assistant_content.append({"type": "text", "text": response.text})
            for tc in response.tool_calls:
                assistant_content.append({
                    "type": "tool_use",
                    "id": tc.id,
                    "name": tc.name,
                    "input": tc.input,
                })
            messages.append({"role": "assistant", "content": assistant_content})

            # Execute tools and append results
            for tc in response.tool_calls:
                if on_tool_call:
                    on_tool_call(tc.name, tc.input)

                result, is_error = self._execute_tool(tc.name, tc.input)

                if on_tool_result:
                    on_tool_result(tc.name, result)

                messages.append(self.llm.make_tool_result(tc.id, result, is_error=is_error))

        logger.warning(f"Agent reached max rounds ({self.max_rounds})")
        messages.append({"role": "assistant", "content": "达到最大工具调用次数，分析未完成。请尝试简化问题后重试。"})
        return messages

    def chat(self, messages: list[dict], system_prompt: str | None = None) -> list[dict]:
        """Chat with conversation history. Returns updated messages list."""
        return self._run_loop(list(messages), system_prompt, None, None)

    def chat_streaming(
        self, messages: list[dict], system_prompt: str | None = None,
        on_tool_call: Callable | None = None, on_tool_result: Callable | None = None,
    ) -> list[dict]:
        """Chat with conversation history and streaming callbacks."""
        return self._run_loop(list(messages), system_prompt, on_tool_call, on_tool_result)

    # Legacy API — single message, no history
    def run(self, user_message: str, system_prompt: str | None = None) -> str:
        """Run the agent loop and return the final response text."""
        messages = [{"role": "user", "content": user_message}]
        result = self._run_loop(messages, system_prompt, None, None)
        # Extract last assistant message text
        for msg in reversed(result):
            if msg.get("role") == "assistant":
                content = msg.get("content", "")
                if isinstance(content, str):
                    return content
        return ""

    def run_streaming(
        self, user_message: str, system_prompt: str | None = None,
        on_tool_call: Callable | None = None, on_tool_result: Callable | None = None,
    ) -> str:
        """Run with callbacks for streaming progress updates."""
        messages = [{"role": "user", "content": user_message}]
        result = self._run_loop(messages, system_prompt, on_tool_call, on_tool_result)
        for msg in reversed(result):
            if msg.get("role") == "assistant":
                content = msg.get("content", "")
                if isinstance(content, str):
                    return content
        return ""
