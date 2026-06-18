"""Docker management tools."""

from __future__ import annotations

import json
import logging
import subprocess
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
