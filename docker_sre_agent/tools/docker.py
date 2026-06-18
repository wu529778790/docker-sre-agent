"""Docker scanning tools."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

import docker

from docker_sre_agent.tools.base import Tool

logger = logging.getLogger(__name__)


def _format_size(size_bytes: int) -> str:
    """Format bytes to human readable."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f}{unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f}PB"


def _time_ago(timestamp: str) -> str:
    """Format datetime string to 'X days ago'."""
    try:
        dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        delta = now - dt
        days = delta.days
        if days == 0:
            hours = delta.seconds // 3600
            return f"{hours}小时前"
        return f"{days}天前"
    except Exception:
        return "未知"


class ScanDockerTool(Tool):
    """Scan Docker environment for reclaimable resources."""

    @property
    def name(self) -> str:
        return "scan_docker"

    @property
    def description(self) -> str:
        return "扫描 Docker 环境：停止的容器、悬空镜像、未使用卷、构建缓存。返回可回收空间汇总。"

    @property
    def input_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    def execute(self, **kwargs: Any) -> str:
        try:
            client = docker.from_env()
        except docker.errors.DockerException:
            return json.dumps({"error": "无法连接 Docker daemon"}, ensure_ascii=False)

        result: dict[str, Any] = {}

        # Stopped containers
        try:
            stopped = client.containers.list(filters={"status": "exited"})
            result["stopped_containers"] = [
                {
                    "name": c.name,
                    "image": c.image.tags[0] if c.image.tags else str(c.image.id)[:12],
                    "status": c.status,
                    "finished": _time_ago(c.attrs["State"]["FinishedAt"]),
                }
                for c in stopped
            ]
        except Exception as e:
            result["stopped_containers"] = [{"error": str(e)}]

        # Dangling images
        try:
            dangling = client.images.list(filters={"dangling": True})
            total_image_size = sum(img.attrs.get("Size", 0) for img in dangling)
            result["dangling_images"] = [
                {
                    "id": img.id[:12],
                    "size": _format_size(img.attrs.get("Size", 0)),
                    "created": _time_ago(img.attrs["Created"]),
                }
                for img in dangling
            ]
            result["dangling_images_total"] = _format_size(total_image_size)
        except Exception as e:
            result["dangling_images"] = [{"error": str(e)}]

        # Unused volumes
        try:
            volumes = client.volumes.list(filters={"dangling": True})
            result["unused_volumes"] = [
                {
                    "name": v.name[:20],
                    "created": _time_ago(v.attrs["CreatedAt"]),
                }
                for v in volumes
            ]
        except Exception as e:
            result["unused_volumes"] = [{"error": str(e)}]

        # Build cache
        try:
            import subprocess
            cache_result = subprocess.run(
                ["docker", "system", "df", "-v", "--format", "{{.Reclaimable}}\t{{.Size}}"],
                capture_output=True, text=True, timeout=10,
            )
            result["disk_usage"] = cache_result.stdout.strip()
        except Exception:
            result["disk_usage"] = "无法获取"

        # Summary
        stopped_count = len(result.get("stopped_containers", []))
        dangling_count = len(result.get("dangling_images", []))
        volume_count = len(result.get("unused_volumes", []))
        result["summary"] = (
            f"停止的容器: {stopped_count}个, "
            f"悬空镜像: {dangling_count}个({result.get('dangling_images_total', '?')}), "
            f"未使用卷: {volume_count}个"
        )

        return json.dumps(result, ensure_ascii=False, indent=2)


class InspectContainerTool(Tool):
    """Inspect a specific container for details."""

    @property
    def name(self) -> str:
        return "inspect_container"

    @property
    def description(self) -> str:
        return "查看指定容器的详细信息：配置、挂载、网络、资源限制、日志摘要"

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
            client = docker.from_env()
            container = client.containers.get(name)
        except docker.errors.NotFound:
            return json.dumps({"error": f"容器 '{name}' 不存在"}, ensure_ascii=False)
        except docker.errors.DockerException:
            return json.dumps({"error": "无法连接 Docker daemon"}, ensure_ascii=False)

        attrs = container.attrs
        state = attrs.get("State", {})
        config = attrs.get("Config", {})
        host_config = attrs.get("HostConfig", {})

        result = {
            "name": container.name,
            "image": container.image.tags[0] if container.image.tags else str(container.image.id)[:12],
            "status": state.get("Status"),
            "health": state.get("Health", {}).get("Status"),
            "started_at": state.get("StartedAt"),
            "restart_count": state.get("RestartCount", 0),
            "oom_killed": state.get("OOMKilled", False),
            "env_count": len(config.get("Env", [])),
            "mounts": [
                {
                    "source": m.get("Source", "?"),
                    "destination": m.get("Destination"),
                    "mode": m.get("Mode"),
                    "rw": m.get("RW"),
                }
                for m in attrs.get("Mounts", [])
            ],
            "port_bindings": list(host_config.get("PortBindings", {}).keys()) or "无",
            "memory_limit": _format_size(host_config.get("Memory", 0)) if host_config.get("Memory") else "无限制",
            "cpu_shares": host_config.get("CpuShares"),
        }

        # Get last 20 lines of logs
        try:
            logs = container.logs(tail=20, timestamps=True).decode("utf-8", errors="replace")
            result["recent_logs"] = logs[-500:]  # last 500 chars
        except Exception:
            result["recent_logs"] = "无法获取日志"

        return json.dumps(result, ensure_ascii=False, indent=2)
