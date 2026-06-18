"""Tools for the Docker SRE Agent."""

from docker_sre_agent.tools.base import Tool
from docker_sre_agent.tools.docker import ScanDockerTool, InspectContainerTool
from docker_sre_agent.tools.disk import ScanDiskTool, ScanResourcesTool
from docker_sre_agent.tools.command import RunCommandTool
from docker_sre_agent.tools.logs import FetchLogsTool

ALL_TOOLS: list[Tool] = [
    ScanDockerTool(),
    InspectContainerTool(),
    ScanDiskTool(),
    ScanResourcesTool(),
    RunCommandTool(),
    FetchLogsTool(),
]

__all__ = ["ALL_TOOLS", "Tool"]
