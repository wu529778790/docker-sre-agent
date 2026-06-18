"""Claude API wrapper with tool_use support."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import anthropic

logger = logging.getLogger(__name__)


@dataclass
class ToolCall:
    """A tool call from the LLM."""
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class AgentResponse:
    """Response from the LLM agent loop."""
    text: str | None
    tool_calls: list[ToolCall]
    stop_reason: str  # "end_turn" or "tool_use"


class LLMClient:
    """Wraps the Anthropic Claude API for agent use."""

    def __init__(self, api_key: str, base_url: str = "", model: str = "claude-sonnet-4-20250514") -> None:
        kwargs = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self.client = anthropic.Anthropic(**kwargs)
        self.model = model

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        system: str | None = None,
    ) -> AgentResponse:
        """Send a message to Claude and get a response."""
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": 4096,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = tools

        response = self.client.messages.create(**kwargs)

        # Parse tool calls from response
        tool_calls = []
        text_parts = []
        for block in response.content:
            if block.type == "tool_use":
                tool_calls.append(ToolCall(
                    id=block.id,
                    name=block.name,
                    input=block.input,
                ))
            elif block.type == "text":
                text_parts.append(block.text)

        return AgentResponse(
            text="\n".join(text_parts) if text_parts else None,
            tool_calls=tool_calls,
            stop_reason=response.stop_reason,
        )

    def make_tool_result(self, tool_call_id: str, content: str, is_error: bool = False) -> dict:
        """Create a tool result message for the conversation."""
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_call_id,
                    "content": content,
                    "is_error": is_error,
                }
            ],
        }
