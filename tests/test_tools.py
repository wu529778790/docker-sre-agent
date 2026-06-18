"""Basic tool tests."""

import json
from server_sre_agent.tools.docker import DockerInfoTool, ContainerListTool, DockerCleanTool
from server_sre_agent.tools.host import HostExecTool, HostDiskScanTool
from server_sre_agent.tools.base import Tool


def test_tool_base():
    """Verify Tool base class works."""
    assert hasattr(Tool, 'name')
    assert hasattr(Tool, 'description')
    assert hasattr(Tool, 'input_schema')
    assert hasattr(Tool, 'execute')
    assert hasattr(Tool, 'to_schema')


def test_docker_info_tool_schema():
    tool = DockerInfoTool()
    schema = tool.to_schema()
    assert schema["name"] == "docker_info"
    assert "input_schema" in schema


def test_container_list_tool_schema():
    tool = ContainerListTool()
    schema = tool.to_schema()
    assert schema["name"] == "container_list"


def test_docker_clean_tool_is_destructive():
    tool = DockerCleanTool()
    assert tool.is_destructive is True


def test_host_exec_tool_is_not_destructive():
    tool = HostExecTool()
    assert tool.is_destructive is False


def test_host_exec_tool_rejects_unknown_command():
    tool = HostExecTool()
    result = tool.execute(command="rm -rf /")
    data = json.loads(result)
    assert "error" in data


def test_host_disk_scan_tool_schema():
    tool = HostDiskScanTool()
    schema = tool.to_schema()
    assert schema["name"] == "host_disk_scan"
