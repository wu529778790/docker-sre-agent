"""Web chat server with SSE streaming, token auth, and session memory."""

from __future__ import annotations

import hashlib
import uuid
import json
import logging
import queue
import threading
from functools import wraps
from pathlib import Path

from flask import Flask, Response, request, send_from_directory

from docker_sre_agent.agent import Agent
from docker_sre_agent.config import AgentConfig
from docker_sre_agent.llm import LLMClient
from docker_sre_agent.tools import ALL_TOOLS
from docker_sre_agent.prompts import ASK_PROMPT

logger = logging.getLogger(__name__)

app = Flask(__name__, template_folder=str(Path(__file__).parent / "templates"))

_agent: Agent | None = None
_config: AgentConfig | None = None
_token_hash: str = ""


@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# --- Rate limiting (per-IP, sliding window) ---
import time as _time
_rate_limits: dict[str, list[float]] = {}
RATE_LIMIT = 10  # max requests per minute
RATE_WINDOW = 60  # seconds


def _check_rate_limit(ip: str) -> bool:
    """Returns True if request is allowed."""
    now = _time.time()
    timestamps = _rate_limits.setdefault(ip, [])
    # Remove old entries
    timestamps[:] = [t for t in timestamps if now - t < RATE_WINDOW]
    if len(timestamps) >= RATE_LIMIT:
        return False
    timestamps.append(now)
    return True


def require_auth(f):
    """Check Bearer token on all /api routes."""
    @wraps(f)
    def decorated(*args, **kwargs):
        # Rate limit
        if not _check_rate_limit(request.remote_addr or "unknown"):
            return {"error": "请求过于频繁，请稍后再试"}, 429

        if not _token_hash:
            return f(*args, **kwargs)

        auth = request.headers.get("Authorization", "")
        token = ""
        if auth.startswith("Bearer "):
            token = auth[7:]
        else:
            token = request.args.get("token", "")

        if not token or _hash_token(token) != _token_hash:
            return {"error": "unauthorized"}, 401
        return f(*args, **kwargs)
    return decorated


def create_app(config: AgentConfig) -> Flask:
    """Create and configure the Flask app."""
    global _agent, _config, _token_hash
    _config = config

    if config.web_token:
        _token_hash = _hash_token(config.web_token)
        logger.info("Token auth enabled")
    else:
        logger.warning("No WEB_TOKEN configured, auth disabled")

    llm = LLMClient(
        api_key=config.llm.api_key,
        base_url=config.llm.base_url,
        model=config.llm.model,
        max_tokens=config.llm.max_tokens,
        timeout=config.llm.timeout,
    )
    _agent = Agent(llm=llm, tools=ALL_TOOLS, max_rounds=config.llm.max_tool_rounds)

    return app


@app.route("/")
def index():
    return send_from_directory(str(Path(__file__).parent / "templates"), "chat.html")


@app.route("/api/auth", methods=["POST"])
def auth():
    """Verify token and return status."""
    data = request.json
    token = data.get("token", "")

    if not _token_hash:
        return {"ok": True, "msg": "no auth required"}

    token_hash = _hash_token(token)

    if token_hash == _token_hash:
        return {"ok": True}
    return {"ok": False, "error": "invalid token"}, 401


MAX_MESSAGE_LENGTH = 4000
MAX_HISTORY_MESSAGES = 50  # keep last N messages per session


# --- Conversation history (per auth token, in-memory) ---
_conversations: dict[str, list[dict]] = {}


