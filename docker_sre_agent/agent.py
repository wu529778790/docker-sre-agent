"""ReAct agent loop with tool dispatch."""

from __future__ import annotations

import json
import logging
from typing import Any

from docker_sre_agent.llm import LLMClient
from docker_sre_agent.tools.base import Tool
from docker_sre_agent.prompts import SYSTEM_PROMPT

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 10


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

    def _execute_tool(self, name: str, input_args: dict) -> str:
        """Execute a tool by name and return the result string."""
        tool = self._tool_map.get(name)
        if not tool:
            return json.dumps({"error": f"未知工具: {name}"}, ensure_ascii=False)

        try:
            return tool.execute(**input_args)
        except Exception as e:
            logger.exception(f"Tool '{name}' failed")
            return json.dumps({"error": f"工具执行失败: {e}"}, ensure_ascii=False)

    def run(self, user_message: str, system_prompt: str | None = None) -> str:
        """Run the agent loop and return the final response text.

        Args:
            user_message: The user's question or scan data.
            system_prompt: Optional override for system prompt.

        Returns:
            The agent's final text response.
        """
        messages: list[dict] = [{"role": "user", "content": user_message}]
        prompt = system_prompt or self.system_prompt

        for round_num in range(self.max_rounds):
            logger.debug(f"Agent round {round_num + 1}/{self.max_rounds}")

            response = self.llm.chat(
                messages=messages,
                tools=self._get_tool_schemas(),
                system=prompt,
            )

            # If no tool calls, we're done
            if not response.tool_calls:
                return response.text or ""

            # Add assistant message with tool calls
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

            # Execute tools and add results
            for tc in response.tool_calls:
                logger.info(f"Calling tool: {tc.name}({tc.input})")
                result = self._execute_tool(tc.name, tc.input)
                logger.debug(f"Tool result: {result[:200]}")
                messages.append(self.llm.make_tool_result(tc.id, result))

        # Max rounds reached
        logger.warning(f"Agent reached max rounds ({self.max_rounds})")
        return "达到最大工具调用次数，分析未完成。请尝试简化问题后重试。"

    def run_streaming(
        self, user_message: str, system_prompt: str | None = None,
        on_tool_call: Any = None, on_tool_result: Any = None,
    ) -> str:
        """Run with callbacks for streaming progress updates.

        Args:
            on_tool_call: Called with (tool_name, tool_input) before execution.
            on_tool_result: Called with (tool_name, result) after execution.
        """
        messages: list[dict] = [{"role": "user", "content": user_message}]
        prompt = system_prompt or self.system_prompt

        for round_num in range(self.max_rounds):
            response = self.llm.chat(
                messages=messages,
                tools=self._get_tool_schemas(),
                system=prompt,
            )

            if not response.tool_calls:
                return response.text or ""

            # Assistant message
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

            # Execute tools
            for tc in response.tool_calls:
                if on_tool_call:
                    on_tool_call(tc.name, tc.input)
                result = self._execute_tool(tc.name, tc.input)
                if on_tool_result:
                    on_tool_result(tc.name, result)
                messages.append(self.llm.make_tool_result(tc.id, result))

        return "达到最大工具调用次数，分析未完成。"
