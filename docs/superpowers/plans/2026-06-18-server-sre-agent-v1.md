# Server SRE Agent v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a lightweight server SRE agent that monitors Docker containers, cleans disk, and auto-restarts failed containers — all running inside a Docker container with host filesystem access via temporary containers.

**Architecture:** Docker container agent → Docker socket (for Docker management) + temporary containers (for host filesystem access). Rules + AI hybrid decision making. Flask web UI for status viewing.

**Tech Stack:** Python 3.11+, Docker SDK, Flask, Anthropic Claude API, PyYAML

## Global Constraints

- Python >=3.9 (use `from __future__ import annotations` for 3.9 compat)
- Build system: hatchling
- Dependencies: docker, pyyaml, anthropic, flask
- Memory budget: < 100MB resident
- No external databases — SQLite for knowledge base
- All host access via temporary Docker containers (never mount host root rw)

---

## Task 1: Project Scaffolding

**Files:**
- Create: `pyproject.toml`
- Create: `server_sre_agent/__init__.py`
- Create: `server_sre_agent/config.py`
- Create: `config.yaml`
- Create: `Dockerfile`
- Create: `docker-compose.yml`
- Create: `.dockerignore`
- Create: `.gitignore`

**Interfaces:**
- Consumes: none
- Produces: `AgentConfig` dataclass, `load_config()` function

- [ ] **Step 1: Create pyproject.toml**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "server-sre-agent"
version = "0.1.0"
description = "Lightweight server SRE agent for small cloud servers"
readme = "README.md"
license = "MIT"
requires-python = ">=3.9"
dependencies = [
    "docker>=7.0.0",
    "pyyaml>=6.0",
    "anthropic>=0.40.0",
    "flask>=3.0.0",
]

[project.optional-dependencies]
dev = ["pytest>=8.0"]

[project.scripts]
server-sre = "server_sre_agent.main:main"

