# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Minimal Docker monitoring agent — listens to Docker events, detects container failures (die, oom, unhealthy), and auto-restarts them with rate limiting.

## Commands

```bash
pip install -e .                    # install in editable mode
docker-sre --config config.yaml     # run the agent
docker compose up -d                # run via Docker
```

### Tests
```bash
pytest                              # run all tests
pytest tests/test_foo.py            # single file
pytest -k "test_name"               # by pattern
```

## Architecture

3 files, ~230 lines of code. Event-driven — uses `docker.events()` stream, not polling.

### Module map

| File | Purpose |
|------|---------|
| `docker_sre_agent/agent.py` | Core: event listener, restart handler (3-level escalation), sliding window rate limiter |
| `docker_sre_agent/config.py` | YAML config loader with dataclass defaults |
| `docker_sre_agent/main.py` | Entry point + signal handling |

### Key flow

```
Docker events stream → _handle_event() → _is_trigger_event() → rate limit check → _restart()
```

### Restart strategy

1. `docker restart` (graceful, 10s timeout)
2. `docker stop` + `docker start` (forced)
3. Give up — after 3 consecutive failures, stop auto-restart for that container

### Rate limiting

Two-level sliding window (deque of timestamps, 1-hour window):
- Per-container: max 5 restarts/hour
- Global: max 20 restarts/hour

### Config (`config.yaml`)

```yaml
monitor:
  exclude_containers: ["docker-sre-agent"]  # self-exclusion
  watch_containers: []                       # empty = all
restart:
  max_per_container_per_hour: 5
  max_global_per_hour: 20
  timeout: 10
  max_consecutive_fails: 3
```

## Conventions

- Python 3.11+ target (uses `from __future__ import annotations` for 3.9 compat)
- Build system: hatchling (`pyproject.toml`)
- Dependencies: only `docker` + `pyyaml`
- Process management: delegated to systemd / docker compose `restart: unless-stopped`
- Logging to stdout, format: `YYYY-MM-DD HH:MM:SS [LEVEL] name: message`