def _get_session_key() -> str:
    """Get session key from auth token or IP."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return _hash_token(auth[7:])
    return request.remote_addr or "anonymous"


def _get_history() -> list[dict]:
    key = _get_session_key()
    return _conversations.setdefault(key, [])


def _trim_history(history: list[dict]) -> None:
    """Keep history within limits."""
    while len(history) > MAX_HISTORY_MESSAGES:
        history.pop(0)


@app.route("/api/chat/history", methods=["GET"])
@require_auth
def get_chat_history():
    """Get conversation history."""
    history = _get_history()
    # Extract readable messages (skip tool internals)
    readable = []
    for msg in history:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if role == "user":
            readable.append({"role": "user", "content": content})
        elif role == "assistant" and isinstance(content, str) and content:
            readable.append({"role": "assistant", "content": content})
    return {"messages": readable}


@app.route("/api/chat/clear", methods=["POST"])
@require_auth
def clear_chat_history():
    """Clear conversation history."""
    key = _get_session_key()
    _conversations[key] = []
    return {"ok": True}


@app.route("/api/chat", methods=["POST"])
@require_auth
def chat():
    """Non-streaming chat endpoint with conversation memory."""
    data = request.json
    message = data.get("message", "")
    if not message:
        return {"error": "empty message"}, 400
    if len(message) > MAX_MESSAGE_LENGTH:
        return {"error": f"消息过长（最多 {MAX_MESSAGE_LENGTH} 字）"}, 400

    if not _agent:
        return {"error": "agent not initialized"}, 500

    history = _get_history()

    # First message — add system context
    if not history:
        history.append({"role": "user", "content": ASK_PROMPT.format(question="")})
        history.append({"role": "assistant", "content": "好的，我是你的服务器运维助手。你可以问我任何关于服务器、Docker、日志的问题。"})

    # Add user message
    history.append({"role": "user", "content": message})
    _trim_history(history)

    # Run agent with full history
    result_messages = _agent.chat(history)

    # Update history with agent's response
    _conversations[_get_session_key()] = result_messages

    # Extract reply text
    reply = ""
    for msg in reversed(result_messages):
        if msg.get("role") == "assistant" and isinstance(msg.get("content"), str):
            reply = msg["content"]
            break

    return {"reply": reply}


@app.route("/api/chat/stream", methods=["POST"])
@require_auth
def chat_stream():
    """SSE streaming chat endpoint with conversation memory."""
    data = request.json or {}
    message = data.get("message", "")
    if not message:
        return {"error": "empty message"}, 400
    if len(message) > MAX_MESSAGE_LENGTH:
        return {"error": f"消息过长（最多 {MAX_MESSAGE_LENGTH} 字）"}, 400

    if not _agent:
        return {"error": "agent not initialized"}, 500

    history = _get_history()

    # First message — add system context
    if not history:
        history.append({"role": "user", "content": ASK_PROMPT.format(question="")})
        history.append({"role": "assistant", "content": "好的，我是你的服务器运维助手。你可以问我任何关于服务器、Docker、日志的问题。"})

    # Add user message
    history.append({"role": "user", "content": message})
    _trim_history(history)

    def generate():
        q: queue.Queue = queue.Queue()

        def on_tool_call(name, inp):
            q.put({"type": "tool_call", "name": name, "input": inp})

        def on_tool_result(name, result):
            q.put({"type": "tool_result", "name": name})

        def run_agent():
            try:
                result_messages = _agent.chat_streaming(
                    history,
                    on_tool_call=on_tool_call,
                    on_tool_result=on_tool_result,
                )
                # Update conversation history
                _conversations[_get_session_key()] = result_messages

                # Extract reply text
                reply = ""
                for msg in reversed(result_messages):
                    if msg.get("role") == "assistant" and isinstance(msg.get("content"), str):
                        reply = msg["content"]
                        break
                q.put({"type": "final", "text": reply})
            except Exception as e:
                q.put({"type": "error", "text": str(e)})
            finally:
                q.put(None)

        thread = threading.Thread(target=run_agent, daemon=True)
        thread.start()

        while True:
            event = q.get()
            if event is None:
                break
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return Response(generate(), mimetype="text/event-stream")


@app.route("/api/containers")
@require_auth
def list_containers():
    """List all containers."""
    from docker_sre_agent.docker_client import get_client
    try:
        client = get_client()
        containers = client.containers.list(all=True)
        result = []
        for c in containers:
            result.append({
                "name": c.name,
                "status": c.status,
                "image": c.image.tags[0] if c.image.tags else str(c.image.id)[:12],
            })
        return {"containers": result}
    except Exception as e:
        return {"error": str(e)}, 500


@app.route("/api/logs/<name>")
@require_auth
def get_logs(name: str):
    """Get logs for a container."""
    tail = request.args.get("tail", 100, type=int)
    grep = request.args.get("grep", "")
    since = request.args.get("since", "")

    from docker_sre_agent.tools import ALL_TOOLS
    tool = next(t for t in ALL_TOOLS if t.name == "fetch_logs")
    result = tool.execute(name=name, tail=tail, grep=grep, since=since)
    return Response(result, mimetype="application/json")


# ============================================================
# MCP HTTP Transport (Streamable HTTP)
# ============================================================

MCP_SERVER_INFO = {
    "name": "docker-sre-agent",
    "version": "0.2.0",
}

MCP_CAPABILITIES = {
    "tools": {"listChanged": False},
}

# Tools exposed via MCP (subset of ALL_TOOLS + custom ones)
_mcp_tool_map: dict[str, Any] = {}


def _get_mcp_tools() -> list[dict]:
    """Return MCP tool schemas."""
    tools = [
        {
            "name": "fetch_logs",
            "description": "获取容器日志，支持 tail 行数、grep 关键词过滤、since 时间范围",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "容器名称"},
                    "tail": {"type": "integer", "default": 100, "description": "获取最近多少行"},
                    "grep": {"type": "string", "description": "关键词过滤"},
                    "since": {"type": "string", "description": "时间范围 (30m/1h/24h)"},
                },
                "required": ["name"],
            },
        },
        {
            "name": "list_containers",
            "description": "列出所有 Docker 容器及其状态",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "container_status",
            "description": "获取指定容器的详细状态、资源使用、最近日志",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "容器名称"},
                },
                "required": ["name"],
            },
        },
        {
            "name": "server_info",
            "description": "获取服务器磁盘和 Docker 环境概览",
            "inputSchema": {"type": "object", "properties": {}},
        },
    ]
    return tools


def _execute_mcp_tool(name: str, arguments: dict) -> str:
    """Execute an MCP tool and return the result string."""
    if name == "fetch_logs":
        from docker_sre_agent.tools.logs import FetchLogsTool
        tool = FetchLogsTool()
        return tool.execute(**arguments)

    elif name == "list_containers":
        from docker_sre_agent.docker_client import get_client
        client = get_client()
        containers = client.containers.list(all=True)
        result = []
        for c in containers:
            result.append({
                "name": c.name,
                "status": c.status,
                "image": c.image.tags[0] if c.image.tags else str(c.image.id)[:12],
            })
        return json.dumps({"containers": result}, ensure_ascii=False, indent=2)

    elif name == "container_status":
        container_name = arguments.get("name", "")
        from docker_sre_agent.docker_client import get_client
        client = get_client()
        try:
            c = client.containers.get(container_name)
            attrs = c.attrs
            state = attrs.get("State", {})
            # Get last 30 lines of logs
            logs = c.logs(tail=30, timestamps=True).decode("utf-8", errors="replace")
            result = {
                "name": c.name,
                "status": state.get("Status"),
                "health": state.get("Health", {}).get("Status"),
                "oom_killed": state.get("OOMKilled", False),
                "restart_count": state.get("RestartCount", 0),
                "started_at": state.get("StartedAt"),
                "recent_logs": logs[-1000:],
            }
            return json.dumps(result, ensure_ascii=False, indent=2)
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    elif name == "server_info":
        import subprocess
        info = {}
        try:
            df = subprocess.run(["df", "-h", "/"], capture_output=True, text=True, timeout=5)
            info["disk"] = df.stdout.strip()
        except Exception:
            info["disk"] = "无法获取"
        try:
            from docker_sre_agent.docker_client import get_client
            client = get_client()
            info["docker_version"] = client.version().get("Version", "unknown")
            info["containers_total"] = len(client.containers.list(all=True))
            info["containers_running"] = len(client.containers.list(filters={"status": "running"}))
        except Exception:
            pass
        return json.dumps(info, ensure_ascii=False, indent=2)

    return json.dumps({"error": f"Unknown tool: {name}"}, ensure_ascii=False)


@app.route("/mcp", methods=["POST"])
@require_auth
def mcp_endpoint():
    """MCP Streamable HTTP endpoint — handles JSON-RPC messages."""
    data = request.json
    if not data:
        return {"error": "invalid JSON"}, 400

    method = data.get("method", "")
    msg_id = data.get("id")
    params = data.get("params", {})

    # Handle different MCP methods
    if method == "initialize":
        result = {
            "protocolVersion": "2025-03-26",
            "capabilities": MCP_CAPABILITIES,
            "serverInfo": MCP_SERVER_INFO,
        }

    elif method == "notifications/initialized":
        # Client acknowledgment, no response needed
        return Response(status=204)

    elif method == "tools/list":
        result = {"tools": _get_mcp_tools()}

    elif method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})
        try:
            tool_result = _execute_mcp_tool(tool_name, arguments)
            result = {
                "content": [{"type": "text", "text": tool_result}],
            }
        except Exception as e:
            result = {
                "content": [{"type": "text", "text": f"Error: {e}"}],
                "isError": True,
            }

    elif method == "ping":
        result = {}

    else:
        return json.dumps({
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }), 400

    # Standard JSON-RPC response
    response = {
        "jsonrpc": "2.0",
        "id": msg_id,
        "result": result,
    }
    return Response(
        json.dumps(response, ensure_ascii=False),
        mimetype="application/json",
    )


@app.route("/mcp", methods=["GET"])
def mcp_sse():
    """MCP SSE endpoint — for server-initiated messages (not used yet)."""
    def generate():
        yield "event: endpoint\ndata: /mcp\n\n"
        # Keep alive
        while True:
            yield "event: ping\ndata: {}\n\n"
            _time.sleep(30)

    return Response(generate(), mimetype="text/event-stream")


@app.route("/mcp", methods=["DELETE"])
def mcp_delete():
    """MCP session termination."""
    return Response(status=200)
