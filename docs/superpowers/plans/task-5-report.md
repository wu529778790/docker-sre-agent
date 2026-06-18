# Task 5: LLM Client + Agent Loop

## Status: COMPLETED

## Files Created

| File | Purpose |
|------|---------|
| `server_sre_agent/llm.py` | Claude API wrapper with retry logic |
| `server_sre_agent/prompts.py` | System prompts for SRE tasks |
| `server_sre_agent/agent.py` | ReAct agent loop implementation |

## Implementation Details

### llm.py
- `ToolCall` dataclass: holds tool call id, name, and input
- `AgentResponse` dataclass: text, tool_calls, stop_reason, token counts
- `LLMClient` class:
  - Configurable model, max_tokens, timeout, base_url
  - `chat()` method with retry logic (3 retries, exponential backoff)
  - Handles RateLimitError, APIConnectionError, APITimeoutError
  - `make_tool_result()` helper for tool result messages

### prompts.py
- `SYSTEM_PROMPT`: SRE expert role with safety rules and output format
- `ASK_PROMPT`: Template for user questions
- `SCAN_PROMPT`: Template for scan result analysis

### agent.py
- `Agent` class with ReAct loop:
  - `_get_tool_schemas()`: converts tools to API format
  - `_truncate_tools_if_needed()`: prevents message size overflow (80K chars)
  - `_execute_tool()`: safe tool execution with error handling
  - `_run_loop()`: main agent loop (max 10 rounds)
  - `chat()`: simple interface
  - `chat_streaming()`: interface with tool call/result callbacks

## Verification

All files compile successfully:
```
python3 -m py_compile server_sre_agent/llm.py
python3 -m py_compile server_sre_agent/prompts.py
python3 -m py_compile server_sre_agent/agent.py
```

## Commit

```
d8d60fb feat: LLM client + ReAct agent loop
```

## Dependencies

- `anthropic` package (required for Claude API)
- Integrates with existing `Tool` base class from Task 1-4