[tool.hatch.build.targets.wheel]
packages = ["server_sre_agent"]
```

- [ ] **Step 2: Create config.py**

```python
"""Configuration loader."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


@dataclass
class MonitorConfig:
    exclude_containers: list[str] = field(default_factory=lambda: ["server-sre-agent"])
    check_interval: int = 300


@dataclass
class RestartConfig:
    max_per_container_per_hour: int = 5
    max_global_per_hour: int = 20
    timeout: int = 10
    max_consecutive_fails: int = 3


@dataclass
class CleanupConfig:
    mode: str = "report"
    exclude_paths: list[str] = field(default_factory=lambda: ["/etc", "/boot", "/usr"])
    auto_clean: list[str] = field(default_factory=lambda: [
        "docker system prune -f",
        "docker volume prune -f",
    ])


@dataclass
class LLMConfig:
    api_key: str = ""
    base_url: str = ""
    model: str = "claude-sonnet-4-20250514"
    max_tokens: int = 4096
    max_tool_rounds: int = 10
    timeout: int = 120
    enabled: bool = True


@dataclass
class WebConfig:
    port: int = 6700
    token: str = ""


@dataclass
class AgentConfig:
    name: str = "server-sre"
    monitor: MonitorConfig = field(default_factory=MonitorConfig)
    restart: RestartConfig = field(default_factory=RestartConfig)
    cleanup: CleanupConfig = field(default_factory=CleanupConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    web: WebConfig = field(default_factory=WebConfig)
    log_level: str = "INFO"


def load_config(path: str | Path | None = None) -> AgentConfig:
    """Load configuration from YAML, falling back to defaults."""
    config = AgentConfig()
    config_path = Path(path) if path else Path("config.yaml")

    if not config_path.exists():
        logger.warning(f"Config not found: {config_path}, using defaults")
        return _apply_env(config)

    try:
        with open(config_path) as f:
            raw: dict = yaml.safe_load(f) or {}

        if "agent" in raw:
            config.name = raw["agent"].get("name", config.name)
        if "monitor" in raw:
            m = raw["monitor"]
            config.monitor = MonitorConfig(
                exclude_containers=m.get("exclude_containers", config.monitor.exclude_containers),
                check_interval=m.get("check_interval", config.monitor.check_interval),
            )
        if "restart" in raw:
            r = raw["restart"]
            config.restart = RestartConfig(
                max_per_container_per_hour=r.get("max_per_container_per_hour", config.restart.max_per_container_per_hour),
                max_global_per_hour=r.get("max_global_per_hour", config.restart.max_global_per_hour),
                timeout=r.get("timeout", config.restart.timeout),
                max_consecutive_fails=r.get("max_consecutive_fails", config.restart.max_consecutive_fails),
            )
        if "cleanup" in raw:
            c = raw["cleanup"]
            config.cleanup = CleanupConfig(
                mode=c.get("mode", config.cleanup.mode),
                exclude_paths=c.get("exclude_paths", config.cleanup.exclude_paths),
                auto_clean=c.get("auto_clean", config.cleanup.auto_clean),
            )
        if "llm" in raw:
            l = raw["llm"]
            config.llm = LLMConfig(
                api_key=l.get("api_key", config.llm.api_key),
                base_url=l.get("base_url", config.llm.base_url),
                model=l.get("model", config.llm.model),
                max_tokens=l.get("max_tokens", config.llm.max_tokens),
                max_tool_rounds=l.get("max_tool_rounds", config.llm.max_tool_rounds),
                timeout=l.get("timeout", config.llm.timeout),
                enabled=l.get("enabled", config.llm.enabled),
            )
        if "web" in raw:
            w = raw["web"]
            config.web = WebConfig(
                port=w.get("port", config.web.port),
                token=w.get("token", config.web.token),
            )
        if "log" in raw:
            config.log_level = raw["log"].get("level", config.log_level)

        logger.info(f"Loaded config from {config_path}")
    except Exception:
        logger.exception(f"Failed to load config from {config_path}")

    return _apply_env(config)


def _load_dotenv(path: str = "/app/.env") -> None:
    """Load .env file into os.environ."""
    p = Path(path)
    if not p.exists():
        return
    try:
        with open(p, "rb") as f:
            raw = f.read()
        if raw.startswith(b'\xef\xbb\xbf'):
            raw = raw[3:]
        text = raw.decode("utf-8", errors="replace")
        loaded = 0
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
                    loaded += 1
        if loaded:
            logger.info(f"Loaded {loaded} variables from {path}")
    except Exception as e:
        logger.warning(f"Failed to load .env: {e}")


def _apply_env(config: AgentConfig) -> AgentConfig:
    """Apply environment variable overrides."""
    _load_dotenv()
    env_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if env_key and env_key != "your-key-here":
        config.llm.api_key = env_key
    env_url = os.environ.get("ANTHROPIC_BASE_URL", "")
    if env_url:
        config.llm.base_url = env_url
    if os.environ.get("ANTHROPIC_MODEL"):
        config.llm.model = os.environ["ANTHROPIC_MODEL"]
    env_token = os.environ.get("WEB_TOKEN", "")
    if env_token:
        config.web.token = env_token
    return config
```

- [ ] **Step 3: Create config.yaml**

```yaml
agent:
  name: "server-sre"

monitor:
  exclude_containers: ["server-sre-agent"]
  check_interval: 300

restart:
  max_per_container_per_hour: 5
  max_global_per_hour: 20
  timeout: 10
  max_consecutive_fails: 3

cleanup:
  mode: "report"
  exclude_paths: ["/etc", "/boot", "/usr"]
  auto_clean:
    - "docker system prune -f"
    - "docker volume prune -f"

llm:
  model: "claude-sonnet-4-20250514"
  enabled: true
  max_tool_rounds: 10

web:
  port: 6700
  token: "${WEB_TOKEN}"

log:
  level: "INFO"
```

- [ ] **Step 4: Create Dockerfile**

```dockerfile
FROM python:3.12-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    curl -fsSL https://download.docker.com/linux/static/stable/x86_64/docker-27.5.1.tgz | \
    tar -xz --strip-components=1 -C /usr/local/bin docker/docker && \
    apt-get purge -y curl && apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . .
RUN pip install --no-cache-dir hatchling && pip install --no-cache-dir .
RUN mkdir -p /app/data

EXPOSE 6700

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:6700/')" || exit 1

ENTRYPOINT ["server-sre"]
CMD ["web", "--port", "6700"]
```

- [ ] **Step 5: Create docker-compose.yml**

```yaml
version: "3.8"
services:
  server-sre:
    build: .
    image: server-sre-agent:latest
    container_name: server-sre-agent
    restart: unless-stopped
    ports:
      - "6700:6700"
    env_file: .env
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
```

- [ ] **Step 6: Create .dockerignore**

```
.git
.github
.gitignore
.env
.venv
venv
__pycache__
*.pyc
*.egg-info
dist
build
data
*.db
docs
.mcp.json
```

- [ ] **Step 7: Create .gitignore**

```
__pycache__/
*.py[cod]
*.egg-info/
dist/
build/
.eggs/
*.egg
.env
.venv/
venv/
data/
*.db
.mcp.json
```

- [ ] **Step 8: Create __init__.py**

```python
"""Server SRE Agent — lightweight server maintenance agent."""

__version__ = "0.1.0"
```

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "init: project scaffolding — config, Dockerfile, docker-compose"
```

---

## Task 2: Docker Client + Tool Base

**Files:**
- Create: `server_sre_agent/docker_client.py`
- Create: `server_sre_agent/tools/__init__.py`
- Create: `server_sre_agent/tools/base.py`

**Interfaces:**
- Consumes: `AgentConfig`
- Produces: `get_client()` → `docker.DockerClient`, `Tool` base class

- [ ] **Step 1: Create docker_client.py**

```python
"""Shared Docker client — create once, use everywhere."""

from __future__ import annotations

import docker

_client: docker.DockerClient | None = None


def get_client() -> docker.DockerClient:
    global _client
    if _client is None:
        _client = docker.from_env()
    return _client


def close() -> None:
    global _client
    if _client is not None:
        _client.close()
        _client = None
```

- [ ] **Step 2: Create tools/base.py**

```python
"""Base tool class."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class Tool(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def description(self) -> str: ...

    @property
    @abstractmethod
    def input_schema(self) -> dict[str, Any]: ...

    @property
    def is_destructive(self) -> bool:
        return False

    @abstractmethod
    def execute(self, **kwargs: Any) -> str: ...

    def to_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }
