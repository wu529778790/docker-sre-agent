# Task 7: Scheduler for Periodic Tasks

## Status: DONE

## What was done

Created `server_sre_agent/scheduler.py` (55 lines).

### Components

- **Scheduler class** — manages two periodic async tasks:
  - `disk_scan` — runs every 3600s (1 hour), invokes `DockerInfoTool` + `HostDiskScanTool`, sends results to the agent via `SCAN_PROMPT`, logs the analysis.
  - `resource_check` — runs every `config.monitor.check_interval` seconds (default 300s), invokes `ContainerListTool`, logs container list at debug level.

- **Concurrency control** — `_lock` (asyncio.Lock) prevents overlapping disk scans; if a scan is already running, the next trigger is skipped.

- **Graceful lifecycle** — `start()` guards against double-start; `stop()` cancels all tasks and awaits cleanup.

- **Tool imports** — deferred inside methods to avoid circular imports.

### Verification

- `python3 -m py_compile server_sre_agent/scheduler.py` — passed.
- Committed: `afd39a9 feat: scheduler — periodic disk scan and resource check`.
