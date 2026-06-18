"""Web chat server with SSE streaming and token auth."""

from __future__ import annotations

import hashlib
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


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def require_auth(f):
    """Check Bearer token on all /api routes."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not _token_hash:
            return f(*args, **kwargs)  # no token configured, allow all

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


@app.route("/api/chat", methods=["POST"])
@require_auth
def chat():
    """Non-streaming chat endpoint."""
    data = request.json
    message = data.get("message", "")
    if not message:
        return {"error": "empty message"}, 400

    if not _agent:
        return {"error": "agent not initialized"}, 500

    prompt = ASK_PROMPT.format(question=message)
    result = _agent.run(prompt)
    return {"reply": result}


@app.route("/api/chat/stream", methods=["POST"])
@require_auth
def chat_stream():
    """SSE streaming chat endpoint."""
    data = request.json or {}
    message = data.get("message", "")
    if not message:
        return {"error": "empty message"}, 400

    if not _agent:
        return {"error": "agent not initialized"}, 500

    def generate():
        q: queue.Queue = queue.Queue()

        def on_tool_call(name, inp):
            q.put({"type": "tool_call", "name": name, "input": inp})

        def on_tool_result(name, result):
            q.put({"type": "tool_result", "name": name})

        def run_agent():
            try:
                prompt = ASK_PROMPT.format(question=message)
                result = _agent.run_streaming(
                    prompt,
                    on_tool_call=on_tool_call,
                    on_tool_result=on_tool_result,
                )
                q.put({"type": "final", "text": result})
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
    import docker
    try:
        client = docker.from_env()
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

    from docker_sre_agent.tools.logs import FetchLogsTool
    tool = FetchLogsTool()
    result = tool.execute(name=name, tail=tail, grep=grep, since=since)
    return Response(result, mimetype="application/json")