```

- [ ] **Step 3: Create tools/__init__.py**

```python
"""Tools for the Server SRE Agent."""

from server_sre_agent.tools.base import Tool

ALL_TOOLS: list[Tool] = []

__all__ = ["ALL_TOOLS", "Tool"]
```

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "feat: Docker client wrapper + tool base class"
```

---

## Task 3: Docker Management Tools

**Files:**
- Create: `server_sre_agent/tools/docker.py`
- Modify: `server_sre_agent/tools/__init__.py`

**Interfaces:**
- Consumes: `get_client()` from docker_client.py
- Produces: `DockerInfoTool`, `DockerCleanTool`, `ContainerListTool`, `ContainerRestartTool`

- [ ] **Step 1: Create tools/docker.py**

```python
"""Docker management tools."""

from __future__ import annotations

import json
import logging
from typing import Any

from server_sre_agent.docker_client import get_client
from server_sre_agent.tools.base import Tool

logger = logging.getLogger(__name__)


def _format_size(size_bytes: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f}{unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f}PB"


class DockerInfoTool(Tool):
    @property
    def name(self) -> str:
        return "docker_info"

    @property
    def description(self) -> str:
        return "获取 Docker 磁盘占用分析：镜像、容器、卷、构建缓存"

    @property
    def input_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    def execute(self, **kwargs: Any) -> str:
        try:
            client = get_client()
            info = client.df()
            result = {}
            for key in ("Images", "Containers", "Volumes", "BuildCache"):
                items = info.get(key, [])
                total = sum(item.get("Size", 0) for item in items)
                result[key.lower()] = {
                    "count": len(items),
                    "total_size": _format_size(total),
                    "total_bytes": total,
                }
            result["summary"] = (
                f"镜像: {result['images']['count']}个({result['images']['total_size']}), "
                f"容器: {result['containers']['count']}个({result['containers']['total_size']}), "
                f"卷: {result['volumes']['count']}个({result['volumes']['total_size']}), "
                f"构建缓存: {result['buildcache']['total_size']}"
            )
            return json.dumps(result, ensure_ascii=False, indent=2)
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)


class DockerCleanTool(Tool):
    @property
    def name(self) -> str:
        return "docker_clean"

    @property
    def description(self) -> str:
        return "清理 Docker 资源：悬空镜像、停止的容器、未使用卷、构建缓存"

    @property
    def is_destructive(self) -> bool:
        return True

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "enum": ["all", "images", "containers", "volumes", "buildcache"],
                    "default": "all",
                    "description": "要清理的资源类型",
                },
            },
        }

    def execute(self, target: str = "all", **kwargs: Any) -> str:
        import subprocess
        commands = {
            "all": "docker system prune -f",
            "images": "docker image prune -f",
            "containers": "docker container prune -f",
            "volumes": "docker volume prune -f",
            "buildcache": "docker builder prune -f",
        }
        cmd = commands.get(target, commands["all"])
        try:
            result = subprocess.run(cmd.split(), capture_output=True, text=True, timeout=60)
            return json.dumps({
                "command": cmd,
                "success": result.returncode == 0,
                "output": result.stdout.strip(),
                "error": result.stderr.strip() if result.returncode != 0 else None,
            }, ensure_ascii=False, indent=2)
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)


class ContainerListTool(Tool):
    @property
    def name(self) -> str:
        return "container_list"

    @property
    def description(self) -> str:
        return "列出所有 Docker 容器及其状态"

    @property
    def input_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    def execute(self, **kwargs: Any) -> str:
        try:
            client = get_client()
            containers = client.containers.list(all=True)
            result = []
            for c in containers:
                result.append({
                    "name": c.name,
                    "status": c.status,
                    "image": c.image.tags[0] if c.image.tags else str(c.image.id)[:12],
                    "created": c.attrs.get("Created", "")[:19],
                })
            return json.dumps({"containers": result, "total": len(result)}, ensure_ascii=False, indent=2)
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)


class ContainerRestartTool(Tool):
    @property
    def name(self) -> str:
        return "container_restart"

    @property
    def description(self) -> str:
        return "重启指定的 Docker 容器"

    @property
    def is_destructive(self) -> bool:
        return True

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "容器名称"},
            },
            "required": ["name"],
        }

    def execute(self, name: str = "", **kwargs: Any) -> str:
        try:
            client = get_client()
            container = client.containers.get(name)
            container.restart(timeout=10)
            return json.dumps({"success": True, "message": f"容器 '{name}' 已重启"}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)
```

