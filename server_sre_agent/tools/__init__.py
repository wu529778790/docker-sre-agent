"""Tools for the Server SRE Agent."""

from server_sre_agent.tools.base import Tool
from server_sre_agent.tools.docker import (
    DockerInfoTool, DockerCleanTool, ContainerListTool, ContainerRestartTool,
)
from server_sre_agent.tools.host import HostExecTool, HostDiskScanTool

ALL_TOOLS: list[Tool] = [
    DockerInfoTool(),
    DockerCleanTool(),
    ContainerListTool(),
    ContainerRestartTool(),
    HostExecTool(),
    HostDiskScanTool(),
]

__all__ = ["ALL_TOOLS", "Tool"]
