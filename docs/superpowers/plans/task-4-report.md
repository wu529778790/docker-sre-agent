# Task 4: Host Access Tools — Report

## Status: DONE

## What was done

Created `server_sre_agent/tools/host.py` with two tools:

- **HostExecTool** — executes whitelisted read-only commands against the host filesystem via a temporary `alpine` container that mounts `/` as `/host` (read-only). Commands must be in `ALLOWED_HOST_COMMANDS`; unrecognized commands are rejected with the full allowlist returned.
- **HostDiskScanTool** — runs three container commands (df, top-level du, docker dir du) and returns a structured disk overview.

Modified `server_sre_agent/tools/__init__.py` to import and register both new tools in `ALL_TOOLS`.

## Files changed

| File | Action |
|------|--------|
| `server_sre_agent/tools/host.py` | Created (113 lines) |
| `server_sre_agent/tools/__init__.py` | Updated — added host tool imports and instances |

## Verification

`py_compile` passed for both files. Commit `217b090` on `main`.