- [ ] **Step 2: Update tools/__init__.py**

```python
"""Tools for the Server SRE Agent."""

from server_sre_agent.tools.base import Tool
from server_sre_agent.tools.docker import (
    DockerInfoTool, DockerCleanTool, ContainerListTool, ContainerRestartTool,
)

ALL_TOOLS: list[Tool] = [
    DockerInfoTool(),
    DockerCleanTool(),
    ContainerListTool(),
    ContainerRestartTool(),
]

__all__ = ["ALL_TOOLS", "Tool"]
```

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "feat: Docker tools — info, clean, list, restart"
```

---

## Task 4: Host Access Tools (Temporary Containers)

**Files:**
- Create: `server_sre_agent/tools/host.py`
- Modify: `server_sre_agent/tools/__init__.py`

**Interfaces:**
- Consumes: `get_client()` from docker_client.py
- Produces: `HostExecTool`, `HostDiskScanTool`

- [ ] **Step 1: Create tools/host.py**

```python
"""Host filesystem tools — via temporary containers."""

from __future__ import annotations

import json
import logging
import shlex
import subprocess
from typing import Any

from server_sre_agent.docker_client import get_client
from server_sre_agent.tools.base import Tool

logger = logging.getLogger(__name__)

# Allowed commands for host execution (exact match)
ALLOWED_HOST_COMMANDS = {
    "du -sh /*",
    "df -h",
    "ls -la /host/",
    "find /host -type f -size +100M -printf '%s %p\\n'",
    "du -sh /host/var/lib/docker/*",
    "du -sh /host/opt/*",
    "du -sh /host/tmp/*",
    "du -sh /host/var/log/*",
    "docker system df",
}


class HostExecTool(Tool):
    """Execute commands on the host via temporary containers."""

    @property
    def name(self) -> str:
        return "host_exec"

    @property
    def description(self) -> str:
        return (
            "在宿主机上执行命令（通过临时只读容器）。"
            f"允许的命令: {', '.join(sorted(ALLOWED_HOST_COMMANDS)[:3])}..."
        )

    @property
    def is_destructive(self) -> bool:
        return False

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "要在宿主机上执行的命令（只读）",
                },
            },
            "required": ["command"],
        }

    def execute(self, command: str = "", **kwargs: Any) -> str:
        if not command:
            return json.dumps({"error": "未提供命令"}, ensure_ascii=False)

        cmd = command.strip()
        if cmd not in ALLOWED_HOST_COMMANDS:
            return json.dumps({
                "error": f"命令不在白名单中: {cmd}",
                "allowed": sorted(ALLOWED_HOST_COMMANDS),
            }, ensure_ascii=False)

        try:
            client = get_client()
            result = client.containers.run(
                "alpine:latest",
                command=["sh", "-c", cmd],
                volumes={"/": {"bind": "/host", "mode": "ro"}},
                remove=True,
                detach=False,
                timeout=30,
            )
            output = result.decode("utf-8", errors="replace") if isinstance(result, bytes) else result
            return json.dumps({
                "command": cmd,
                "success": True,
                "output": output[:5000],
            }, ensure_ascii=False, indent=2)
        except Exception as e:
            return json.dumps({"command": cmd, "error": str(e)}, ensure_ascii=False)


class HostDiskScanTool(Tool):
    """Scan host disk usage via temporary container."""

    @property
    def name(self) -> str:
        return "host_disk_scan"

    @property
    def description(self) -> str:
        return "扫描宿主机磁盘使用情况：大文件、目录占用"

    @property
    def input_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    def execute(self, **kwargs: Any) -> str:
        try:
            client = get_client()
            # Get overall disk usage
            df_result = client.containers.run(
                "alpine:latest",
                command=["sh", "-c", "df -h /"],
                volumes={"/": {"bind": "/host", "mode": "ro"}},
                remove=True, detach=False, timeout=10,
            ).decode("utf-8", errors="replace")

            # Get top directories
            du_result = client.containers.run(
                "alpine:latest",
                command=["sh", "-c", "du -sh /host/*/ 2>/dev/null | sort -rh | head -15"],
                volumes={"/": {"bind": "/host", "mode": "ro"}},
                remove=True, detach=False, timeout=30,
            ).decode("utf-8", errors="replace")

            # Get Docker-specific usage
            docker_result = client.containers.run(
                "alpine:latest",
                command=["sh", "-c", "du -sh /host/var/lib/docker/*/ 2>/dev/null | sort -rh"],
                volumes={"/": {"bind": "/host", "mode": "ro"}},
                remove=True, detach=False, timeout=30,
            ).decode("utf-8", errors="replace")

            return json.dumps({
                "disk_overview": df_result.strip(),
                "top_directories": du_result.strip(),
                "docker_usage": docker_result.strip(),
            }, ensure_ascii=False, indent=2)
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)
```

- [ ] **Step 2: Update tools/__init__.py**

Add to the imports and ALL_TOOLS list:

```python
from server_sre_agent.tools.host import HostExecTool, HostDiskScanTool

