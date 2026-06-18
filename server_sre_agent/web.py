"""Web chat server with SSE streaming, token auth, and session memory."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import queue
import threading
import time as _time
from functools import wraps
from pathlib import Path

from flask import Flask, Response, request, send_from_directory

from server_sre_agent.agent import Agent
from server_sre_agent.config import AgentConfig
from server_sre_agent.llm import LLMClient
from server_sre_agent.tools import ALL_TOOLS
from server_sre_agent.prompts import ASK_PROMPT

logger = logging.getLogger(__name__)

app = Flask(__name__, template_folder=str(Path(__file__).parent / "templates"))
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024  # 100KB max request

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


def _verify_token(token: str) -> bool:
    """Constant-time token comparison."""
    return hmac.compare_digest(_hash_token(token), _token_hash)


# --- Rate limiting (per-IP, sliding window) ---
_rate_limits: dict[str, list[float]] = {}
RATE_LIMIT = 10
RATE_WINDOW = 60


def _check_rate_limit(ip: str) -> bool:
    now = _time.time()
    timestamps = _rate_limits.setdefault(ip, [])
    timestamps[:] = [t for t in timestamps if now - t < RATE_WINDOW]
    if len(timestamps) >= RATE_LIMIT:
        return False
    timestamps.append(now)
    return True


# --- Session management with eviction ---
_sessions: dict[str, list[dict]] = {}
_session_last_access: dict[str, float] = {}
SESSION_MAX_AGE = 3600  # 1 hour
MAX_SESSIONS = 100


def _evict_sessions() -> None:
    """Remove stale sessions."""
    now = _time.time()
    stale = [k for k, t in _session_last_access.items() if now - t > SESSION_MAX_AGE]
    for k in stale:
        _sessions.pop(k, None)
        _session_last_access.pop(k, None)
    # If still too many, remove oldest
    if len(_sessions) > MAX_SESSIONS:
        sorted_keys = sorted(_session_last_access, key=_session_last_access.get)
        for k in sorted_keys[:len(_sessions) - MAX_SESSIONS]:
            _sessions.pop(k, None)
            _session_last_access.pop(k, None)


def _get_session(key: str) -> list[dict]:
    _session_last_access[key] = _time.time()
    if key not in _sessions:
        _sessions[key] = []
    return _sessions[key]


def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not _check_rate_limit(request.remote_addr or "unknown"):
            return {"error": "请求过于频繁"}, 429
        if not _token_hash:
            return f(*args, **kwargs)
        auth = request.headers.get("Authorization", "")
        token = auth[7:] if auth.startswith("Bearer ") else request.args.get("token", "")
        if not token or not _verify_token(token):
            return {"error": "unauthorized"}, 401
        return f(*args, **kwargs)
    return decorated


def create_app(config: AgentConfig) -> Flask:
    global _agent, _config, _token_hash
    _config = config

    if config.web.token:
        _token_hash = _hash_token(config.web.token)
        logger.info("Token auth enabled")
    else:
        logger.warning("No WEB_TOKEN configured, auth disabled")

    llm = LLMClient(
        api_key=config.llm.api_key, base_url=config.llm.base_url,
        model=config.llm.model, max_tokens=config.llm.max_tokens, timeout=config.llm.timeout,
    )
    _agent = Agent(llm=llm, tools=ALL_TOOLS, max_rounds=config.llm.max_tool_rounds)
    return app


def _get_session_key() -> str:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return _hash_token(auth[7:])
    return request.remote_addr or "anonymous"


MAX_MESSAGE_LENGTH = 4000
MAX_HISTORY_MESSAGES = 50


@app.route("/")
def index():
    return send_from_directory(str(Path(__file__).parent / "templates"), "chat.html")


@app.route("/api/auth", methods=["POST"])
def auth():
    data = request.json
    if not data:
        return {"error": "invalid JSON"}, 400
    token = data.get("token", "")
    if not _token_hash:
        return {"ok": True, "msg": "no auth required"}
    if _verify_token(token):
        return {"ok": True}
    return {"ok": False, "error": "invalid token"}, 401


@app.route("/api/chat/history", methods=["GET"])
@require_auth
def get_chat_history():
    _evict_sessions()
    history = _get_session(_get_session_key())
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
    key = _get_session_key()
    _sessions[key] = []
    return {"ok": True}


@app.route("/api/chat", methods=["POST"])
@require_auth
def chat():
    data = request.json
    if not data:
        return {"error": "invalid JSON"}, 400
    message = data.get("message", "")
    if not message or not isinstance(message, str):
        return {"error": "empty message"}, 400
    if len(message) > MAX_MESSAGE_LENGTH:
        return {"error": f"消息过长（最多 {MAX_MESSAGE_LENGTH} 字）"}, 400
    if not _agent:
        return {"error": "agent not initialized"}, 500

    session_key = _get_session_key()
    history = _get_session(session_key)

    if not history:
        history.append({"role": "user", "content": ASK_PROMPT.format(question="")})
        history.append({"role": "assistant", "content": "好的，我是你的服务器运维助手。"})

    history.append({"role": "user", "content": message})
    while len(history) > MAX_HISTORY_MESSAGES:
        history.pop(0)

    result_messages = _agent.chat(history)
    _sessions[session_key] = result_messages
    _session_last_access[session_key] = _time.time()

    reply = ""
    for msg in reversed(result_messages):
        if msg.get("role") == "assistant" and isinstance(msg.get("content"), str):
            reply = msg["content"]
            break
    return {"reply": reply}


@app.route("/api/chat/stream", methods=["POST"])
@require_auth
def chat_stream():
    data = request.json
    if not data:
        return {"error": "invalid JSON"}, 400
    message = data.get("message", "")
    if not message or not isinstance(message, str):
        return {"error": "empty message"}, 400
    if len(message) > MAX_MESSAGE_LENGTH:
        return {"error": f"消息过长（最多 {MAX_MESSAGE_LENGTH} 字）"}, 400
    if not _agent:
        return {"error": "agent not initialized"}, 500

    session_key = _get_session_key()
    history = _get_session(session_key)

    if not history:
        history.append({"role": "user", "content": ASK_PROMPT.format(question="")})
        history.append({"role": "assistant", "content": "好的，我是你的服务器运维助手。"})

    history.append({"role": "user", "content": message})
    while len(history) > MAX_HISTORY_MESSAGES:
        history.pop(0)

    # Deep copy history for the thread to avoid race conditions
    import copy
    history_copy = copy.deepcopy(history)

    def generate():
        q: queue.Queue = queue.Queue()

        def on_tool_call(name, inp):
            q.put({"type": "tool_call", "name": name, "input": inp})

        def on_tool_result(name, result):
            q.put({"type": "tool_result", "name": name})

        def run_agent():
            try:
                result_messages = _agent.chat_streaming(
                    history_copy,
                    on_tool_call=on_tool_call,
                    on_tool_result=on_tool_result,
                )
                _sessions[session_key] = result_messages
                _session_last_access[session_key] = _time.time()

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
    from server_sre_agent.docker_client import get_client
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
    tail = request.args.get("tail", 100, type=int)
    grep = request.args.get("grep", "")
    since = request.args.get("since", "")

    from server_sre_agent.docker_client import get_client
    try:
        client = get_client()
        container = client.containers.get(name)
        kwargs = {"tail": min(tail, 500), "timestamps": True}
        if since:
            import datetime
            now = datetime.datetime.now(datetime.timezone.utc)
            if since.endswith("m"):
                delta = datetime.timedelta(minutes=int(since[:-1]))
            elif since.endswith("h"):
                delta = datetime.timedelta(hours=int(since[:-1]))
            elif since.endswith("d"):
                delta = datetime.timedelta(days=int(since[:-1]))
            else:
                delta = datetime.timedelta(hours=1)
            kwargs["since"] = now - delta
        logs = container.logs(**kwargs).decode("utf-8", errors="replace")
        if grep:
            logs = "\n".join(line for line in logs.splitlines() if grep in line)
        return Response(logs[:50000], mimetype="text/plain")
    except Exception as e:
        return {"error": str(e)}, 500
