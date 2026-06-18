"""Log fetching tool using Docker Python SDK."""

from __future__ import annotations

import json
import logging
from typing import Any

from docker_sre_agent.docker_client import get_client
from docker_sre_agent.tools.base import Tool

logger = logging.getLogger(__name__)


class FetchLogsTool(Tool):
    """Fetch container logs with filtering."""

    @property
    def name(self) -> str:
        return "fetch_logs"

    @property
    def description(self) -> str:
        return "获取容器日志，支持 tail 行数、grep 关键词过滤、since 时间范围"

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "容器名称",
                },
                "tail": {
                    "type": "integer",
                    "default": 100,
                    "description": "获取最近多少行日志",
                },
                "grep": {
                    "type": "string",
                    "description": "关键词过滤（不区分大小写）",
                },
                "since": {
                    "type": "string",
                    "description": "时间范围，如 30m、1h、24h",
                },
            },
            "required": ["name"],
        }

    def execute(self, name: str = "", tail: int = 100, grep: str = "", since: str = "", **kwargs: Any) -> str:
        if not name:
            return json.dumps({"error": "未提供容器名称"}, ensure_ascii=False)

        try:
            client = get_client()
            container = client.containers.get(name)
        except Exception as e:
            return json.dumps({"error": f"获取容器失败: {e}"}, ensure_ascii=False)

        try:
            logs = container.logs(
                tail=tail,
                timestamps=True,
                since=since if since else None,
            ).decode("utf-8", errors="replace")
        except Exception as e:
            return json.dumps({"error": f"获取日志失败: {e}"}, ensure_ascii=False)

        lines = logs.strip().split("\n") if logs.strip() else []
        total_lines = len(lines)

        # Apply grep filter
        if grep:
            lines = [l for l in lines if grep.lower() in l.lower()]

        # Limit output
        max_display = 200
        truncated = len(lines) > max_display
        display_lines = lines[-max_display:]

        return json.dumps({
            "container": name,
            "total_lines": total_lines,
            "matched_lines": len(lines),
            "grep": grep or None,
            "since": since or None,
            "truncated": truncated,
            "lines": display_lines,
        }, ensure_ascii=False, indent=2)