ALL_TOOLS: list[Tool] = [
    DockerInfoTool(),
    DockerCleanTool(),
    ContainerListTool(),
    ContainerRestartTool(),
    HostExecTool(),
    HostDiskScanTool(),
]
```

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "feat: host tools — execute commands and scan disk via temp containers"
```

---

## Task 5: LLM Client + Agent Loop

**Files:**
- Create: `server_sre_agent/llm.py`
- Create: `server_sre_agent/agent.py`
- Create: `server_sre_agent/prompts.py`

**Interfaces:**
- Consumes: `Tool.to_schema()`, `Tool.execute()`
- Produces: `LLMClient`, `Agent.chat()`, `Agent.chat_streaming()`

- [ ] **Step 1: Create llm.py**

```python
"""Claude API wrapper with retry logic."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import anthropic

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_DELAY = 2


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class AgentResponse:
    text: str | None
    tool_calls: list[ToolCall]
    stop_reason: str
    input_tokens: int = 0
    output_tokens: int = 0


class LLMClient:
    def __init__(self, api_key: str, base_url: str = "", model: str = "claude-sonnet-4-20250514",
                 max_tokens: int = 4096, timeout: int = 120) -> None:
        kwargs: dict[str, Any] = {"api_key": api_key, "timeout": timeout}
        if base_url:
            kwargs["base_url"] = base_url
        self.client = anthropic.Anthropic(**kwargs)
        self.model = model
        self.max_tokens = max_tokens

    def chat(self, messages: list[dict], tools: list[dict] | None = None,
             system: str | None = None) -> AgentResponse:
        kwargs: dict[str, Any] = {
            "model": self.model, "max_tokens": self.max_tokens, "messages": messages,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = tools

        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                response = self.client.messages.create(**kwargs)
                tool_calls, text_parts = [], []
                for block in response.content:
                    if block.type == "tool_use":
                        tool_calls.append(ToolCall(id=block.id, name=block.name, input=block.input))
                    elif block.type == "text":
                        text_parts.append(block.text)
                return AgentResponse(
                    text="\n".join(text_parts) if text_parts else None,
                    tool_calls=tool_calls, stop_reason=response.stop_reason,
                    input_tokens=response.usage.input_tokens, output_tokens=response.usage.output_tokens,
                )
            except (anthropic.RateLimitError, anthropic.APIConnectionError, anthropic.APITimeoutError) as e:
                last_error = e
                wait = RETRY_DELAY * (2 ** attempt)
                logger.warning(f"LLM error, retrying in {wait}s: {e}")
                time.sleep(wait)

        return AgentResponse(text=f"LLM 调用失败: {last_error}", tool_calls=[], stop_reason="error")

    def make_tool_result(self, tool_call_id: str, content: str, is_error: bool = False) -> dict:
        return {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": tool_call_id, "content": content, "is_error": is_error}],
        }
```

- [ ] **Step 2: Create prompts.py**

```python
"""System prompts."""

SYSTEM_PROMPT = """你是一个专业的 Linux/SRE 运行专家。你的职责：
1. 监控服务器资源使用情况
2. 发现并清理无用的 Docker 资源和磁盘垃圾
3. 确保服务器健康运行

重要：你可以直接执行命令！Docker socket 已挂载，你可以执行 docker 命令。宿主机文件系统可以通过临时容器访问。

安全规则：
- 绝对不删除系统关键文件（/etc, /boot, /usr, /bin, /sbin）
- 不删除正在运行的容器
- 不删除 7 天内有变化的数据卷
- 建议必须给出理由

输出格式（中文）：
- 用简洁的列表给出建议
- 按回收空间大小排序
- 标注风险等级：安全删除 / 需确认 / 不建议删除
"""

ASK_PROMPT = """你是服务器运维助手。用户有以下问题，请用工具收集信息后回答。

用户问题：{question}"""

SCAN_PROMPT = """你是服务器维护助手。以下是扫描结果，请分析并给出清理建议。

{scan_data}

请分析以上数据，给出清理建议。"""
```

- [ ] **Step 3: Create agent.py**

