"""Claude API wrapper with retry logic."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import anthropic

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_DELAY = 2

# Transient errors that should be retried
RETRYABLE_ERRORS = (
    anthropic.RateLimitError,
    anthropic.APIConnectionError,
    anthropic.APITimeoutError,
    anthropic.InternalServerError,  # 5xx
    anthropic.APIStatusError,       # covers overloaded (529) etc.
)


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class AgentResponse:
    text: str | None
    tool_calls: list[ToolCall]
    stop_reason: str
    input_tokens: int = 0
    output_tokens: int = 0


class LLMClient:
    def __init__(self, api_key: str, base_url: str = "", model: str = "claude-sonnet-4-20250514",
                 max_tokens: int = 4096, timeout: int = 120) -> None:
        kwargs: dict[str, Any] = {"api_key": api_key, "timeout": timeout}
        if base_url:
            kwargs["base_url"] = base_url
        self.client = anthropic.Anthropic(**kwargs)
        self.model = model
        self.max_tokens = max_tokens
        self._total_input_tokens = 0
        self._total_output_tokens = 0

    def chat(self, messages: list[dict], tools: list[dict] | None = None,
             system: str | None = None) -> AgentResponse:
        kwargs: dict[str, Any] = {
            "model": self.model, "max_tokens": self.max_tokens, "messages": messages,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = tools

        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                response = self.client.messages.create(**kwargs)
                tool_calls, text_parts = [], []
                for block in response.content:
                    if block.type == "tool_use":
                        tool_calls.append(ToolCall(id=block.id, name=block.name, input=block.input))
                    elif block.type == "text":
                        text_parts.append(block.text)

                # Track token usage
                self._total_input_tokens += response.usage.input_tokens
                self._total_output_tokens += response.usage.output_tokens
                logger.debug(
                    f"LLM tokens: in={response.usage.input_tokens} out={response.usage.output_tokens} "
                    f"(total: in={self._total_input_tokens} out={self._total_output_tokens})"
                )

                return AgentResponse(
                    text="\n".join(text_parts) if text_parts else None,
                    tool_calls=tool_calls, stop_reason=response.stop_reason,
                    input_tokens=response.usage.input_tokens, output_tokens=response.usage.output_tokens,
                )
            except RETRYABLE_ERRORS as e:
                last_error = e
                wait = RETRY_DELAY * (2 ** attempt)
                logger.warning(f"LLM error (attempt {attempt+1}/{MAX_RETRIES}), retrying in {wait}s: {e}")
                time.sleep(wait)
            except Exception as e:
                # Non-retryable error — return error response, don't crash
                logger.error(f"LLM non-retryable error: {e}")
                return AgentResponse(text=None, tool_calls=[], stop_reason="error")

        # All retries exhausted — return error, don't fake an assistant message
        logger.error(f"LLM failed after {MAX_RETRIES} retries: {last_error}")
        return AgentResponse(text=None, tool_calls=[], stop_reason="error")

    def get_token_usage(self) -> dict[str, int]:
        return {"input": self._total_input_tokens, "output": self._total_output_tokens}

    def make_tool_result(self, tool_call_id: str, content: str, is_error: bool = False) -> dict:
        return {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": tool_call_id, "content": content, "is_error": is_error}],
        }
