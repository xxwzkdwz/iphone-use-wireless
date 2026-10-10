"""Exercise the real MCP protocol in a fresh process with no device configured."""
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def test_real_stdio_handshake_list_and_readonly_status(tmp_path):
    env = {key: os.environ[key] for key in ("HOME", "PATH", "TMPDIR") if key in os.environ}
    env["VICO_IPHONE_HOME"] = str(tmp_path)
    parameters = StdioServerParameters(command=sys.executable, args=["-m", "vico_iphone.server"], env=env)
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as session:
            hello = await session.initialize()
            assert hello.serverInfo.name == "iphone-use"
            tools = await session.list_tools()
            assert {tool.name for tool in tools.tools} == {"iphone_status", "iphone_setup", "iphone_session", "iphone_observe", "iphone_act", "iphone_launch"}
            status = await session.call_tool("iphone_status", {})
            assert not status.isError, status
            value = json.loads(status.content[0].text)
            assert value["enabled"] is False and value["connected"] is False
            invalid = await session.call_tool("iphone_act", {"action": "delete_everything"})
            assert invalid.isError
    assert not (tmp_path / "session.json").exists()