```python
"""ReAct agent loop."""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

from server_sre_agent.llm import LLMClient
from server_sre_agent.tools.base import Tool
from server_sre_agent.prompts import SYSTEM_PROMPT

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 10
MAX_MESSAGE_CHARS = 80000


class Agent:
    def __init__(self, llm: LLMClient, tools: list[Tool],
                 system_prompt: str = SYSTEM_PROMPT, max_rounds: int = MAX_TOOL_ROUNDS) -> None:
        self.llm = llm
        self.tools = tools
        self.system_prompt = system_prompt
        self.max_rounds = max_rounds
        self._tool_map = {t.name: t for t in tools}

    def _get_tool_schemas(self) -> list[dict]:
        return [t.to_schema() for t in self.tools]

    def _truncate_tools_if_needed(self, messages: list[dict]) -> list[dict]:
        total = sum(len(json.dumps(m, ensure_ascii=False)) for m in messages)
        if total <= MAX_MESSAGE_CHARS:
            return messages
        for msg in messages:
            if msg.get("role") == "user" and isinstance(msg.get("content"), list):
                for block in msg["content"]:
                    if block.get("type") == "tool_result" and len(block.get("content", "")) > 2000:
                        block["content"] = block["content"][:2000] + "\n... [truncated]"
        return messages

    def _execute_tool(self, name: str, input_args: dict) -> tuple[str, bool]:
        tool = self._tool_map.get(name)
        if not tool:
            return json.dumps({"error": f"未知工具: {name}"}), True
        try:
            return tool.execute(**input_args), False
        except Exception as e:
            logger.exception(f"Tool '{name}' failed")
            return json.dumps({"error": str(e)}), True

    def _run_loop(self, messages: list[dict], system_prompt: str | None,
                  on_tool_call: Callable | None, on_tool_result: Callable | None) -> list[dict]:
        prompt = system_prompt or self.system_prompt
        for round_num in range(self.max_rounds):
            messages = self._truncate_tools_if_needed(messages)
            response = self.llm.chat(messages=messages, tools=self._get_tool_schemas(), system=prompt)
            if not response.tool_calls:
                messages.append({"role": "assistant", "content": response.text or ""})
                return messages
            assistant_content = []
            if response.text:
                assistant_content.append({"type": "text", "text": response.text})
            for tc in response.tool_calls:
                assistant_content.append({"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.input})
            messages.append({"role": "assistant", "content": assistant_content})
            for tc in response.tool_calls:
                if on_tool_call:
                    on_tool_call(tc.name, tc.input)
                result, is_error = self._execute_tool(tc.name, tc.input)
                if on_tool_result:
                    on_tool_result(tc.name, result)
                messages.append(self.llm.make_tool_result(tc.id, result, is_error=is_error))
        messages.append({"role": "assistant", "content": "达到最大工具调用次数，请简化问题后重试。"})
        return messages

    def chat(self, messages: list[dict], system_prompt: str | None = None) -> list[dict]:
        return self._run_loop(list(messages), system_prompt, None, None)

    def chat_streaming(self, messages: list[dict], system_prompt: str | None = None,
                       on_tool_call: Callable | None = None, on_tool_result: Callable | None = None) -> list[dict]:
        return self._run_loop(list(messages), system_prompt, on_tool_call, on_tool_result)
```

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "feat: LLM client + ReAct agent loop"
```

---

## Task 6: Scanner (Docker Event Listener + Auto-Restart)

**Files:**
- Create: `server_sre_agent/scanner.py`

**Interfaces:**
- Consumes: `get_client()`, `AgentConfig`
- Produces: `Scanner.start()`, `Scanner.stop()`

- [ ] **Step 1: Create scanner.py**

```python
"""Docker event listener + auto-restart."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field

from server_sre_agent.config import AgentConfig
from server_sre_agent.docker_client import get_client

logger = logging.getLogger(__name__)

TRIGGER_EVENTS = {"die", "oom"}
HEALTH_PREFIX = "health_status: unhealthy"
RECONNECT_DELAY = 5
MAX_STATE_AGE = 86400


@dataclass
class ContainerState:
    restart_timestamps: deque = field(default_factory=deque)
    consecutive_fails: int = 0
    stopped: bool = False


class Scanner:
    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self.client = get_client()
        self._states: dict[str, ContainerState] = {}
        self._global_timestamps: deque = deque()
        self._running = False
        self._last_cleanup = time.time()

    def _should_monitor(self, name: str) -> bool:
        if name in self.config.monitor.exclude_containers:
            return False
        return True

    def _cleanup_window(self, timestamps: deque, window: float = 3600) -> None:
        cutoff = time.time() - window
        while timestamps and timestamps[0] < cutoff:
            timestamps.popleft()

    def _is_rate_limited(self, name: str) -> bool:
        rc = self.config.restart
        state = self._get_state(name)
        self._cleanup_window(state.restart_timestamps)
        if len(state.restart_timestamps) >= rc.max_per_container_per_hour:
            return True
        self._cleanup_window(self._global_timestamps)
        if len(self._global_timestamps) >= rc.max_global_per_hour:
            return True
        return False

    def _record_restart(self, name: str) -> None:
        now = time.time()
        self._get_state(name).restart_timestamps.append(now)
        self._global_timestamps.append(now)

    def _get_state(self, name: str) -> ContainerState:
        if name not in self._states:
            self._states[name] = ContainerState()
        return self._states[name]

    def _restart(self, name: str) -> bool:
        try:
            container = self.client.containers.get(name)
        except Exception:
            return False
        try:
            container.restart(timeout=self.config.restart.timeout)
            logger.info(f"Restarted '{name}'")
            return True
        except Exception:
            pass
        try:
            container.stop(timeout=self.config.restart.timeout)
            container.start()
            logger.info(f"Stop+start '{name}' succeeded")
            return True
        except Exception as e:
            logger.error(f"Failed to restart '{name}': {e}")
            return False

    def _handle_event(self, event: dict) -> None:
        status = event.get("status", "")
        if status not in TRIGGER_EVENTS and not status.startswith(HEALTH_PREFIX):
            return
        name = event.get("Actor", {}).get("Attributes", {}).get("name")
        if not name or not self._should_monitor(name):
            return
        state = self._get_state(name)
        if state.stopped or self._is_rate_limited(name):
            return
        success = self._restart(name)
        if success:
            state.consecutive_fails = 0
        else:
            state.consecutive_fails += 1
            if state.consecutive_fails >= self.config.restart.max_consecutive_fails:
                state.stopped = True
                logger.error(f"'{name}' failed {state.consecutive_fails} times, stopping auto-restart")
        self._record_restart(name)

    def _cleanup_old_states(self) -> None:
        now = time.time()
        if now - self._last_cleanup < 3600:
            return
        self._last_cleanup = now
        to_remove = [n for n, s in self._states.items()
                     if s.restart_timestamps and s.restart_timestamps[-1] < now - MAX_STATE_AGE]
        for n in to_remove:
            del self._states[n]

    def _event_loop(self) -> None:
        while self._running:
            try:
                for event in self.client.events(decode=True):
                    if not self._running:
                        break
                    try:
                        self._handle_event(event)
                        self._cleanup_old_states()
                    except Exception:
                        logger.exception("Error handling event")
            except Exception:
                if not self._running:
                    break
                logger.exception(f"Events lost, reconnecting in {RECONNECT_DELAY}s...")
                time.sleep(RECONNECT_DELAY)

    async def start(self) -> None:
        self._running = True
        await asyncio.to_thread(self._event_loop)

    async def stop(self) -> None:
        self._running = False
```

- [ ] **Step 2: Commit**

```bash
git add -A
git commit -m "feat: Docker event scanner with auto-restart and rate limiting"
```

---

## Task 7: Scheduler (Periodic Tasks)

**Files:**
- Create: `server_sre_agent/scheduler.py`

**Interfaces:**
- Consumes: `Agent`, `AgentConfig`
- Produces: `Scheduler.start()`, `Scheduler.stop()`

- [ ] **Step 1: Create scheduler.py**

```python
"""Scheduler for periodic scan tasks."""

from __future__ import annotations

import asyncio
import logging

from server_sre_agent.agent import Agent
from server_sre_agent.config import AgentConfig
from server_sre_agent.prompts import SCAN_PROMPT

logger = logging.getLogger(__name__)


class Scheduler:
    def __init__(self, config: AgentConfig, agent: Agent) -> None:
        self.config = config
        self.agent = agent
        self._running = False
        self._tasks: list[asyncio.Task] = []
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._tasks = [
            asyncio.create_task(self._run_periodic("disk_scan", 3600, self._disk_scan)),
            asyncio.create_task(self._run_periodic("resource_check", self.config.monitor.check_interval, self._resource_check)),
        ]
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def stop(self) -> None:
        self._running = False
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    async def _run_periodic(self, name: str, interval: int, handler) -> None:
        while self._running:
            try:
                await asyncio.sleep(interval)
                if not self._running:
                    break
                await handler()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception(f"Error in {name}")

    async def _disk_scan(self) -> None:
        if self._lock.locked():
            return
        async with self._lock:
            from server_sre_agent.tools.docker import DockerInfoTool
            from server_sre_agent.tools.host import HostDiskScanTool
            def scan():
                return DockerInfoTool().execute() + "\n\n" + HostDiskScanTool().execute()
            scan_data = await asyncio.to_thread(scan)
            prompt = SCAN_PROMPT.format(scan_data=scan_data)
            result = await asyncio.to_thread(self.agent.chat, [{"role": "user", "content": prompt}])
            logger.info(f"Disk scan result: {result[-1].get('content', '')[:500]}")

    async def _resource_check(self) -> None:
        from server_sre_agent.tools.docker import ContainerListTool
        def check():
            return ContainerListTool().execute()
        result = await asyncio.to_thread(check)
        logger.debug(f"Resource check: {result[:200]}")
```

- [ ] **Step 2: Commit**

```bash
git add -A
git commit -m "feat: scheduler — periodic disk scan and resource check"
```

---

## Task 8: Web UI + Main Entry Point

**Files:**
- Create: `server_sre_agent/web.py`
- Create: `server_sre_agent/main.py`
- Create: `server_sre_agent/templates/chat.html`

**Interfaces:**
- Consumes: `Agent`, `AgentConfig`, `ALL_TOOLS`
- Produces: Flask app, CLI entry point

- [ ] **Step 1: Create web.py**

(Same structure as current docker_sre_agent/web.py — Flask app with session management, chat endpoint, MCP endpoint. Copy from current codebase and update imports to use `server_sre_agent` package.)

- [ ] **Step 2: Create main.py**

```python
"""Entry point."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

