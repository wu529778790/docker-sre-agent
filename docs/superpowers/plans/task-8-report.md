# Task 8: Web UI + Main Entry Point

## Status: DONE

## Files Created

### 1. `server_sre_agent/main.py`
Entry point with two subcommands:
- `server-sre-agent run` — runs daemon with Scanner (Docker event listener) + Scheduler (periodic tasks)
- `server-sre-agent web` — runs Flask web server for chat UI

Features:
- Signal handling (SIGINT/SIGTERM) for graceful shutdown
- `--version` flag
- `--config` / `--port` options per subcommand
- Logging setup via `setup_logging()`

### 2. `server_sre_agent/web.py`
Flask web server with:
- `GET /` — serves chat.html
- `POST /api/auth` — token authentication
- `GET /api/chat/history` — get conversation history
- `POST /api/chat` — non-streaming chat with agent
- `POST /api/chat/stream` — SSE streaming chat with tool events
- `POST /api/chat/clear` — clear session history
- `GET /api/containers` — list Docker containers
- `GET /api/logs/<name>` — fetch container logs with tail/grep/since support

Session management: per-auth-token conversation history (in-memory, max 50 messages).
Rate limiting: 10 requests/minute per IP.
No MCP endpoint (simplified for v1).

### 3. `server_sre_agent/templates/chat.html`
Chat UI with:
- Dark theme (GitHub-style)
- Token-based login
- Markdown rendering (marked.js)
- Syntax highlighting (highlight.js)
- SSE streaming with tool call/result events
- Quick action buttons
- Auto-growing textarea

## Verification
- `python3 -m py_compile server_sre_agent/main.py` — OK
- `python3 -m py_compile server_sre_agent/web.py` — OK
- Commit: `7782d09 feat: web UI + main entry point`

## Notes
- Adapted from existing `docker_sre_agent/web.py` pattern
- Config uses `config.web.token` (not `config.web_token`)
- Container logs endpoint uses docker client directly (no fetch_logs tool in server_sre_agent)
