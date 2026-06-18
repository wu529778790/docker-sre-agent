# Task 2 Report: Docker Client + Tool Base

## Status: Complete

## Files Created

1. `server_sre_agent/docker_client.py` — Shared Docker client singleton using `docker.from_env()`. Provides `get_client()` and `close()` functions with lazy initialization.

2. `server_sre_agent/tools/__init__.py` — Package init exporting `Tool` base class and `ALL_TOOLS` registry list.

3. `server_sre_agent/tools/base.py` — Abstract `Tool` base class with:
   - `name`, `description`, `input_schema` abstract properties
   - `is_destructive` property (defaults to `False`)
   - `execute(**kwargs)` abstract method
   - `to_schema()` helper for serialization

## Verification

All three files pass `python3 -m py_compile` without errors.

## Commit

```
4f36243 feat: Docker client wrapper + tool base class
```