from server_sre_agent.config import load_config
from server_sre_agent.agent import Agent
from server_sre_agent.llm import LLMClient
from server_sre_agent.tools import ALL_TOOLS


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=level, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S", handlers=[logging.StreamHandler(sys.stdout)],
    )


def cmd_web(config) -> None:
    from server_sre_agent.web import create_app
    app = create_app(config)
    logging.info(f"Web server on http://0.0.0.0:{config.web.port}")
    app.run(host="0.0.0.0", port=config.web.port, debug=False, threaded=True)


def cmd_run(config) -> None:
    from server_sre_agent.scanner import Scanner
    from server_sre_agent.scheduler import Scheduler
    llm = LLMClient(api_key=config.llm.api_key, base_url=config.llm.base_url,
                     model=config.llm.model, max_tokens=config.llm.max_tokens, timeout=config.llm.timeout)
    agent = Agent(llm=llm, tools=ALL_TOOLS, max_rounds=config.llm.max_tool_rounds)
    scanner = Scanner(config)
    scheduler = Scheduler(config, agent)

    async def run_all():
        shutdown = asyncio.Event()
        def handle_signal(sig, frame):
            shutdown.set()
        signal.signal(signal.SIGINT, handle_signal)
        signal.signal(signal.SIGTERM, handle_signal)
        await asyncio.gather(scanner.start(), scheduler.start())
        await shutdown.wait()
        await scanner.stop()
        await scheduler.stop()
        from server_sre_agent.docker_client import close
        close()

    asyncio.run(run_all())


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Server SRE Agent")
    parser.add_argument("--version", "-v", action="store_true")
    sub = parser.add_subparsers(dest="command")
    for name, help_text in [("run", "Run daemon"), ("web", "Run web server")]:
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--config", "-c", default=None)
        p.add_argument("--port", "-p", type=int, default=6700)
    args = parser.parse_args()
    if args.version:
        from server_sre_agent import __version__
        print(f"server-sre-agent v{__version__}")
        return
    config = load_config(args.config)
    setup_logging(config.log_level)
    if args.command == "web":
        cmd_web(config)
    else:
        cmd_run(config)


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "feat: web UI + main entry point"
```

---

## Task 9: Integration Test + Deploy

**Files:**
- Create: `tests/test_tools.py`
- Modify: `Dockerfile`
- Modify: `docker-compose.yml`

**Interfaces:**
- Consumes: all tools
- Produces: passing tests, working deployment

- [ ] **Step 1: Create test file**

```python
"""Basic tool tests."""

