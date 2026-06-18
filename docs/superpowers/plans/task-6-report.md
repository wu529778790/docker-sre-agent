# Task 6: Docker Event Scanner — Report

## Status: Complete

## What Was Done

Created `server_sre_agent/scanner.py` — a Docker event listener with auto-restart and rate limiting.

### File Created
- `/Users/mac/github/docker-sre-agent/server_sre_agent/scanner.py` (147 lines)

### Key Components

**ContainerState** — Per-container tracking dataclass:
- `restart_timestamps`: deque of restart times (sliding window)
- `consecutive_fails`: counter for escalation logic
- `stopped`: flag to disable auto-restart after max failures

**Scanner** — Main event listener class:
- `__init__()`: Initializes with config, Docker client, per-container and global state
- `_should_monitor()`: Checks exclude list from config
- `_cleanup_window()`: Prunes timestamps outside 1-hour window
- `_is_rate_limited()`: Two-level rate limiting (per-container + global)
- `_restart()`: 2-level escalation (restart → stop+start)
- `_handle_event()`: Processes die/oom/unhealthy events
- `_cleanup_old_states()`: Removes stale container states (>24h old)
- `_event_loop()`: Blocking event stream with auto-reconnect
- `start()`/`stop()`: Async interface via `asyncio.to_thread()`

### Design Decisions

1. **Event-driven**: Uses `client.events(decode=True)` stream, not polling
2. **Two-level rate limiting**: Per-container (5/hour) + global (20/hour) sliding windows
3. **Escalation strategy**: `docker restart` → `docker stop` + `docker start` → give up
4. **Self-healing**: Auto-reconnects on event stream errors (5s delay)
5. **Memory management**: Cleans up container states older than 24 hours
6. **Async wrapper**: `start()` runs blocking loop in thread pool

### Verification
- `python3 -m py_compile server_sre_agent/scanner.py` — passed
- Committed: `6403296 feat: Docker event scanner with auto-restart and rate limiting`
