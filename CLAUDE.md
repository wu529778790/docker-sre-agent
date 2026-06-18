# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Lightweight server SRE agent for small cloud servers (2-core 2GB). Monitors Docker containers, auto-restarts failed ones, scans and cleans disk usage. Runs as a Docker container with host filesystem access via temporary containers.

## Commands

```bash
pip install -e .                          # install in editable mode
server-sre run                            # run daemon (event listener + scheduler)
server-sre web --port 6700                # run web UI
docker compose up -d                      # run via Docker
pytest                                    # run tests
pytest tests/test_foo.py                  # single test file
pytest -k "test_name"                     # by pattern
```

## Architecture

14 Python files, ~1370 lines. Event-driven + ReAct agent loop.

### Module map

| File | Purpose |
|------|---------|
| `server_sre_agent/agent.py` | ReAct loop — LLM + tool dispatch |
| `server_sre_agent/llm.py` | Claude API client with retry |
| `server_sre_agent/scanner.py` | Docker event listener + auto-restart |
| `server_sre_agent/scheduler.py` | Periodic disk scan + resource check |
| `server_sre_agent/web.py` | Flask web UI with session memory |
| `server_sre_agent/tools/docker.py` | Docker tools (info, clean, list, restart) |
| `server_sre_agent/tools/host.py` | Host tools via temporary containers |
| `server_sre_agent/config.py` | YAML config loader |

### Key flow

```
Docker events → Scanner → auto-restart (rate-limited)
Scheduler → periodic scan → LLM analysis → report
Web UI → chat → Agent → tools → response
```

### Host filesystem access

The agent runs in a Docker container but can access the host filesystem by creating temporary containers with `/` mounted read-only at `/host`. See `tools/host.py`.

### Restart strategy

1. `docker restart` (graceful, 10s timeout)
2. `docker stop` + `docker start` (forced)
3. Give up — after 3 consecutive failures, stop auto-restart

## Config

```yaml
monitor:
  exclude_containers: ["server-sre-agent"]
  check_interval: 300
restart:
  max_per_container_per_hour: 5
  max_consecutive_fails: 3
llm:
  enabled: true
  model: "claude-sonnet-4-20250514"
web:
  port: 6700
  token: "${WEB_TOKEN}"
```

## Conventions

- Python 3.9+ (use `from __future__ import annotations`)
- Build system: hatchling
- Dependencies: docker, pyyaml, anthropic, flask
- All host access via temporary Docker containers (never mount host root rw)
- Logging to stdout, format: `YYYY-MM-DD HH:MM:SS [LEVEL] name: message`