import json
from server_sre_agent.tools.docker import DockerInfoTool, ContainerListTool, DockerCleanTool


def test_docker_info_tool():
    tool = DockerInfoTool()
    assert tool.name == "docker_info"
    result = tool.execute()
    data = json.loads(result)
    assert "images" in data or "error" in data


def test_container_list_tool():
    tool = ContainerListTool()
    assert tool.name == "container_list"
    result = tool.execute()
    data = json.loads(result)
    assert "containers" in data or "error" in data


def test_docker_clean_tool_dry():
    tool = DockerCleanTool()
    assert tool.is_destructive is True
    assert tool.name == "docker_clean"
```

- [ ] **Step 2: Run tests**

```bash
pytest tests/ -v
```

- [ ] **Step 3: Update docker-compose.yml**

```yaml
version: "3.8"
services:
  server-sre:
    build: .
    image: server-sre-agent:latest
    container_name: server-sre-agent
    restart: unless-stopped
    ports:
      - "6700:6700"
    env_file: .env
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
```

- [ ] **Step 4: Update deploy.yml**

Update the GitHub Action to deploy as Docker container with docker socket mount.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: integration tests + deployment config"
```

- [ ] **Step 6: Final commit**

```bash
git add -A
git commit -m "v0.1.0: server-sre-agent — Docker management + host access + web UI"
```
