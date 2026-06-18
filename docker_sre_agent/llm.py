"""Claude API wrapper with tool_use support and retry logic."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import anthropic

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds


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
    input_tokens: int = 0
    output_tokens: int = 0


class LLMClient:
    """Wraps the Anthropic Claude API for agent use."""

    def __init__(self, api_key: str, base_url: str = "", model: str = "claude-sonnet-4-20250514",
                 max_tokens: int = 4096, timeout: int = 120) -> None:
        kwargs: dict[str, Any] = {"api_key": api_key, "timeout": timeout}
        if base_url:
            kwargs["base_url"] = base_url
        self.client = anthropic.Anthropic(**kwargs)
        self.model = model
        self.max_tokens = max_tokens

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        system: str | None = None,
    ) -> AgentResponse:
        """Send a message to Claude with automatic retry on transient errors."""
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = tools

        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                response = self.client.messages.create(**kwargs)

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
                    input_tokens=response.usage.input_tokens,
                    output_tokens=response.usage.output_tokens,
                )

            except anthropic.RateLimitError as e:
                last_error = e
                wait = RETRY_DELAY * (2 ** attempt)
                logger.warning(f"Rate limited, retrying in {wait}s (attempt {attempt + 1}/{MAX_RETRIES})")
                time.sleep(wait)
            except anthropic.APIConnectionError as e:
                last_error = e
                wait = RETRY_DELAY * (2 ** attempt)
                logger.warning(f"API connection error, retrying in {wait}s: {e}")
                time.sleep(wait)
            except anthropic.APITimeoutError as e:
                last_error = e
                wait = RETRY_DELAY * (2 ** attempt)
                logger.warning(f"API timeout, retrying in {wait}s: {e}")
                time.sleep(wait)

        logger.error(f"LLM call failed after {MAX_RETRIES} retries: {last_error}")
        return AgentResponse(
            text=f"LLM 调用失败: {last_error}",
            tool_calls=[],
            stop_reason="error",
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
