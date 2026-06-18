"""Host filesystem tools — via temporary containers."""

from __future__ import annotations

import json
import logging
from typing import Any

from server_sre_agent.docker_client import get_client
from server_sre_agent.tools.base import Tool

logger = logging.getLogger(__name__)

ALLOWED_HOST_COMMANDS = {
    "du -sh /*",
    "df -h",
    "ls -la /host/",
    "find /host -type f -size +100M -printf '%s %p\n'",
    "du -sh /host/var/lib/docker/*",
    "du -sh /host/opt/*",
    "du -sh /host/tmp/*",
    "du -sh /host/var/log/*",
    "docker system df",
}


class HostExecTool(Tool):
    @property
    def name(self) -> str:
        return "host_exec"

    @property
    def description(self) -> str:
        return "在宿主机上执行命令（通过临时只读容器）"

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
            df_result = client.containers.run(
                "alpine:latest",
                command=["sh", "-c", "df -h /"],
                volumes={"/": {"bind": "/host", "mode": "ro"}},
                remove=True, detach=False, timeout=10,
            ).decode("utf-8", errors="replace")

            du_result = client.containers.run(
                "alpine:latest",
                command=["sh", "-c", "du -sh /host/*/ 2>/dev/null | sort -rh | head -15"],
                volumes={"/": {"bind": "/host", "mode": "ro"}},
                remove=True, detach=False, timeout=30,
            ).decode("utf-8", errors="replace")

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
