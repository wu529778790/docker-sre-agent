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
    "find /host -type f -size +100M -printf '%s %p\\n'",
    "du -sh /host/var/lib/docker/*",
    "du -sh /host/opt/*",
    "du -sh /host/tmp/*",
    "du -sh /host/var/log/*",
    "docker system df",
}

HOST_CONTAINER_LIMITS = {
    "mem_limit": "256m",
    "cpu_quota": 50000,
    "pids_limit": 50,
    "network_disabled": True,
}


class HostExecTool(Tool):
    @property
    def name(self) -> str:
        return "host_exec"

    @property
    def description(self) -> str:
        return "在宿主机上执行命令（通过临时只读容器，有资源限制）"

    @property
    def is_destructive(self) -> bool:
        return False

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "要在宿主机上执行的命令（只读）"},
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
            try:
                client.images.get("alpine:latest")
            except Exception:
                logger.info("Pulling alpine:latest...")
                client.images.pull("alpine:latest")

            result = client.containers.run(
                "alpine:latest",
                command=["sh", "-c", cmd],
                volumes={"/": {"bind": "/host", "mode": "ro"}},
                remove=True,
                detach=False,
                **HOST_CONTAINER_LIMITS,
            )
            output = result.decode("utf-8", errors="replace") if isinstance(result, bytes) else result
            return json.dumps({
                "command": cmd, "success": True, "output": output[:5000],
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

    def _run_host_cmd(self, client, cmd: str, timeout: int = 30) -> str:
        try:
            result = client.containers.run(
                "alpine:latest",
                command=["sh", "-c", cmd],
                volumes={"/": {"bind": "/host", "mode": "ro"}},
                remove=True, detach=False, timeout=timeout,
                **HOST_CONTAINER_LIMITS,
            )
            return result.decode("utf-8", errors="replace") if isinstance(result, bytes) else result
        except Exception as e:
            return f"Error: {e}"

    def execute(self, **kwargs: Any) -> str:
        try:
            client = get_client()
            try:
                client.images.get("alpine:latest")
            except Exception:
                client.images.pull("alpine:latest")

            df_result = self._run_host_cmd(client, "df -h /", timeout=10)
            du_result = self._run_host_cmd(client, "du -sh /host/*/ 2>/dev/null | sort -rh | head -15", timeout=30)
            docker_result = self._run_host_cmd(client, "du -sh /host/var/lib/docker/*/ 2>/dev/null | sort -rh", timeout=30)

            return json.dumps({
                "disk_overview": df_result.strip(),
                "top_directories": du_result.strip(),
                "docker_usage": docker_result.strip(),
            }, ensure_ascii=False, indent=2)
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)
