"""Command execution tool with safety controls."""

from __future__ import annotations

import json
import logging
import subprocess
from typing import Any

from docker_sre_agent.tools.base import Tool

logger = logging.getLogger(__name__)

# Commands that are safe to run automatically
SAFE_COMMANDS = [
    "docker system prune",
    "docker system prune -f",
    "docker volume prune",
    "docker volume prune -f",
    "docker image prune",
    "docker image prune -f",
    "docker container prune",
    "docker container prune -f",
    "docker network prune",
    "docker network prune -f",
]

# Commands that require confirmation
CONFIRM_COMMANDS = [
    "docker rm",
    "docker rmi",
    "truncate",
    "rm",
]


class RunCommandTool(Tool):
    """Execute a system command with safety checks."""

    @property
    def name(self) -> str:
        return "run_command"

    @property
    def description(self) -> str:
        return (
            "执行系统清理命令。自动模式下安全命令自动执行，其他需用户确认。"
            f"安全命令: {', '.join(SAFE_COMMANDS[:3])}..."
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
                    "description": "要执行的命令",
                },
                "reason": {
                    "type": "string",
                    "description": "为什么要执行这个命令",
                },
            },
            "required": ["command", "reason"],
        }

    def is_safe(self, command: str) -> bool:
        """Check if a command is in the safe list."""
        cmd = command.strip()
        return any(cmd.startswith(safe) for safe in SAFE_COMMANDS)

    def is_allowed(self, command: str) -> bool:
        """Check if a command is allowed at all."""
        cmd = command.strip()
        if self.is_safe(cmd):
            return True
        return any(cmd.startswith(allowed) for allowed in CONFIRM_COMMANDS)

    def execute(self, command: str = "", reason: str = "", **kwargs: Any) -> str:
        if not command:
            return json.dumps({"error": "未提供命令"}, ensure_ascii=False)

        if not self.is_allowed(command):
            return json.dumps({
                "error": f"命令不在白名单中: {command}",
                "allowed": SAFE_COMMANDS + CONFIRM_COMMANDS,
            }, ensure_ascii=False)

        logger.info(f"Executing: {command} (reason: {reason})")

        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=60,
            )
            return json.dumps({
                "command": command,
                "reason": reason,
                "exit_code": result.returncode,
                "stdout": result.stdout[-2000:] if result.stdout else "",
                "stderr": result.stderr[-1000:] if result.stderr else "",
                "success": result.returncode == 0,
            }, ensure_ascii=False, indent=2)
        except subprocess.TimeoutExpired:
            return json.dumps({
                "command": command,
                "error": "命令执行超时（60秒）",
            }, ensure_ascii=False)
        except Exception as e:
            return json.dumps({
                "command": command,
                "error": str(e),
            }, ensure_ascii=False)
