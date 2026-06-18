"""Disk and resource scanning tools."""

from __future__ import annotations

import json
import logging
import os
import subprocess
from typing import Any

from docker_sre_agent.docker_client import get_client
from docker_sre_agent.tools.base import Tool

logger = logging.getLogger(__name__)


def _format_size(size_bytes: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f}{unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f}PB"


class ScanDiskTool(Tool):
    """Scan disk for large files and old logs."""

    @property
    def name(self) -> str:
        return "scan_disk"

    @property
    def description(self) -> str:
        return "扫描磁盘：大文件、旧日志、包缓存。返回占用空间最大的前 N 项"

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "top_n": {
                    "type": "integer",
                    "default": 20,
                    "description": "返回前 N 个最大文件",
                },
            },
        }

    def execute(self, top_n: int = 20, **kwargs: Any) -> str:
        result: dict[str, Any] = {}

        # Disk usage overview
        try:
            df = subprocess.run(
                ["df", "-h", "/"],
                capture_output=True, text=True, timeout=10,
            )
            result["disk_overview"] = df.stdout.strip()
        except Exception as e:
            result["disk_overview"] = f"无法获取: {e}"

        # Top large files — scan common directories instead of /
        try:
            scan_dirs = [d for d in ["/var", "/tmp", "/opt", "/root", "/home"] if os.path.isdir(d)]
            find_result = subprocess.run(
                ["find"] + scan_dirs + ["-xdev", "-type", "f", "-size", "+50M",
                 "-printf", "%s %p\n"],
                capture_output=True, text=True, timeout=60,
            )
            files = []
            for line in find_result.stdout.strip().split("\n"):
                if not line or not line[0].isdigit():
                    continue
                parts = line.split(" ", 1)
                if len(parts) == 2:
                    size_bytes = int(parts[0])
                    files.append({
                        "size_bytes": size_bytes,
                        "size": _format_size(size_bytes),
                        "path": parts[1],
                    })
            files.sort(key=lambda f: f["size_bytes"], reverse=True)
            # Remove size_bytes from output
            for f in files:
                f.pop("size_bytes")
            result["large_files"] = files[:top_n]
        except subprocess.TimeoutExpired:
            result["large_files"] = "扫描超时"
        except Exception as e:
            result["large_files"] = f"扫描失败: {e}"

        # Log directories
        try:
            log_sizes = []
            for log_dir in ["/var/log", "/tmp"]:
                if os.path.isdir(log_dir):
                    du = subprocess.run(
                        ["du", "-sh", log_dir],
                        capture_output=True, text=True, timeout=10,
                    )
                    if du.returncode == 0:
                        log_sizes.append(du.stdout.strip())
            result["log_directories"] = log_sizes
        except Exception:
            result["log_directories"] = "无法获取"

        # Package manager cache
        try:
            apt_cache = subprocess.run(
                ["du", "-sh", "/var/cache/apt"],
                capture_output=True, text=True, timeout=10,
            )
            if apt_cache.returncode == 0:
                result["apt_cache"] = apt_cache.stdout.strip()
        except Exception:
            pass

        return json.dumps(result, ensure_ascii=False, indent=2)


class ScanResourcesTool(Tool):
    """Scan running container resource usage."""

    @property
    def name(self) -> str:
        return "scan_resources"

    @property
    def description(self) -> str:
        return "获取所有运行中容器的 CPU、内存占用排行"

    @property
    def input_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    def execute(self, **kwargs: Any) -> str:
        try:
            client = get_client()
        except Exception:
            return json.dumps({"error": "无法连接 Docker daemon"}, ensure_ascii=False)

        try:
            containers = client.containers.list(filters={"status": "running"})
        except Exception as e:
            return json.dumps({"error": f"获取容器列表失败: {e}"}, ensure_ascii=False)

        resources = []
        for c in containers:
            try:
                stats = c.stats(stream=False)

                # CPU
                cpu_delta = stats["cpu_stats"]["cpu_usage"]["total_usage"] - \
                            stats["precpu_stats"]["cpu_usage"]["total_usage"]
                system_delta = stats["cpu_stats"]["system_cpu_usage"] - \
                               stats["precpu_stats"]["system_cpu_usage"]
                num_cpus = stats["cpu_stats"]["online_cpus"]
                cpu_percent = (cpu_delta / system_delta * num_cpus * 100.0) if system_delta > 0 else 0.0

                # Memory
                mem_usage = stats["memory_stats"].get("usage", 0)
                mem_limit = stats["memory_stats"].get("limit", 0)
                mem_percent = (mem_usage / mem_limit * 100.0) if mem_limit > 0 else 0.0

                resources.append({
                    "name": c.name,
                    "cpu_percent": round(cpu_percent, 1),
                    "memory_mb": round(mem_usage / (1024 * 1024), 1),
                    "memory_percent": round(mem_percent, 1),
                })
            except Exception:
                resources.append({"name": c.name, "error": "获取 stats 失败"})

        # Sort by memory usage
        resources.sort(key=lambda r: r.get("memory_percent", 0), reverse=True)

        return json.dumps({
            "containers": resources,
            "total_running": len(containers),
        }, ensure_ascii=False, indent=2)
