"""Command execution tool with safety controls."""

from __future__ import annotations

import json
import logging
import shlex
import subprocess
from typing import Any

from docker_sre_agent.tools.base import Tool

logger = logging.getLogger(__name__)

# Exact allowed commands (no prefix matching — prevents injection)
ALLOWED_COMMANDS = {
    # Docker cleanup (safe, auto-execute)
    "docker system prune -f",
    "docker system prune -a -f",
    "docker volume prune -f",
    "docker image prune -f",
    "docker image prune -a -f",
    "docker container prune -f",
    "docker network prune -f",
}


class RunCommandTool(Tool):
    """Execute a system command with strict safety controls."""

    @property
    def name(self) -> str:
        return "run_command"

    @property
    def description(self) -> str:
        return (
            "执行 Docker 清理命令。只允许以下精确命令: "
            + ", ".join(sorted(ALLOWED_COMMANDS))
        )

    @property
    def is_destructive(self) -> bool:
        return True

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "要执行的命令（必须完全匹配允许列表）",
                },
                "reason": {
                    "type": "string",
                    "description": "为什么要执行这个命令",
                },
            },
            "required": ["command", "reason"],
        }

    def execute(self, command: str = "", reason: str = "", **kwargs: Any) -> str:
        if not command:
            return json.dumps({"error": "未提供命令"}, ensure_ascii=False)

        # Exact match only — no prefix matching, no shell=True
        cmd = command.strip()
        if cmd not in ALLOWED_COMMANDS:
            return json.dumps({
                "error": f"命令不在白名单中: {cmd}",
                "allowed": sorted(ALLOWED_COMMANDS),
            }, ensure_ascii=False)

        logger.info(f"Executing: {cmd} (reason: {reason})")

        try:
            # Split command safely, no shell=True
            args = shlex.split(cmd)
            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=60,
            )
            return json.dumps({
                "command": cmd,
                "reason": reason,
                "exit_code": result.returncode,
                "stdout": result.stdout[-2000:] if result.stdout else "",
                "stderr": result.stderr[-1000:] if result.stderr else "",
                "success": result.returncode == 0,
            }, ensure_ascii=False, indent=2)
        except subprocess.TimeoutExpired:
            return json.dumps({
                "command": cmd,
                "error": "命令执行超时（60秒）",
            }, ensure_ascii=False)
        except Exception as e:
            return json.dumps({
                "command": cmd,
                "error": str(e),
            }, ensure_ascii=False)
