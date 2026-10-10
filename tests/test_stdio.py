"""Exercise the real MCP protocol in a fresh process with no device configured."""
import json
import os
import sys

import pytest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


@pytest.mark.parametrize('backend', [None, 'appium'])
async def test_real_stdio_handshake_list_and_readonly_status(tmp_path, backend):
    env = {key: os.environ[key] for key in ("HOME", "PATH", "TMPDIR") if key in os.environ}
    env["VICO_IPHONE_HOME"] = str(tmp_path)
    if backend:
        env["IPHONE_USE_BACKEND"] = backend
    parameters = StdioServerParameters(command=sys.executable, args=["-m", "vico_iphone.server"], env=env)
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as session:
            hello = await session.initialize()
            assert hello.serverInfo.name == ("iphone-use" if backend == "appium" else "iphone-use-wireless")
            tools = await session.list_tools()
            if backend is None:
                assert "no Appium Server" in hello.instructions
                assert "experimental" not in hello.instructions
                assert all("direct-WDA" in tool.description for tool in tools.tools)
                assert all("experiment" not in tool.description for tool in tools.tools)
            assert {tool.name for tool in tools.tools} == {"iphone_status", "iphone_setup", "iphone_session", "iphone_observe", "iphone_act", "iphone_launch"}
            status = await session.call_tool("iphone_status", {})
            assert not status.isError, status
            value = json.loads(status.content[0].text)
            assert value["enabled"] is False and value["connected"] is False
            if backend is None:
                assert value["backend"] == "direct-wda"
                assert value["bridge"]["port"] == 18401
            invalid = await session.call_tool("iphone_act", {"action": "delete_everything"})
            assert invalid.isError
    assert not (tmp_path / "session.json").exists()
